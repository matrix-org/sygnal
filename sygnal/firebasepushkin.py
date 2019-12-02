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

import logging
from typing import Dict
from functools import partial

import attr
from opentracing import logs
from firebase_admin import (
    credentials,
    initialize_app,
    messaging,
    exceptions as firebase_exceptions,
)
from prometheus_client import Histogram
from sygnal.utils import twisted_sleep, NotificationLoggerAdapter

from .exceptions import (
    PushkinSetupException,
    TemporaryNotificationDispatchException,
    NotificationDispatchException,
)
from .notifications import Pushkin

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_fcm_request_time", "Time taken to send HTTP request"
)

NOTIFICATION_DATA_INCLUDED = [
    "type",
    "room_id",
    "event_id",
    "sender_display_name",
]

DEFAULT_HANDLER = {"m.room.message": "message", "m.call.invite": "voip"}

logger = logging.getLogger(__name__)


@attr.s
class FirebaseConfig(object):
    credentials = attr.ib()
    max_connections = attr.ib(default=20)
    message_types = attr.ib(default=attr.Factory(dict), type=Dict[str, str])
    event_handlers = attr.ib(default=DEFAULT_HANDLER, type=Dict[str, str])
    android_click_action = attr.ib(default=None, type=str)


class FirebasePushkin(Pushkin):

    MAX_TRIES = 3
    RETRY_DELAY_BASE = 10

    def __init__(self, name, sygnal, config):
        super(FirebasePushkin, self).__init__(name, sygnal, config)

        self.db = sygnal.database
        self.reactor = sygnal.reactor
        self.config = FirebaseConfig(
            **{x: y for x, y in self.cfg.items() if x != "type"}
        )

        self._app = initialize_app(self._load_credentials(), name="app")

    def _load_credentials(self):
        credential_path = self.config.credentials
        if not credential_path:
            raise PushkinSetupException("No Credential path set in config")

        return credentials.Certificate(credential_path)

    async def _dispatch_message(self, n, data, device, span, log):
        """
        Construct Firebase message and dispatch to device.

        Args:
            n: The notification for the user and device.
            data: Optional data fields, see `firebase_admin.messaging.Message`.
        """
        notification_title = n.room_name or n.sender_display_name
        notification_body = self._message_body_from_notification(
            n, self.config.message_types
        ).strip()
        notification = messaging.Notification(
            title=notification_title, body=notification_body
        )

        android = messaging.AndroidConfig(
            priority=self._map_android_priority(n),
            notification=messaging.AndroidNotification(
                click_action=self.config.android_click_action, tag=n.room_id,
            ),
        )
        apns = messaging.APNSConfig(
            headers={"apns-priority": self._map_ios_priority(n)},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(badge=self._map_counts_unread(n), thread_id=n.room_id)
            ),
        )

        request = messaging.Message(
            notification=notification,
            data=data,
            android=android,
            apns=apns,
            token=device.pushkey,
        )
        return await self._dispatch(request, device, span, log)

    async def _dispatch_data_only(self, n, data, device, span, log):
        logger.info("Dispatching data-only event")
        android = messaging.AndroidConfig(priority=self._map_android_priority(n))

        apns = messaging.APNSConfig(
            headers={"apns-priority": self._map_ios_priority(n)},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(badge=self._map_counts_unread(n), thread_id=n.room_id)
            ),
        )

        request = messaging.Message(
            data=data, android=android, apns=apns, token=device.pushkey
        )
        return await self._dispatch(request, device, span, log)

    async def _dispatch(self, request, device, span, log):
        if request.data is None and request.notification:
            span.log_kv({logs.EVENT: "firebase_no_payload"})

        try:
            with SEND_TIME_HISTOGRAM.time():
                response = self._perform_firebase_send(request)
        except firebase_exceptions.FirebaseError as e:
            span.set_tag("firebase_reason", e.cause)
            if e.code is firebase_exceptions.NOT_FOUND:
                # Token invalid
                return [device.pushkey]
            elif e.code in (
                firebase_exceptions.UNAVAILABLE,
                firebase_exceptions.INTERNAL,
            ):
                error = f"FirebaseError: {e.code} {e.cause}"
                raise TemporaryNotificationDispatchException(error)
            else:
                error = f"FirebaseError: {e.code} {e.cause}"
                raise NotificationDispatchException(error)
        except ValueError as e:
            span.set_tag("firebase_reason", e)
            error = f"ValueError: {e}"
            raise NotificationDispatchException(error)

        span.set_tag("firebase_id", response)
        return []

    def _perform_firebase_send(self, request):
        return messaging.send(request, app=self._app)

    def _map_event_dispatch_handler(self, n):
        """
        Map event types to dispatch handler with custom behavior, e.g. voip contains
        VoIP-related content and the message handler is intended for a visible user
        notification.

        Args:
            n: The notification to dispatch.

        Returns:
            Function to dispatch notification to a device.
        """
        handler = self.config.event_handlers.get(n.type)
        if handler == "message":
            return partial(
                self._dispatch_message,
                n,
                FirebasePushkin._message_data_from_notification(n),
            )
        elif handler == "voip":
            return partial(
                self._dispatch_data_only,
                n,
                FirebasePushkin._voip_data_from_notification(n),
            )
        elif handler == "event":
            return partial(
                self._dispatch_data_only,
                n,
                FirebasePushkin._event_data_from_notification(n),
            )

        return None

    async def dispatch_notification(self, n, device, context):
        log = NotificationLoggerAdapter(logger, {"request_id": context.request_id})

        span_tags = {}
        with self.sygnal.tracer.start_span(
            "firebase_dispatch", tags=span_tags, child_of=context.opentracing_span
        ) as span_parent:

            dispatch_handler = self._map_event_dispatch_handler(n)
            if dispatch_handler is None:
                return []  # skipped

            for retry_number in range(self.MAX_TRIES):
                try:
                    log.debug("Trying")

                    span_tags = {"retry_num": retry_number}

                    with self.sygnal.tracer.start_span(
                        "firebase_dispatch_try", tags=span_tags, child_of=span_parent
                    ) as span:
                        return await dispatch_handler(device, span, log)
                except TemporaryNotificationDispatchException as ex:
                    retry_delay = self.RETRY_DELAY_BASE * (2 ** retry_number)
                    if ex.custom_retry_delay is not None:
                        retry_delay = ex.custom_retry_delay

                    log.warning(
                        "Temporary failure, will retry in %d seconds",
                        retry_delay,
                        exc_info=True,
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

    @staticmethod
    def _map_counts_unread(n):
        return n.counts.unread or 0

    @staticmethod
    def _map_android_priority(n):
        return "normal" if n.prio == "low" else "high"

    @staticmethod
    def _map_ios_priority(n):
        return "10" if n.prio == "high" else "5"

    @staticmethod
    def _message_body_from_notification(n, message_types):
        if n.type != "m.room.message":
            return ""

        from_display = ""
        if n.room_name is not None and n.sender_display_name is not None:
            from_display = n.sender_display_name + ": "

        body = message_types.get(n.content["msgtype"])
        if body:
            return body

        return from_display + n.content["body"]

    @staticmethod
    def _message_data_from_notification(n):
        data = {}
        for field in NOTIFICATION_DATA_INCLUDED:
            value = getattr(n, field, None)
            if value is not None:
                data[field] = value
        return data

    @staticmethod
    def _event_data_from_notification(n):
        data = {}
        if n.room_id:
            data["room_id"] = n.room_id
        if n.event_id:
            data["event_id"] = n.event_id

        if n.counts.unread is not None:
            data["unread_count"] = n.counts.unread
        if n.counts.missed_calls is not None:
            data["missed_calls"] = n.counts.missed_calls

        return data

    @staticmethod
    def _voip_data_from_notification(n):
        data = {}
        if n.room_id:
            data["room_id"] = n.room_id
        if n.event_id:
            data["event_id"] = n.event_id

        if n.type is not None and "m.call" in n.type:
            data["type"] = n.type
            if n.sender_display_name is not None:
                data["sender_display_name"] = n.sender_display_name

            data["is_video_call"] = "false"
            if n.content:
                if "offer" in n.content and "sdp" in n.content["offer"]:
                    sdp = n.content["offer"]["sdp"]
                    if "m=video" in sdp:
                        data["is_video_call"] = "true"
                if "call_id" in n.content:
                    data["call_id"] = n.content["call_id"]

        return data
