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
from functools import partial
from typing import Dict

import attr
from firebase_admin import (
    credentials,
    exceptions as firebase_exceptions,
    initialize_app,
    messaging,
)
from opentracing import logs
from prometheus_client import Histogram

from sygnal.utils import NotificationLoggerAdapter, twisted_sleep

from .exceptions import (
    NotificationDispatchException,
    PushkinSetupException,
    TemporaryNotificationDispatchException,
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
    message_types = attr.ib(default=attr.Factory(dict), type=Dict[str, str])
    event_handlers = attr.ib(default=attr.Factory(dict), type=Dict[str, str])
    android_click_action = attr.ib(default=None, type=str)


class FirebasePushkin(Pushkin):

    MAX_TRIES = 3
    RETRY_DELAY_BASE = 10
    MAX_BYTES_PER_FIELD = 1024

    def __init__(self, name, sygnal, config):
        super(FirebasePushkin, self).__init__(name, sygnal, config)

        self.config = FirebaseConfig(
            **{x: y for x, y in self.cfg.items() if x != "type"}
        )

        # Play the default notification sound of the default on receiving a message
        self.notification_sound = messaging.CriticalSound("default")

        self._app = initialize_app(self._load_credentials(), name="app")

    def _load_credentials(self):
        credential_path = self.config.credentials
        if not credential_path:
            raise PushkinSetupException("No Credential path set in config")

        return credentials.Certificate(credential_path)

    async def _dispatch_message(self, n, data, device, span):
        """
        Construct Firebase message and dispatch to device.

        Args:
            n (Notification): The notification for the user and device.
            data (dict[str:obj]): Optional data fields,
                see `firebase_admin.messaging.Message`.
            device (Device): The device to dispatch the notification for.
            span (Span): The span for the dispatch request triggering.

        Returns:
            list[str]: List of unregistered device tokens.
        """
        notification_title, notification_body = self._message_notification_content(
            n, self.config.message_types
        )

        notification = messaging.Notification(
            title=notification_title[0 : self.MAX_BYTES_PER_FIELD],
            body=notification_body[0 : self.MAX_BYTES_PER_FIELD],
        )
        android = messaging.AndroidConfig(
            collapse_key=n.room_id,
            priority=self._map_android_priority(n),
            notification=messaging.AndroidNotification(
                tag=n.event_id,
                click_action=self.config.android_click_action,
                notification_count=self._map_counts_unread(n),
            ),
        )

        apns = messaging.APNSConfig(
            headers={"apns-priority": self._map_ios_priority(n)},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    badge=self._map_counts_unread(n),
                    thread_id=n.room_id,
                    sound=self.notification_sound,
                )
            ),
        )

        request = messaging.Message(
            notification=notification,
            data=data,
            android=android,
            apns=apns,
            token=device.pushkey,
        )
        return await self._dispatch(request, device, span)

    async def _dispatch_data_only(self, n, data, device, span):
        """
        Dispatch handler for data pushes. Used to handle event_id only requests
        and VoIP pushes on Android.

        Args:
            n (Notification): The notification for the user and device.
            data (dict[str:obj]): Optional data fields,
                see `firebase_admin.messaging.Message`.
            device (Device): The device to dispatch the notification for.
            span (Span): The span for the dispatch request triggering.

        Returns:
            list[str]: List of unregistered device tokens.
        """
        logger.info("Dispatching data-only event")
        android = messaging.AndroidConfig(priority=self._map_android_priority(n))

        apns = messaging.APNSConfig(
            headers={"apns-priority": self._map_ios_priority(n)},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    badge=self._map_counts_unread(n),
                    thread_id=n.room_id,
                    sound=self.notification_sound,
                )
            ),
        )

        request = messaging.Message(
            data=data, android=android, apns=apns, token=device.pushkey
        )
        return await self._dispatch(request, device, span)

    async def _dispatch(self, request, device, span):
        """
        Dispatches a notification request (request) for (device) to firebase.

        Args:
            request (Message): The notification request,
                see `firebase_admin.messaging.Message`.
            device (Device): The device to dispatch the notification for.
            span (Span): The span for the dispatch request triggering.

        Returns:
            list[str]: list of device keys which are no longer registered

        Raises:
            NotificationDispatchException: Error during dispatch..
            TemporaryNotificationDispatchException: Server is currently unavailable.
        """
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
        """
        Sends a request to firebase

        Args:
            request (Message): The message to be sent,
                see `firebase_admin.messaging.Message`.
        """
        return messaging.send(request, app=self._app)

    def _map_event_dispatch_handler(self, n):
        """
        Map event types to dispatch handler with custom behavior, e.g. voip contains
        VoIP-related content and the message handler is intended for a visible user
        notification. If no handler is specified for a given event, it defaults to the
        message handler.

        Args:
            n (Notification): The notification to dispatch.

        Returns:
            partial: _dispatch_message or _dispatch_data_only partial with pre-filled
                n (Notification) and data (dict[str:obj]).
        """
        if n.type is None:
            return None

        handler = self.config.event_handlers.get(n.type, "message")
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
        """
        Dispatches a notification using a handler base on the configured parameters and
        information of the incoming event. Implements retry and error handling for the
        dispatching process.

        Args:
            n (Notification): The incoming notification.
            device (Device): The device token to which the notification should be sent.
            context (NotificationContext): The request context.

        Returns:
            list[str]: List of unregistered device tokens.

        Raises:
            NotificationDispatchException: Error during dispatch (retry failed).
        """
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
                    log.debug(f"Trying {retry_number} of {self.MAX_TRIES}")
                    span_tags = {"retry_num": retry_number}

                    with self.sygnal.tracer.start_span(
                        "firebase_dispatch_try", tags=span_tags, child_of=span_parent
                    ) as span:
                        return await dispatch_handler(device, span)
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
        """Returns unread count if included in incoming notification or 0"""
        return n.counts.unread or 0

    @staticmethod
    def _map_android_priority(n):
        """
        Maps the notification priority coming from the homeserver to
        an fcm conform value.

        Args:
            n (Notification): The incoming notification.

        Returns:
            str: Fcm conform priority value
        """
        return "normal" if n.prio == "low" else "high"

    @staticmethod
    def _map_ios_priority(n):
        """
        Maps the notification priority coming from the homeserver to
        an apns conform value.

        Args:
            n (Notification): The incoming notification.

        Returns:
            int: Apns conform priority value.
        """
        return "10" if n.prio == "high" else "5"

    @staticmethod
    def _message_notification_content(n, message_types):
        """
        Generates the title and body of the visible notification given an
        incoming notification [n] and [message_types].

        Args:
            n (Notification): The incoming notification
            message_types (dict[str: str]): Mapping from message types to
                body replacement strings.

        Returns:
            (str, str): The title and body of the visible notification.
        """
        notification_title = n.room_name or n.sender_display_name or ""
        if n.content is None:
            return notification_title, ""

        from_display = ""
        if n.room_name and n.sender_display_name:
            from_display = n.sender_display_name + ": "

        body = ""
        content_msgtype = n.content.get("msgtype")
        if n.type == "m.room.message" and content_msgtype:
            body = message_types.get(content_msgtype, "")

        if body:
            notification_body = from_display + body
        else:
            notification_body = from_display + n.content.get("body", "")

        notification_title = n.room_name or n.sender_display_name or ""

        return notification_title, notification_body

    @staticmethod
    def _message_data_from_notification(n):
        """
        Generates the data payload for an outgoing 'message' type notification
        based on the predefined fields in [NOTIFICATION_DATA_INCLUDED].

        Args:
            n (Notification): The incoming notification for which the data
                should be generated.

        Returns:
            dict[str:obj]: Containing data payload for the outgoing notification.
        """
        data = {}
        for field in NOTIFICATION_DATA_INCLUDED:
            value = getattr(n, field, None)
            if value is not None:
                data[field] = value
        return data

    @staticmethod
    def _event_data_from_notification(n):
        """
        Generates the data payload for an outgoing 'event' type notification
        including minimal information about the event.

        Args:
            n (Notification): The incoming notification for which the data
                should be generated.

        Returns:
            dict[str:obj]: Containing data payload for the outgoing notification.
        """
        data = {}
        if n.room_id is not None:
            data["room_id"] = n.room_id
        if n.event_id is not None:
            data["event_id"] = n.event_id

        if n.counts.unread is not None:
            data["unread_count"] = str(n.counts.unread)
        if n.counts.missed_calls is not None:
            data["missed_calls"] = str(n.counts.missed_calls)

        return data

    @staticmethod
    def _voip_data_from_notification(n):
        """
        Generates the data payload for an outgoing 'voip' type notification
        including voice call specific information.

        Args:
            n (Notification): The incoming notification for which the data
                should be generated.

        Returns:
            dict[str,obj]: Containing data payload for the outgoing notification.
        """
        data = {}
        if n.room_id is not None:
            data["room_id"] = n.room_id
        if n.event_id is not None:
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
