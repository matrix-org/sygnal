# -*- coding: utf-8 -*-
# Copyright 2017 Johannes Oertel
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

from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import json

import paho.mqtt.client as mqtt

from . import Pushkin
from .exceptions import PushkinSetupException


logger = logging.getLogger(__name__)

class MqttPushkin(Pushkin):
    def __init__(self, name):
        super(MqttPushkin, self).__init__(name)

    def setup(self, ctx):
        self.broker = self.getConfig('broker')
        if not self.broker:
            raise PushkinSetupException("No MQTT broker set in config")

        self.mqtt = mqtt.Client(None)
        username = self.getConfig('username')
        password = self.getConfig('password')
        if username and password:
            self.mqtt.username_pw_set(username, password)
        self.mqtt.connect(self.broker)
        self.mqtt.loop_start()

    def dispatchNotification(self, n):
        pushkeys = [device.pushkey for device in n.devices if device.app_id == self.name]
        data = MqttPushkin.build_data(n)
        json_data = json.dumps(data)

        for pushkey in pushkeys:
            self.mqtt.publish(pushkey, json_data, 2)

        return []

    def shutdown(self):
        self.mqtt.disconnect()
        self.mqtt.loop_stop()
