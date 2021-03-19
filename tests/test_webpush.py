# -*- coding: utf-8 -*-
# Copyright 2021 The Matrix.org Foundation C.I.C.
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
from unittest.mock import MagicMock, patch
from sygnal.webpushpushkin import WebpushPushkin

from sygnal import apnstruncate

from tests import testutils

from py_vapid import Vapid, VapidException

PUSHKIN_ID = "com.example.webpush"
VAPID_EMAIL = "alice@server.tld"
VAPID_PRIVATE_KEY = "tests/webpush/private_key.pem"
DEVICE_EXAMPLE = {
    "app_id":PUSHKIN_ID,
    "kind":"http",
    "data":{
        "endpoint":"https://updates.push.services.mozilla.com/wpush/v2/gAAAAABgVKjXJ64tNBRqS112IwSNXffl4bN6egRgiIwN9Gv5ki28Uu-DGJm0lZdtr_DyRwpHQwNKsKlzckgcYnqxP4heOYpEqU0IUFPeGkvE2QnpF-i02b-fN-bgwbKFNtSpPXYj7VC1UR-fMzjtlmYI3yk1l1v3smZM6BQCnwIZor52Ip5LCzI",
        "auth":"tk-uFizVuguwlVdI6lXrKA",
        "default_payload":{
            "session_id":"7192604822299679"
        },
    },
    "pushkey":"BMndGyzAWuhx4qbONDPp_pwtaA95U8c967lkUMx8LUY09WcxRzRB5WuSJox56DYZy7lx4Yt9tfuKcpyoz-KDYTA",
    "app_display_name":"Some web app",
    "device_display_name":"Some web app",
    "lang":"en"
}

class MockHttpWrapper:
    def __init__(self):
        self.code = None
        self.response_text = None
        self.headers = None
        self.url = None
        self.body = None

    def post(self, endpoint, data, headers, timeout):
        self.url = endpoint
        self.body = data
        self.headers = headers
        deferred = Deferred()
        # this class has code, which is what expected
        # and readBody in the wrapper below reads the response_text prop
        deferred.callback(this)
        return MockHttpResponseWrapper(deferred)

class MockHttpResponseWrapper:
    status_code = 200
    text = None

    def __init__(self, deferred):
        self.deferred = deferred

    async def read_body(self, response):
        http_wrapper = await self.deferred
        return http_wrapper.response_text

class TestWebpushPushkin(WebpushPushkin):
    def __init__(self, name, sygnal, config, code, response_text):
        super().__init__(name, sygnal, config)
        self.http_agent_wrapper = MockHttpWrapper()

class WebPushTestCase(testutils.TestCase):
    def config_setup(self, config):
        super(WebPushTestCase, self).config_setup(config)
        config["apps"][PUSHKIN_ID] = {
            "type": "tests.test_webpush.TestWebpushPushkin",
            "vapid_private_key": VAPID_PRIVATE_KEY,
            "vapid_contact_email": VAPID_EMAIL
        }

    def test_happypath(self):
        pushkin = self.sygnal.pushkins[PUSHKIN_ID]
        # Arrange
        pushkin.http_agent_wrapper.code = 201
        pushkin.http_agent_wrapper.response_text = ""
        # Act
        self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        # Assert
        print(pushkin.http_agent_wrapper.url)
        print(pushkin.http_agent_wrapper.headers)
