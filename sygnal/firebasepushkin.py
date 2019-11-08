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
from firebase_admin import credentials, initialize_app, messaging
from prometheus_client import Histogram
from twisted.internet.defer import Deferred
from twisted.python.threadpool import ThreadPool
from sygnal.utils import twisted_sleep

from .exceptions import PushkinSetupException, TemporaryNotificationDispatchException, NotificationDispatchException
from .notifications import Pushkin

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_fcm_request_time", "Time taken to send HTTP request"
)

log = logging.getLogger(__name__)


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
        log.debug("self.config %s", self.config)

        credential_path = self.config.credentials
        if not credential_path:
            raise PushkinSetupException("No Credential path set in config")

        cred = credentials.Certificate(credential_path)
        log.debug("cred %s", cred)

        self._pool = ThreadPool(maxthreads=self.config.max_connections)
        self._pool.start()

        self._app = initialize_app(cred, name="app")
        log.debug("self._app %s", self._app)

    def _decode_notification_body(self, message):
        notification_body = message.get("title", "").strip() + " "
        log.debug("notification_body now %s", notification_body)
        if "images" in message:
            notification_body += self.config.message_types.get("m.image")
            log.debug("notification_body now %s", notification_body)
        elif "videos" in message:
            notification_body += self.config.message_types.get("m.video")
            log.debug("notification_body now %s", notification_body)
        elif "title" not in message and "message" in message:
            notification_body += message["message"].strip()
            log.debug("notification_body now %s", notification_body)
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

    def _map_request_message(self, n, device):
        notification_title = n.room_name or n.sender_display_name
        notification_body = self._map_notification_body(n).strip()
        notification = messaging.Notification(title=notification_title, body=notification_body)
        data = {
            "title": notification_title,  # this seems redundant
            "body": notification_body,  # this seems redundant
            "room_id": n.room_id
        }
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

        request = messaging.MulticastMessage(
            notification=notification,
            data=data,
            android=android,
            apns=apns,
            tokens=[device.pushkey],
        )
        return request

    @staticmethod
    def _map_request_event(n, device):
        notification_title = getattr(n, "room_name", getattr(n, "sender_display_name"))
        data = {
            "title": notification_title,  # this seems redundant
            "room_id": n["room_id"],
            "event_id": n["event_id"]
        }
        request = messaging.MulticastMessage(
            data=data,
            tokens=[device.pushkey]
        )
        return request

    async def _dispatch_message(self, n, device):
        request = self._map_request_message(n, device)
        return await self._send(request)

    async def _dispatch_event(self, n, device):
        request = self._map_request_event(n, device)
        return await self._send(request)

    def _map_event_dispatch_handler(self, n):
        event_handlers = self.config.event_handlers
        if not event_handlers:
            if n.type != "m.room.message" or n.content["msgtype"] not in self.config.message_types:
                return None
        else:
            handler = event_handlers.get(n.type, None)
            if handler == "message":
                return self._dispatch_message
            elif handler == "event":
                return self._dispatch_event
            else:
                return None

    async def dispatch_notification(self, n, device, context):
        dispatch_handler = self._map_event_dispatch_handler(n)
        if dispatch_handler is None:
            return []  # skipped

        span_tags = {}
        with self.sygnal.tracer.start_span(
                "firebase_dispatch", tags=span_tags, child_of=context.opentracing_span
        ) as span_parent:
            for retry_number in range(self.MAX_TRIES):
                try:
                    log.debug("Trying")
                    return await dispatch_handler(n, device)
                except TemporaryNotificationDispatchException as ex:
                    retry_delay = self.RETRY_DELAY_BASE * (2 ** retry_number)
                    if ex.custom_retry_delay is not None:
                        retry_delay = ex.custom_retry_delay

                    log.warning(
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

    async def _send(self, message):
        d = Deferred()

        def done(success, result):
            self.reactor.callFromThread(d.callback, result)

        with SEND_TIME_HISTOGRAM.time():
            self._pool.callInThreadWithCallback(
                done, messaging.send_multicast, message, app=self._app
            )
            response = await d

        log.debug(
            "Message send success: %s of %s",
            response.success_count,
            response.success_count + response.failure_count,
        )
        failed = []
        return failed

    @staticmethod
    def _map_android_priority(n):
        return "normal" if n.prio == "low" else "high"

    @staticmethod
    def _map_ios_priority(n):
        return "10" if n.prio == 10 else "5"


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
