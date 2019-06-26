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
import logging
import os

from aioapns import APNs, NotificationRequest

from sygnal import apnstruncate
from sygnal.exceptions import (
    PushkinSetupException,
    TemporaryNotificationDispatchException,
    NotificationDispatchException,
)
from sygnal.notifications import Pushkin, NotificationLoggerAdapter
from sygnal.utils import twisted_sleep

logger = logging.getLogger(__name__)


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

    UNDERSTOOD_CONFIG_FIELDS = {"type", "platform", "certfile"}

    def __init__(self, name, sygnal, config):
        super().__init__(name, sygnal, config)

        nonunderstood = set(self.cfg.keys()).difference(self.UNDERSTOOD_CONFIG_FIELDS)
        if len(nonunderstood) > 0:
            logger.warning(
                "The following configuration fields are not understood: %s",
                nonunderstood,
            )

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

        self.apns_client = None

    async def start(self, sygnal):
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

    async def dispatch_notification(self, n, device, context):
        log = NotificationLoggerAdapter(logger, {"request_id": context.request_id})

        if n.event_id and not n.type:
            payload = self._get_payload_event_id_only(n)
        else:
            payload = self._get_payload_full(n, log)

        prio = 10
        if n.prio == "low":
            prio = 5

        shaved_payload = apnstruncate.truncate(
            payload, max_length=self.MAX_JSON_BODY_SIZE
        )

        async def dispatch_request():
            """
            Actually attempts to dispatch the notification once.
            """
            request = NotificationRequest(
                device_token=device.pushkey,
                message=shaved_payload,
                priority=prio
                # todo notification_id=str(uuid4()) ?
                # todo time_to_live=3 ?
            )

            response = await self.apns_client.send_notification(request)

            # TODO asyncio compat **is** required.

            code = int(response.status)

            if response.is_successful:
                return []
            else:
                # .description corresponds to the 'reason' response field
                if (
                    code == self.TOKEN_ERROR_CODE
                    or response.description == self.TOKEN_ERROR_REASON
                ):
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

        for retry_number in range(self.MAX_TRIES):
            try:
                log.debug("Trying")
                return await dispatch_request()
            except TemporaryNotificationDispatchException as exc:
                retry_delay = self.RETRY_DELAY_BASE * (2 ** retry_number)
                if exc.custom_retry_delay is not None:
                    retry_delay = exc.custom_retry_delay

                log.exception(
                    "Temporary failure, will retry in %d seconds", retry_delay
                )

                if retry_number == self.MAX_TRIES - 1:
                    raise NotificationDispatchException(
                        "Retried too many times."
                    ) from exc
                else:
                    await twisted_sleep(
                        retry_delay, twisted_reactor=self.sygnal.reactor
                    )

    def _get_payload_event_id_only(self, n):
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

    def _get_payload_full(self, n, log):
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
                        # fallback: 'body' should always be user-visible text in an m.room.message
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

    async def shutdown(self):  # TODO
        return await super().shutdown()
