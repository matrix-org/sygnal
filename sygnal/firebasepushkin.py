# -*- coding: utf-8 -*-
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
from typing import Dict, Optional

import attr
import asyncio
from opentracing import logs, tags
from firebase_admin import credentials, initialize_app, messaging
from firebase_admin.exceptions import *
from prometheus_client import Histogram
from twisted.internet.defer import Deferred
from twisted.python.threadpool import ThreadPool
from sygnal.utils import twisted_sleep, NotificationLoggerAdapter

from .exceptions import PushkinSetupException, TemporaryNotificationDispatchException, NotificationDispatchException
from .notifications import Pushkin

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_fcm_request_time", "Time taken to send HTTP request"
)

NOTIFICATION_DATA_INCLUDED = [
    'type',
    'room_id',
    'event_id',
    'sender_display_name',
]

logger = logging.getLogger(__name__)


@attr.s
class FirebaseConfig(object):
    credentials = attr.ib()
    max_connections = attr.ib(default=20)
    message_types = attr.ib(default=attr.Factory(dict), type=Dict[str, str])
    event_handlers = attr.ib(default=attr.Factory(dict), type=Dict[str, str])


class FirebasePushkin(Pushkin):
    MAX_TRIES = 1
    RETRY_DELAY_BASE = 10
    MAX_BYTES_PER_FIELD = 1024
    DEFAULT_MAX_CONNECTIONS = 20

    def __init__(self, name, sygnal, config):
        super(FirebasePushkin, self).__init__(name, sygnal, config)

        self.db = sygnal.database
        self.reactor = sygnal.reactor
        self.config = FirebaseConfig(
            **{x: y for x, y in self.cfg.items() if x != "type"}
        )
        logger.debug("self.config %s", self.config)

        credential_path = self.config.credentials
        if not credential_path:
            raise PushkinSetupException("No Credential path set in config")

        cred = credentials.Certificate(credential_path)
        logger.debug("cred %s", cred)

        self._pool = ThreadPool(maxthreads=self.config.max_connections)
        self._pool.start()

        self._app = initialize_app(cred, name="app")
        logger.debug("self._app %s", self._app)

    def _decode_notification_body(self, message):
        notification_body = message.get("title", "").strip() + " "
        logger.debug("notification_body now %s", notification_body)
        if "images" in message:
            notification_body += self.config.message_types.get("m.image")
            logger.debug("notification_body now %s", notification_body)
        elif "videos" in message:
            notification_body += self.config.message_types.get("m.video")
            logger.debug("notification_body now %s", notification_body)
        elif "title" not in message and "message" in message:
            notification_body += message["message"].strip()
            logger.debug("notification_body now %s", notification_body)
        return notification_body

    def _map_notification_body(self, n):
        if n.type == "m.room.message" and n.content["msgtype"] == "m.text":
            decoded_message = decode_complex_message(n.content["body"])
            if decoded_message:
                return self._decode_notification_body(decoded_message)
        if n.room_name is None:
            return n.content["body"]
        else:
            return n.sender_display_name + ": " + n.content["body"]

    @staticmethod
    def _map_counts_unread(n):
        return n.counts.unread or 0

    async def _dispatch_message(self, n, device, span, log):
        log.info("dispatching message")
        notification_title = n.room_name or n.sender_display_name
        notification_body = self._map_notification_body(n).strip()
        notification = messaging.Notification(title=notification_title, body=notification_body)

        android = messaging.AndroidConfig(priority=self._map_android_priority(n),
                                          notification=messaging.AndroidNotification(
                                              click_action="FLUTTER_NOTIFICATION_CLICK",
                                              tag=n.room_id))
        apns = messaging.APNSConfig(
            headers={"apns-priority": self._map_ios_priority(n)},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(badge=self._map_counts_unread(n), thread_id=n.room_id)
            )
        )

        request = messaging.Message(
            notification=notification,
            data=build_data_for_notification(n),
            android=android,
            apns=apns,
            token=device.pushkey,
        )
        return await self._dispatch(request, span, log)

    async def _dispatch_event(self, n, device, span, log):
        logger.info("dispatching event")
        android = messaging.AndroidConfig(priority=self._map_android_priority(n))

        apns = messaging.APNSConfig(
            headers={"apns-priority": self._map_ios_priority(n)},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(badge=self._map_counts_unread(n), thread_id=n.room_id)
            )
        )

        request = messaging.Message(
            data=build_data_for_notification(n),
            android=android,
            apns=apns,
            token=device.pushkey
        )
        return await self._dispatch(request, span, log)

    async def _dispatch(self, request, span, log):
        if request.data is None and request.notification:
            span.log_kv({logs.EVENT: "firebase_no_payload"})

        log.info(f"Sending Firebase message")
        try:
            with SEND_TIME_HISTOGRAM.time():
                response = await self._send_notification(request)
        except FirebaseError as e:
            span.set_tag("firebase_reason", e.cause)
            log.info(f"{e}")
            if e.code is UNAVAILABLE:
                error = f"FirebaseError: {e.code} {e.cause}"
                raise TemporaryNotificationDispatchException(error)
            else:
                error = f"FirebaseError: {e.code} {e.cause}"
                raise NotificationDispatchException(error)
        except ValueError as e:
            log.info(f"{e}")
            span.set_tag("firebase_reason", e)
            error = f"ValueError: {e}"
            raise NotificationDispatchException(error)

        log.info("Success sending Firebase message")
        span.set_tag("firebase_id", response)
        return []

    def _map_event_dispatch_handler(self, n, log):
        event_handlers = self.config.event_handlers
        if not event_handlers:
            if n.type != "m.room.message" or n.content["msgtype"] not in self.config.message_types:
                log.info("event handlers not specified, no valid type or message type found")
                return None
            else:
                log.info("event handlers not specified, using message handler")
                return self._dispatch_message
        else:
            handler = event_handlers.get(n.type, None)
            if handler == "message":
                log.info("using message handler")
                return self._dispatch_message
            elif handler == "event":
                log.info("using event handler")
                return self._dispatch_event
            else:
                return None

    async def dispatch_notification(self, n, device, context):
        log = NotificationLoggerAdapter(logger, {"request_id": context.request_id})

        dispatch_handler = self._map_event_dispatch_handler(n, log)
        if dispatch_handler is None:
            return []  # skipped

        span_tags = {}
        with self.sygnal.tracer.start_span(
                "firebase_dispatch", tags=span_tags, child_of=context.opentracing_span
        ) as span_parent:

            for retry_number in range(self.MAX_TRIES):
                try:
                    with self.sygnal.tracer.start_span(
                            "firebase_dispatch_try", tags=span_tags, child_of=span_parent
                    ) as span:
                        return await dispatch_handler(n, device, span, log)
                except TemporaryNotificationDispatchException as ex:
                    retry_delay = self.RETRY_DELAY_BASE * (2 ** retry_number)
                    if ex.custom_retry_delay is not None:
                        retry_delay = ex.custom_retry_delay

                    logger.warning(
                        "Temporary failure, will retry in %d seconds",
                        retry_delay, exc_info=True,
                    )
                    span_parent.log_kv(
                        {"event": "temporary_fail", "retrying_in": retry_delay}
                    )
                    if retry_number == self.MAX_TRIES - 1:
                        raise NotificationDispatchException(
                            "Retried too many times."
                        ) from ex
                    else:
                        await twisted_sleep(
                            retry_delay, twisted_reactor=self.sygnal.reactor
                        )

    async def _send_notification(self, request):
        return messaging.send(request, app=self._app)

    @staticmethod
    def _map_android_priority(n):
        return "normal" if n.prio == "low" else "high"

    @staticmethod
    def _map_ios_priority(n):
        return "10" if n.prio == 10 else "5"


def build_data_for_notification(n):
    data = {}
    for field in NOTIFICATION_DATA_INCLUDED:
        if hasattr(n, field) and getattr(n, field) is not None:
            data[field] = getattr(n, field)
    return data


def decode_complex_message(message: str) -> Optional[Dict]:
    """
    Tries to parse a message as json

    :param message: json string of notification m.text message
    :return: dict if successful and None if parsing fails or message is not valid
    """
    try:
        decoded_message = json.loads(message)
        if is_valid_matrix_complex_message(decoded_message):
            return decoded_message
    except json.JSONDecodeError:
        pass

    return None


def is_valid_matrix_complex_message(
        decoded_message: dict, message_keys=("title", "message", "images", "videos")
):
    """
    Checks if decoded message contains one of the predefined fields
    of a MatrixComplexMessage

    :param decoded_message: json decoded m.text message
    :param message_keys: keys to check for
    :return:
    """
    if not isinstance(decoded_message, dict):
        return False

    # Return whether any required key is in the decoded message
    return not decoded_message.keys().isdisjoint(message_keys)
