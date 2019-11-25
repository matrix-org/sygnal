# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
# Copyright 2017 Vector Creations Ltd
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
import asyncio
import base64
import logging
import os
from uuid import uuid4

import aioapns
from aioapns import APNs, NotificationRequest, PushType
from opentracing import logs, tags
from prometheus_client import Histogram, Counter
from twisted.internet.defer import Deferred

from sygnal import apnstruncate
from sygnal.exceptions import (
    PushkinSetupException,
    TemporaryNotificationDispatchException,
    NotificationDispatchException,
)
from sygnal.notifications import Pushkin
from sygnal.utils import twisted_sleep, NotificationLoggerAdapter

logger = logging.getLogger(__name__)

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_apns_request_time", "Time taken to send HTTP request to APNS"
)

RESPONSE_STATUS_CODES_COUNTER = Counter(
    "sygnal_apns_status_codes",
    "Number of HTTP response status codes received from APNS",
    labelnames=["pushkin", "code"],
)


class ApnsPushkin(Pushkin):
    """
    Relays notifications to the Apple Push Notification Service.
    """

    # Errors for which the token should be rejected
    TOKEN_ERROR_REASON = "Unregistered"
    TOKEN_ERROR_CODE = 410

    MAX_TRIES = 3
    RETRY_DELAY_BASE = 10

    MAX_FIELD_LENGTH = 1024
    MAX_JSON_BODY_SIZE = 4096
    VOIP_MAX_JSON_BODY_SIZE = 5120

    UNDERSTOOD_CONFIG_FIELDS = {"type", "platform", "certfile", "event_handlers"}

    def __init__(self, name, sygnal, config):
        super().__init__(name, sygnal, config)

        nonunderstood = set(self.cfg.keys()).difference(self.UNDERSTOOD_CONFIG_FIELDS)
        if len(nonunderstood) > 0:
            logger.warning(
                "The following configuration fields are not understood: %s",
                nonunderstood,
            )

        self.template = self.get_config("template")
        self.event_handlers = self.get_config("event_handlers")

        platform = self.get_config("platform")
        if not platform or platform == "production" or platform == "prod":
            self.use_sandbox = False
        elif platform == "sandbox":
            self.use_sandbox = True
        else:
            raise PushkinSetupException(f"Invalid platform: {platform}")

        certfile = self.get_config("certfile")
        keyfile = self.get_config("keyfile")
        if not certfile and not keyfile:
            raise PushkinSetupException(
                "You must provide a path to an APNs certificate, or an APNs token."
            )

        if certfile:
            if not os.path.exists(certfile):
                raise PushkinSetupException(
                    f"The APNs certificate '{certfile}' does not exist."
                )
        else:
            # keyfile
            if not os.path.exists(keyfile):
                raise PushkinSetupException(
                    f"The APNs key file '{keyfile}' does not exist."
                )
            if not self.get_config("key_id"):
                raise PushkinSetupException("You must supply key_id.")
            if not self.get_config("team_id"):
                raise PushkinSetupException("You must supply team_id.")
            if not self.get_config("topic"):
                raise PushkinSetupException("You must supply topic.")

        if self.get_config("certfile") is not None:
            self.apns_client = APNs(
                client_cert=self.get_config("certfile"), use_sandbox=self.use_sandbox
            )
        else:
            self.apns_client = APNs(
                key=self.get_config("keyfile"),
                key_id=self.get_config("key_id"),
                team_id=self.get_config("team_id"),
                topic=self.get_config("topic"),
                use_sandbox=self.use_sandbox,
            )

        # without this, aioapns will retry every second forever.
        self.apns_client.pool.max_connection_attempts = 3

    @staticmethod
    def _map_device_token(device):
        return base64.b64decode(device.pushkey).hex()

    def _map_event_dispatch_handler(self, n):
        if n.event_id and not n.type:
            return self._dispatch_event

        if not self.event_handlers:
            return self._dispatch_message

        handler = self.event_handlers.get(n.type, None)
        if handler == "message":
            return self._dispatch_message
        elif handler == "voip":
            return self._dispatch_voip
        elif handler == "event":
            return self._dispatch_event
        else:
            return None

    async def _dispatch_event(self, log, span, n, device):
        """
        Dispatch handler for a data only notification (no alert)
        See `event_handlers` configuration option
        """
        payload = apnstruncate.truncate(
            self._get_payload_event(n),
            max_length=self.MAX_JSON_BODY_SIZE)
        return await self._dispatch(log, span, device, payload, n.prio)

    async def _dispatch_voip(self, log, span, n, device):
        """
        Dispatch handler for a voip notification
        See `event_handlers` configuration option
        """
        payload = apnstruncate.truncate(
            self._get_payload_voip(n, log),
            max_length=self.VOIP_MAX_JSON_BODY_SIZE)
        return await self._dispatch(log, span, device, payload, n.prio, push_type=PushType.VOIP)

    async def _dispatch_message(self, log, span, n, device):
        """
        Dispatch handler for a standard notification
        See `event_handlers` configuration option
        """
        payload = apnstruncate.truncate(
            self._get_payload_message(n, log),
            max_length=self.MAX_JSON_BODY_SIZE)
        return await self._dispatch(log, span, device, payload, n.prio)

    async def _dispatch(self, log, span, device, shaved_payload, prio, push_type=None):
        notification_id = str(uuid4())

        log.info(f"Sending as APNs-ID {notification_id}")
        span.set_tag("apns_id", notification_id)

        if shaved_payload is None:
            span.log_kv({logs.EVENT: "apns_no_payload"})
            return

        log.info(device.pushkey)
        device_token = base64.b64decode(device.pushkey).hex()
        log.info(device_token)

        request = NotificationRequest(
            device_token=device_token,
            notification_id=notification_id,
            message=shaved_payload,
            priority=self._map_priority(prio),
            push_type=push_type
        )

        log.info(f"Sending APN {request.notification_id}")
        if request.notification_id:
            span.set_tag("apns_id", request.notification_id)

        try:
            with SEND_TIME_HISTOGRAM.time():
                response = await self._send_notification(request)
        except aioapns.ConnectionError:
            raise TemporaryNotificationDispatchException('aioapns: Connection Failure')

        code = int(response.status)
        span.set_tag(tags.HTTP_STATUS_CODE, code)
        RESPONSE_STATUS_CODES_COUNTER.labels(pushkin=self.name, code=code).inc()

        if response.is_successful:
            return []
        else:
            span.set_tag("apns_reason", response.description)
            if code == self.TOKEN_ERROR_CODE or response.description == self.TOKEN_ERROR_REASON:
                return [request.device_token]
            elif 500 <= code < 600:
                error = f"{response.status} {response.description}"
                raise TemporaryNotificationDispatchException(error)
            else:
                error = f"{response.status} {response.description}"
                raise NotificationDispatchException(error)

    async def dispatch_notification(self, n, device, context):
        log = NotificationLoggerAdapter(logger, {"request_id": context.request_id})

        # The pushkey is kind of secret because you can use it to send push
        # to someone.
        # span_tags = {"pushkey": device.pushkey}
        span_tags = {}

        with self.sygnal.tracer.start_span(
            "apns_dispatch", tags=span_tags, child_of=context.opentracing_span
        ) as span_parent:

            dispatch_handler = self._map_event_dispatch_handler(n)
            if dispatch_handler is None:
                return []  # skipped

            for retry_number in range(self.MAX_TRIES):
                try:
                    log.debug("Trying")
                    span_tags = {"retry_num": retry_number}

                    with self.sygnal.tracer.start_span(
                        "apns_dispatch_try", tags=span_tags, child_of=span_parent
                    ) as span:
                        return await dispatch_handler(log, span, n, device)
                except TemporaryNotificationDispatchException as exc:
                    retry_delay = self.RETRY_DELAY_BASE * (2 ** retry_number)
                    if exc.custom_retry_delay is not None:
                        retry_delay = exc.custom_retry_delay

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
                        ) from exc
                    else:
                        await twisted_sleep(
                            retry_delay, twisted_reactor=self.sygnal.reactor
                        )

    def _get_payload_event(self, n):
        """
        Constructs a payload for a notification where we know only the event ID.
        Args:
            n: The notification to construct a payload for.

        Returns:
            The APNs payload as a nested dicts.
        """
        payload = {}

        if n.room_id:
            payload["room_id"] = n.room_id
        if n.event_id:
            payload["event_id"] = n.event_id

        if n.counts.unread is not None:
            payload["unread_count"] = n.counts.unread
        if n.counts.missed_calls is not None:
            payload["missed_calls"] = n.counts.missed_calls

        return payload

    def _get_payload_voip(self, n, log):
        """
        Constructs a payload for a voip notification (no alert needed)
        Args:
            n: The notification to construct a payload for
            log: A logger

        Returns:
            The APNs payload as nested dicts.
        """
        data = {}
        if n.room_id:
            data["room_id"] = n.room_id
        if n.event_id:
            data["event_id"] = n.event_id

        if n.type is not None and "m.call" in n.type:
            data["type"] = n.type
            if n.sender_display_name is not None:
                data["sender_display_name"] = n.sender_display_name

            data["is_video_call"] = False
            if n.content:
                if "offer" in n.content and "sdp" in n.content["offer"]:
                    sdp = n.content["offer"]["sdp"]
                    if "m=video" in sdp:
                        data["is_video_call"] = True
                if "call_id" in n.content:
                    data["call_id"] = n.content["call_id"]

        return data

    def _get_payload_message(self, n, log):
        """
        Constructs a payload for a notification.
        Args:
            n: The notification to construct a payload for.
            log: A logger.

        Returns:
            The APNs payload as nested dicts.
        """
        from_display = n.sender
        if n.sender_display_name is not None:
            from_display = n.sender_display_name
        from_display = from_display[0: self.MAX_FIELD_LENGTH]

        loc_key = None
        loc_args = None
        if n.type == "m.room.message" or n.type == "m.room.encrypted":
            room_display = None
            if n.room_name:
                room_display = n.room_name[0: self.MAX_FIELD_LENGTH]
            elif n.room_alias:
                room_display = n.room_alias[0: self.MAX_FIELD_LENGTH]

            content_display = None
            action_display = None
            is_image = False
            if n.content and "msgtype" in n.content and "body" in n.content:
                if "body" in n.content:
                    if n.content["msgtype"] == "m.text":
                        content_display = n.content["body"]
                    elif n.content["msgtype"] == "m.emote":
                        action_display = n.content["body"]
                    else:
                        # fallback: 'body' should always be user-visible text
                        # in an m.room.message
                        content_display = n.content["body"]
                if n.content["msgtype"] == "m.image":
                    is_image = True

            if room_display:
                if is_image:
                    loc_key = "IMAGE_FROM_USER_IN_ROOM"
                    loc_args = [from_display, content_display, room_display]
                elif content_display:
                    loc_key = "MSG_FROM_USER_IN_ROOM_WITH_CONTENT"
                    loc_args = [from_display, room_display, content_display]
                elif action_display:
                    loc_key = "ACTION_FROM_USER_IN_ROOM"
                    loc_args = [room_display, from_display, action_display]
                else:
                    loc_key = "MSG_FROM_USER_IN_ROOM"
                    loc_args = [from_display, room_display]
            else:
                if is_image:
                    loc_key = "IMAGE_FROM_USER"
                    loc_args = [from_display, content_display]
                elif content_display:
                    loc_key = "MSG_FROM_USER_WITH_CONTENT"
                    loc_args = [from_display, content_display]
                elif action_display:
                    loc_key = "ACTION_FROM_USER"
                    loc_args = [from_display, action_display]
                else:
                    loc_key = "MSG_FROM_USER"
                    loc_args = [from_display]

        elif n.type == "m.call.invite":
            is_video_call = False

            # This detection works only for hs that uses WebRTC for calls
            if n.content and "offer" in n.content and "sdp" in n.content["offer"]:
                sdp = n.content["offer"]["sdp"]
                if "m=video" in sdp:
                    is_video_call = True

            if is_video_call:
                loc_key = "VIDEO_CALL_FROM_USER"
            else:
                loc_key = "VOICE_CALL_FROM_USER"

            loc_args = [from_display]
        elif n.type == "m.room.member":
            if n.user_is_target:
                if n.membership == "invite":
                    if n.room_name:
                        loc_key = "USER_INVITE_TO_NAMED_ROOM"
                        loc_args = [
                            from_display,
                            n.room_name[0: self.MAX_FIELD_LENGTH],
                        ]
                    elif n.room_alias:
                        loc_key = "USER_INVITE_TO_NAMED_ROOM"
                        loc_args = [
                            from_display,
                            n.room_alias[0: self.MAX_FIELD_LENGTH],
                        ]
                    else:
                        loc_key = "USER_INVITE_TO_CHAT"
                        loc_args = [from_display]
        elif n.type:
            # A type of message was received that we don't know about
            # but it was important enough for a push to have got to us
            loc_key = "MSG_FROM_USER"
            loc_args = [from_display]

        aps = {}
        if loc_key:
            aps["alert"] = {"loc-key": loc_key}

        if loc_args:
            aps["alert"]["loc-args"] = loc_args

        badge = None
        if n.counts.unread is not None:
            badge = n.counts.unread
        if n.counts.missed_calls is not None:
            if badge is None:
                badge = 0
            badge += n.counts.missed_calls

        if badge is not None:
            aps["badge"] = badge

        if loc_key:
            aps["content-available"] = 1

        if loc_key is None and badge is None:
            log.info("Nothing to do for alert of type %s", n.type)
            return None

        payload = {}

        if loc_key and n.room_id:
            payload["room_id"] = n.room_id

        payload["aps"] = aps

        return payload

    async def _send_notification(self, request):
        return await Deferred.fromFuture(
            asyncio.ensure_future(self.apns_client.send_notification(request))
        )

    @staticmethod
    def _map_priority(priority):
        p = 10
        if priority == "low":
            p = 5
        return p
