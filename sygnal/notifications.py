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

        if 'sound' in raw:
            self.sound = raw['sound']


class Device:
    def __init__(self, raw):
        self.app_id = None
        self.pushkey = None
        self.pushkey_ts = 0
        self.data = None
        self.tweaks = None

        if 'app_id' not in raw:
            raise InvalidNotificationException("Device with no app_id")
        if 'pushkey' not in raw:
            raise InvalidNotificationException("Device with no pushkey")
        if 'pushkey_ts' in raw:
            self.pushkey_ts = raw['pushkey_ts']
        if 'tweaks' in raw:
            self.tweaks = Tweaks(raw['tweaks'])
        else:
            self.tweaks = Tweaks({})
        self.app_id = raw['app_id']
        self.pushkey = raw['pushkey']
        if 'data' in raw:
            self.data = raw['data']


class Counts:
    def __init__(self, raw):
        self.unread = None
        self.missed_calls = None

        if 'unread' in raw:
            self.unread = raw['unread']
        if 'missed_calls' in raw:
            self.missed_calls = raw['missed_calls']


class Notification:
    def __init__(self, notif):
        optional_attrs = [
            'room_name',
            'room_alias',
            'prio',
            'membership',
            'sender_display_name',
            'content',
            'event_id',
            'room_id',
            'user_is_target',
            'type',
            'sender',
        ]
        for a in optional_attrs:
            if a in notif:
                self.__dict__[a] = notif[a]
            else:
                self.__dict__[a] = None

        if 'devices' not in notif or not isinstance(notif['devices'], list):
            raise InvalidNotificationException("Expected list in 'devices' key")

        if 'counts' in notif:
            self.counts = Counts(notif['counts'])
        else:
            self.counts = Counts({})

        self.devices = [Device(d) for d in notif['devices']]


class Pushkin(object):
    def __init__(self, name):
        self.name = name

    def setup(self, sygnal):
        pass

    def getConfig(self, key):
        if not self.cfg.has_option('apps', '%s.%s' % (self.name, key)):
            return None
        return self.cfg.get('apps', '%s.%s' % (self.name, key))

    async def dispatchNotification(self, n, device):
        """
        Attempt to dispatch the notification for the device specified.

        :param n: The notification to dispatch via this pushkin
        :param device: The device to dispatch the notification for.
        :return: A list of rejected pushkeys, to be reported back to the homeserver
        """
        pass

    async def shutdown(self):
        pass
