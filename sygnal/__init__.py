# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
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


class Device:
    def __init__(self, raw):
        self.app_id = None
        self.pushkey = None
        self.data = None

        if 'app_id' not in raw:
            raise InvalidNotificationException("Device with no app_id")
        if 'pushkey' not in raw:
            raise InvalidNotificationException("Device with no pushkey")
        self.app_id = raw['app_id']
        self.pushkey = raw['pushkey']
        if 'data' in raw:
            self.data = raw['data']

class Notification:
    def __init__(self, notif):
        attrs = [ 'transition', 'id', 'type', 'from' ]
        for a in attrs:
            if a not in notif:
               raise InvalidNotificationException("Expected '%s' key" % (a,))
            # 'from'  is reserved
            if a == 'from':
                self.fromuser = notif[a]
            else:
                self.__dict__[a] = notif[a]

        if 'devices' not in notif or not isinstance(notif['devices'], list):
               raise InvalidNotificationException("Expected list in 'devices' key")

        self.devices = [Device(d) for d in notif['devices']]
        
class Pushkin(object):
    def __init__(self, name):
        self.name = name

    def setup(self):
        pass

    def getConfig(self, key):
        if not self.cfg.has_option('apps', '%s.%s' % (self.name, key)):
            return None
        return self.cfg.get('apps', '%s.%s' % (self.name, key))
        
    def dispatchNotification(self, n):
        pass

class InvalidNotificationException(Exception):
    pass

class PushkinSetupException(Exception):
    pass

class NotificationDispatchException(Exception):
    pass
