# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
# Copyright 2019 New Vector Ltd
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

from .exceptions import InvalidNotificationException


class Tweaks:
    def __init__(self, raw):
        self.sound = None

        if "sound" in raw:
            self.sound = raw["sound"]


class Device:
    def __init__(self, raw):
        self.app_id = None
        self.pushkey = None
        self.pushkey_ts = 0
        self.data = None
        self.tweaks = None

        if "app_id" not in raw:
            raise InvalidNotificationException("Device with no app_id")
        if "pushkey" not in raw:
            raise InvalidNotificationException("Device with no pushkey")
        if "pushkey_ts" in raw:
            self.pushkey_ts = raw["pushkey_ts"]
        if "tweaks" in raw:
            self.tweaks = Tweaks(raw["tweaks"])
        else:
            self.tweaks = Tweaks({})
        self.app_id = raw["app_id"]
        self.pushkey = raw["pushkey"]
        if "data" in raw:
            self.data = raw["data"]


class Counts:
    def __init__(self, raw):
        self.unread = None
        self.missed_calls = None

        if "unread" in raw:
            self.unread = raw["unread"]
        if "missed_calls" in raw:
            self.missed_calls = raw["missed_calls"]


class Notification:
    def __init__(self, notif):
        optional_attrs = [
            "room_name",
            "room_alias",
            "prio",
            "membership",
            "sender_display_name",
            "content",
            "event_id",
            "room_id",
            "user_is_target",
            "type",
            "sender",
        ]
        for a in optional_attrs:
            if a in notif:
                self.__dict__[a] = notif[a]
            else:
                self.__dict__[a] = None

        if "devices" not in notif or not isinstance(notif["devices"], list):
            raise InvalidNotificationException("Expected list in 'devices' key")

        if "counts" in notif:
            self.counts = Counts(notif["counts"])
        else:
            self.counts = Counts({})

        self.devices = [Device(d) for d in notif["devices"]]


class Pushkin(object):
    def __init__(self, name, sygnal, config):
        self.name = name
        self.cfg = config
        self.sygnal = sygnal

    async def start(self, sygnal):
        pass

    def get_config(self, key, default=None):
        if key not in self.cfg:
            return default
        return self.cfg[key]

    async def dispatch_notification(self, n, device, context):
        """
        Args:
            n: The notification to dispatch via this pushkin
            device: The device to dispatch the notification for.
            context (NotificationContext): the request context

        Returns:
            A list of rejected pushkeys, to be reported back to the homeserver
        """
        pass

    async def shutdown(self):
        pass


class NotificationContext(object):
    def __init__(self, request_id, opentracing_span):
        """
        Args:
            request_id (str): An ID for the request, or None to have it
                generated automatically.
            opentracing_span (Span): The span for the API request triggering
                the notification.
        """
        self.request_id = request_id
        self.opentracing_span = opentracing_span
