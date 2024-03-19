# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
# Copyright 2017 Vector Creations Ltd
# Copyright 2019-2020 The Matrix.org Foundation C.I.C.
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
import copy
import logging
import os
from datetime import timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

import aioapns
from aioapns import APNs, NotificationRequest
from aioapns.common import NotificationResult, PushType
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import load_pem_x509_certificate
from opentracing import Span, logs, tags
from prometheus_client import Counter, Gauge, Histogram
from twisted.internet.defer import Deferred

from sygnal import apnstruncate
from sygnal.exceptions import (
    NotificationDispatchException,
    PushkinSetupException,
    TemporaryNotificationDispatchException,
)
from sygnal.helper.proxy.proxy_asyncio import ProxyingEventLoopWrapper
from sygnal.notifications import (
    ConcurrencyLimitedPushkin,
    Device,
    Notification,
    NotificationContext,
)
from sygnal.utils import NotificationLoggerAdapter, twisted_sleep

if TYPE_CHECKING:
    from sygnal.sygnal import Sygnal


logger = logging.getLogger(__name__)

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_apns_request_time", "Time taken to send HTTP request to APNS"
)

ACTIVE_REQUESTS_GAUGE = Gauge(
    "sygnal_active_apns_requests", "Number of APNS requests in flight"
)

RESPONSE_STATUS_CODES_COUNTER = Counter(
    "sygnal_apns_status_codes",
    "Number of HTTP response status codes received from APNS",
    labelnames=["pushkin", "code"],
)

CERTIFICATE_EXPIRATION_GAUGE = Gauge(
    "sygnal_client_cert_expiry",
    "The expiry date of the client certificate in seconds since the epoch",
    labelnames=["pushkin"],
)


class ApnsPushkin(ConcurrencyLimitedPushkin):
    """
    Relays notifications to the Apple Push Notification Service.
    """

    # Errors for which the token should be rejected and not reused
    # See https://developer.apple.com/documentation/usernotifications/setting_up_a_remote_notification_server/handling_notification_responses_from_apns  # noqa: E501
    # for the full list of possible errors.
    TOKEN_ERRORS = {
        # A client has uploaded an invalid token.
        (400, "BadDeviceToken"),
        # `DeviceTokenNotForTopic` may be due to a token for a different app or an
        # incorrect topic in the Sygnal configuration. In the event of a
        # misconfiguration, clients will need to reupload their tokens to their
        # homeserver.
        (400, "DeviceTokenNotForTopic"),
        (400, "TopicDisallowed"),
        # The token is no longer valid, probably because the app has been uninstalled.
        (410, "Unregistered"),
    }

    MAX_TRIES = 3
    RETRY_DELAY_BASE = 10

    MAX_FIELD_LENGTH = 1024
    MAX_JSON_BODY_SIZE = 4096

    UNDERSTOOD_CONFIG_FIELDS = {
        "type",
        "platform",
        "certfile",
        "team_id",
        "key_id",
        "keyfile",
        "topic",
        "push_type",
        "convert_device_token_to_hex",
    } | ConcurrencyLimitedPushkin.UNDERSTOOD_CONFIG_FIELDS

    APNS_PUSH_TYPES = {
        "alert": PushType.ALERT,
        "background": PushType.BACKGROUND,
        "voip": PushType.VOIP,
        "complication": PushType.COMPLICATION,
        "fileprovider": PushType.FILEPROVIDER,
        "mdm": PushType.MDM,
    }

    def __init__(self, name: str, sygnal: "Sygnal", config: Dict[str, Any]) -> None:
        super().__init__(name, sygnal, config)

        nonunderstood = set(self.cfg.keys()).difference(self.UNDERSTOOD_CONFIG_FIELDS)
        if len(nonunderstood) > 0:
            logger.warning(
                "The following configuration fields are not understood: %s",
                nonunderstood,
            )

        platform = self.get_config("platform", str)
        if not platform or platform == "production" or platform == "prod":
            self.use_sandbox = False
        elif platform == "sandbox":
            self.use_sandbox = True
        else:
            raise PushkinSetupException(f"Invalid platform: {platform}")

        certfile = self.get_config("certfile", str)
        keyfile = self.get_config("keyfile", str)
        if not certfile and not keyfile:
            raise PushkinSetupException(
                "You must provide a path to an APNs certificate, or an APNs token."
            )

        if certfile:
            if not os.path.exists(certfile):
                raise PushkinSetupException(
                    f"The APNs certificate '{certfile}' does not exist."
                )
        elif keyfile:
            # keyfile
            if not os.path.exists(keyfile):
                raise PushkinSetupException(
                    f"The APNs key file '{keyfile}' does not exist."
                )
            if not self.get_config("key_id", str):
                raise PushkinSetupException("You must supply key_id.")
            if not self.get_config("team_id", str):
                raise PushkinSetupException("You must supply team_id.")
            if not self.get_config("topic", str):
                raise PushkinSetupException("You must supply topic.")

        # use the Sygnal global proxy configuration
        proxy_url_str = sygnal.config.get("proxy")

        loop = asyncio.get_event_loop()
        if proxy_url_str:
            # this overrides the create_connection method to use a HTTP proxy
            loop = ProxyingEventLoopWrapper(loop, proxy_url_str)  # type: ignore

        async def make_apns() -> aioapns.APNs:
            if certfile is not None:
                # max_connection_attempts is actually the maximum number of
                # additional connection attempts, so =0 means try once only
                # (we will retry at a higher level so not worth doing more here)
                apns_client = APNs(
                    client_cert=certfile,
                    use_sandbox=self.use_sandbox,
                    max_connection_attempts=0,
                )

                self._report_certificate_expiration(certfile)

                return apns_client
            else:
                # max_connection_attempts is actually the maximum number of
                # additional connection attempts, so =0 means try once only
                # (we will retry at a higher level so not worth doing more here)
                return APNs(
                    key=self.get_config("keyfile", str),
                    key_id=self.get_config("key_id", str),
                    team_id=self.get_config("team_id", str),
                    topic=self.get_config("topic", str),
                    use_sandbox=self.use_sandbox,
                    max_connection_attempts=0,
                )

        self.apns_client = loop.run_until_complete(make_apns())

        push_type = self.get_config("push_type", str)
        if not push_type:
            self.push_type = None
        else:
            if push_type not in self.APNS_PUSH_TYPES.keys():
                raise PushkinSetupException(f"Invalid value for push_type: {push_type}")

            self.push_type = self.APNS_PUSH_TYPES[push_type]

        # without this, aioapns will retry every second forever.
        self.apns_client.pool.max_connection_attempts = 3

        # without this, aioapns will not use the proxy if one is configured.
        self.apns_client.pool.loop = loop

    def _report_certificate_expiration(self, certfile: str) -> None:
        """Export the epoch time that the certificate expires as a metric."""
        with open(certfile, "rb") as f:
            cert_bytes = f.read()

        cert = load_pem_x509_certificate(cert_bytes, default_backend())
        # Report the expiration time as seconds since the epoch (in UTC time).
        CERTIFICATE_EXPIRATION_GAUGE.labels(pushkin=self.name).set(
            cert.not_valid_after.replace(tzinfo=timezone.utc).timestamp()
        )

    async def _dispatch_request(
        self,
        log: NotificationLoggerAdapter,
        span: Span,
        device: Device,
        shaved_payload: Dict[str, Any],
        prio: int,
        notif_id: str,
    ) -> List[str]:
        """
        Actually attempts to dispatch the notification once.
        """
        span.set_tag("apns_id", notif_id)

        # Some client libraries will provide the push token in hex format already. Avoid
        # attempting to convert from base 64 to hex.
        if self.get_config("convert_device_token_to_hex", bool, True):
            device_token = base64.b64decode(device.pushkey).hex()
        else:
            device_token = device.pushkey

        request = NotificationRequest(
            device_token=device_token,
            message=shaved_payload,
            priority=prio,
            notification_id=notif_id,
            push_type=self.push_type,
        )

        try:
            with ACTIVE_REQUESTS_GAUGE.track_inprogress():
                with SEND_TIME_HISTOGRAM.time():
                    response = await self._send_notification(request)
        except aioapns.ConnectionError:
            raise TemporaryNotificationDispatchException("aioapns Connection Failure")

        code = int(response.status)

        span.set_tag(tags.HTTP_STATUS_CODE, code)

        RESPONSE_STATUS_CODES_COUNTER.labels(pushkin=self.name, code=code).inc()

        if response.is_successful:
            return []
        else:
            # .description corresponds to the 'reason' response field
            span.set_tag("apns_reason", response.description or "None")
            if (code, response.description) in self.TOKEN_ERRORS:
                log.info(
                    "APNs token %s for pushkin %s was rejected: %d %s",
                    device_token,
                    self.name,
                    code,
                    response.description,
                )
                return [device.pushkey]
            else:
                if 500 <= code < 600:
                    raise TemporaryNotificationDispatchException(
                        f"{response.status} {response.description}"
                    )
                else:
                    raise NotificationDispatchException(
                        f"{response.status} {response.description}"
                    )

    async def _dispatch_notification_unlimited(
        self, n: Notification, device: Device, context: NotificationContext
    ) -> List[str]:
        log = NotificationLoggerAdapter(logger, {"request_id": context.request_id})

        # The pushkey is kind of secret because you can use it to send push
        # to someone.
        # span_tags = {"pushkey": device.pushkey}
        span_tags: Dict[str, int] = {}

        with self.sygnal.tracer.start_span(
            "apns_dispatch", tags=span_tags, child_of=context.opentracing_span
        ) as span_parent:
            # Before we build the payload, check that the default_payload is not
            # malformed and reject the pushkey if it is

            default_payload = {}

            if device.data:
                default_payload = device.data.get("default_payload", {})
                if not isinstance(default_payload, dict):
                    log.warning(
                        "Rejecting pushkey due to misconfigured default_payload, "
                        "please ensure that default_payload is a dict."
                    )
                    return [device.pushkey]

            if n.event_id and not n.type:
                payload: Optional[Dict[str, Any]] = self._get_payload_event_id_only(
                    n, default_payload
                )
            else:
                payload = self._get_payload_full(n, device, log)

            if payload is None:
                # Nothing to do
                span_parent.log_kv({logs.EVENT: "apns_no_payload"})
                return []
            prio = 10
            if n.prio == "low":
                prio = 5

            shaved_payload = apnstruncate.truncate(
                payload, max_length=self.MAX_JSON_BODY_SIZE
            )

            for retry_number in range(self.MAX_TRIES):
                try:
                    span_tags = {"retry_num": retry_number}

                    with self.sygnal.tracer.start_span(
                        "apns_dispatch_try", tags=span_tags, child_of=span_parent
                    ) as span:
                        assert shaved_payload is not None

                        # this is no good: APNs expects ID to be in their format
                        # so we can't just derive a
                        # notif_id = context.request_id + f"-{n.devices.index(device)}"
                        notif_id = str(uuid4())
                        # XXX: shouldn't we use the same notif_id for each retry?

                        log.info(
                            "Sending (attempt %i) => %s APNs-ID:%s room:%s, event:%s",
                            retry_number,
                            notif_id,
                            device.pushkey,
                            n.room_id,
                            n.event_id,
                        )

                        return await self._dispatch_request(
                            log, span, device, shaved_payload, prio, notif_id
                        )
                except TemporaryNotificationDispatchException as exc:
                    retry_delay = self.RETRY_DELAY_BASE * (2**retry_number)
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
                    if retry_number < self.MAX_TRIES - 1:
                        await twisted_sleep(
                            retry_delay, twisted_reactor=self.sygnal.reactor
                        )

            raise NotificationDispatchException("Retried too many times.")

    def _get_payload_event_id_only(
        self, n: Notification, default_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Constructs a payload for a notification where we know only the event ID.
        Args:
            n: The notification to construct a payload for.
            device: Device information to which the constructed payload
            will be sent.

        Returns:
            The APNs payload as a nested dicts.
        """
        payload = {}

        payload.update(default_payload)

        if n.room_id:
            payload["room_id"] = n.room_id
        if n.event_id:
            payload["event_id"] = n.event_id

        if n.counts.unread is not None:
            payload["unread_count"] = n.counts.unread
        if n.counts.missed_calls is not None:
            payload["missed_calls"] = n.counts.missed_calls

        return payload

    def _get_payload_full(
        self, n: Notification, device: Device, log: NotificationLoggerAdapter
    ) -> Optional[Dict[str, Any]]:
        """
        Constructs a payload for a notification.
        Args:
            n: The notification to construct a payload for.
            device: Device information to which the constructed payload
            will be sent.
            log: A logger.

        Returns:
            The APNs payload as nested dicts.
        """
        if not n.sender and not n.sender_display_name:
            from_display = " "
        elif n.sender_display_name is not None:
            from_display = n.sender_display_name
        elif n.sender is not None:
            from_display = n.sender
        from_display = from_display[0 : self.MAX_FIELD_LENGTH]

        loc_key = None
        loc_args = None
        if n.type == "m.room.message" or n.type == "m.room.encrypted":
            room_display = None
            if n.room_name:
                room_display = n.room_name[0 : self.MAX_FIELD_LENGTH]
            elif n.room_alias:
                room_display = n.room_alias[0 : self.MAX_FIELD_LENGTH]

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
                            n.room_name[0 : self.MAX_FIELD_LENGTH],
                        ]
                    elif n.room_alias:
                        loc_key = "USER_INVITE_TO_NAMED_ROOM"
                        loc_args = [
                            from_display,
                            n.room_alias[0 : self.MAX_FIELD_LENGTH],
                        ]
                    else:
                        loc_key = "USER_INVITE_TO_CHAT"
                        loc_args = [from_display]
        elif n.type:
            # A type of message was received that we don't know about
            # but it was important enough for a push to have got to us
            loc_key = "MSG_FROM_USER"
            loc_args = [from_display]

        badge = None
        if n.counts.unread is not None:
            badge = n.counts.unread
        if n.counts.missed_calls is not None:
            if badge is None:
                badge = 0
            badge += n.counts.missed_calls

        if loc_key is None and badge is None:
            log.info("Nothing to do for alert of type %s", n.type)
            return None

        payload = {}

        if n.type and device.data:
            payload = copy.deepcopy(device.data.get("default_payload", {}))

        payload.setdefault("aps", {})

        if loc_key:
            payload["aps"].setdefault("alert", {})["loc-key"] = loc_key

        if loc_args:
            payload["aps"].setdefault("alert", {})["loc-args"] = loc_args

        if badge is not None:
            payload["aps"]["badge"] = badge

        if loc_key and n.room_id:
            payload["room_id"] = n.room_id
        if loc_key and n.event_id:
            payload["event_id"] = n.event_id

        return payload

    async def _send_notification(
        self, request: NotificationRequest
    ) -> NotificationResult:
        return await Deferred.fromFuture(
            asyncio.ensure_future(self.apns_client.send_notification(request))
        )
