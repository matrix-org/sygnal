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

from .exceptions import PushkinSetupException
from .notifications import Pushkin

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_fcm_request_time", "Time taken to send HTTP request"
)

MAX_TRIES = 3
RETRY_DELAY_BASE = 10
MAX_BYTES_PER_FIELD = 1024
DEFAULT_MAX_CONNECTIONS = 20

logger = logging.getLogger(__name__)


@attr.s
class FirebaseConfig(object):
    credentials = attr.ib()
    max_connections = attr.ib(default=20)
    message_types = attr.ib(default=attr.Factory(dict), type=Dict[str, str])


class FirebasePushkin(Pushkin):
    def __init__(self, name, sygnal, config):
        super(FirebasePushkin, self).__init__(name, sygnal, config)

        self.db = sygnal.database
        self.reactor = sygnal.reactor
        self.config = FirebaseConfig(
            **{x: y for x, y in self.cfg.items() if x != "type"}
        )

        credential_path = self.config.credentials
        if not credential_path:
            raise PushkinSetupException("No Credential path set in config")

        cred = credentials.Certificate(credential_path)

        self._pool = ThreadPool(maxthreads=self.config.max_connections)
        self._pool.start()

        self._app = initialize_app(cred, name="app")

    async def dispatch_notification(self, n, device, context):

        pushkeys = [
            device.pushkey for device in n.devices if device.app_id == self.name
        ]

        failed = []
        data = self.build_message(n)

        logger.debug("Type: %s", data["type"])

        if (
            data["type"] != "m.room.message"
            or data["content"]["msgtype"] not in self.config.message_types
        ):
            return failed

        unread_count = data["unread"] if data["unread"] is not None else 0
        notification_title = (
            data["sender_display_name"]
            if data["room_name"] is None
            else data["room_name"]
        )

        if data["content"]["msgtype"] == "m.text":
            message = self.text_message_notification(
                data, notification_title, unread_count, pushkeys
            )
        else:
            message = self.default_notification(
                data,
                notification_title,
                unread_count,
                pushkeys,
                self.config.message_types[data["content"]["msgtype"]],
            )

        failed.extend(await self.send(message))

        return failed

    async def send(self, message):

        d = Deferred()

        def done(success, result):
            self.reactor.callFromThread(d.callback, result)

        with SEND_TIME_HISTOGRAM.time():
            self._pool.callInThreadWithCallback(
                done, messaging.send_multicast, message, app=self._app
            )
            response = await d

        logger.debug(
            "Message send success: %s of %s",
            response.success_count,
            response.success_count + response.failure_count,
        )

        failed = []

        return failed

    @staticmethod
    def build_message(n):
        data = {}
        for attribute in [
            "event_id",
            "type",
            "sender",
            "room_name",
            "room_alias",
            "membership",
            "sender_display_name",
            "content",
            "room_id",
        ]:
            if hasattr(n, attribute):
                data[attribute] = getattr(n, attribute)
                # Truncate fields to a sensible maximum length. If the whole
                # body is too long, GCM will reject it.
                if (
                    data[attribute] is not None
                    and len(data[attribute]) > MAX_BYTES_PER_FIELD
                ):
                    data[attribute] = data[attribute][0:MAX_BYTES_PER_FIELD]

        data["prio"] = "high"
        if n.prio == "low":
            data["prio"] = "normal"

        if getattr(n, "counts", None):
            data["unread"] = n.counts.unread
            data["missed_calls"] = n.counts.missed_calls

        return data

    def text_message_notification(
        self, data, notification_title, unread_count, pushkeys
    ):
        decoded_message = decode_complex_message(data["content"]["body"])
        # Check if data contains a json-decodable and valid MatrixComplexMessage
        if decoded_message:
            return self.complex_message_notification(
                data, decoded_message, notification_title, unread_count, pushkeys
            )

        if data["room_name"] is None:
            notification_body = data["content"]["body"]
        else:
            notification_body = (
                data["sender_display_name"] + ": " + data["content"]["body"]
            )

        return self.default_notification(
            data, notification_title, unread_count, pushkeys, notification_body.strip()
        )

    def complex_message_notification(
        self, data, message, notification_title, unread_count, pushkeys
    ):
        notification_body = message.get("title", "").strip() + " "
        if "images" in message:
            notification_body += self.config.message_types.get("m.image")
        elif "videos" in message:
            notification_body += self.config.message_types.get("m.video")
        elif "title" not in message and "message" in message:
            notification_body += message["message"].strip()

        return self.default_notification(
            data, notification_title, unread_count, pushkeys, notification_body
        )

    def default_notification(
        self, data, notification_title, unread_count, pushkeys, notification_body
    ):
        return messaging.MulticastMessage(
            notification=messaging.Notification(
                title=notification_title, body=notification_body
            ),
            data={
                "title": notification_title,
                "body": notification_body,
                "room_id": data["room_id"],
            },
            android=messaging.AndroidConfig(
                priority=data["prio"],
                notification=messaging.AndroidNotification(
                    click_action="FLUTTER_NOTIFICATION_CLICK", tag=data["room_id"]
                ),
            ),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10" if data["prio"] == 10 else "5"},
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(badge=unread_count, thread_id=data["room_id"])
                ),
            ),
            tokens=pushkeys,
        )


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
