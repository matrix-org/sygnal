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
from twisted.internet import defer
from sygnal import apnstruncate

from tests import testutils

from py_vapid import Vapid, VapidException

PUSHKIN_ID = "com.example.webpush"
VAPID_EMAIL = "alice@server.tld"
# need to go one dir up because the tests are run in a temporary sub directory
VAPID_PRIVATE_KEY = "../tests/webpush/private_key.pem"
DEVICE1_EXAMPLE = {
    "app_id": PUSHKIN_ID,
    "kind": "http",
    "data": {
        "endpoint": "https://some.push.gateway.com/endpoint",
        "auth": "tk-uFizVuguwlVdI6lXrKA",
        "default_payload": {"session_id": "7192604822299679"},
    },
    "pushkey": "BMndGyzAWuhx4qbONDPp_pwtaA95U8c967lkUMx8LUY09WcxRzRB5WuSJox56DYZy7lx4Yt9tfuKcpyoz-KDYTA",
    "app_display_name": "Some web app",
    "device_display_name": "Some web app",
    "lang": "en",
}
DEVICE1_AUTHORIZATION = "vapid t=eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiJ9.eyJhdWQiOiJodHRwczovL3NvbWUucHVzaC5nYXRld2F5LmNvbSIsImV4cCI6MzE1NTc1NjQwMCwic3ViIjoibWFpbHRvOmFsaWNlQHNlcnZlci50bGQifQ.viywllKQrPs7HJT-rTSesFGSYMdfIseLKWV6C0_r4qO_gNg0BUTCMJJriJZPMsnl_ZwnXsejiyN19cqPLUHDkA,k=BNYtdPa5ccnu8AvoMSQVuIuBU94Z-w3aJo7u2qIV6p20b0PCiamqzckCH38yRCbTUIFzXzqIgvjbFguK9Id-0zc"
DEVICE2_EXAMPLE = {
    "app_id": PUSHKIN_ID,
    "kind": "http",
    "data": {
        "endpoint": "https://some.other.push.gateway.com/endpoint",
        "auth": "uxAHzLLdA8bQYmcso8PQHQ",
        "default_payload": {"session_id": "405552421672423"},
    },
    "pushkey": "BIGwA79vqCCxVngC4f038nezxZXL8E7ZpjbO-tb8hTxG1CaLBAUOwG5Nj8RI5eXV37kwmsQwoKXwgd9BUXjy9ws",
    "app_display_name": "Some web app",
    "device_display_name": "Some web app",
    "lang": "en",
}
DEVICE2_AUTHORIZATION = "..."


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
        # this class has code, which is what expected
        # and readBody in the wrapper below reads the response_text prop
        return MockHttpResponseWrapper(defer.succeed(self))


class MockHttpResponseWrapper:
    status_code = 200
    text = None

    def __init__(self, deferred):
        self.deferred = deferred

    async def read_body(self, response):
        http_wrapper = await self.deferred
        return http_wrapper.response_text


class TestWebpushPushkin(WebpushPushkin):
    def __init__(self, name, sygnal, config):
        super().__init__(name, sygnal, config)
        self.http_agent_wrapper = MockHttpWrapper()

    def _get_vapid_exp(self):
        # jan 1st 2070 at midnight- some time that will always be in the future
        # otherwise pywebpush will set the current time
        # as the expire value
        return 3155756400


class WebPushTestCase(testutils.TestCase):
    def config_setup(self, config):
        super(WebPushTestCase, self).config_setup(config)
        config["apps"][PUSHKIN_ID] = {
            "type": "tests.test_webpush.TestWebpushPushkin",
            "vapid_private_key": VAPID_PRIVATE_KEY,
            "vapid_contact_email": VAPID_EMAIL,
        }

    def test_device1(self):
        pushkin = self.sygnal.pushkins[PUSHKIN_ID]
        # Arrange
        pushkin.http_agent_wrapper.code = 201
        pushkin.http_agent_wrapper.response_text = ""
        # Act
        self._request(self._make_dummy_notification([DEVICE1_EXAMPLE]))
        # Assert
        # print(pushkin.http_agent_wrapper.headers.get("authorization"))
        authorization = pushkin.http_agent_wrapper.headers.get("authorization")
        self.assertEqual(authorization, DEVICE1_AUTHORIZATION)

    def test_device2(self):
        pushkin = self.sygnal.pushkins[PUSHKIN_ID]
        # Arrange
        pushkin.http_agent_wrapper.code = 201
        pushkin.http_agent_wrapper.response_text = ""
        # Act
        self._request(self._make_dummy_notification([DEVICE2_EXAMPLE]))
        # Assert
        # print(pushkin.http_agent_wrapper.headers.get("authorization"))
        authorization = pushkin.http_agent_wrapper.headers.get("authorization")
        self.assertEqual(authorization, DEVICE2_AUTHORIZATION)

    def test_device2_after_device1_has_same_result(self):
        pushkin = self.sygnal.pushkins[PUSHKIN_ID]
        # Arrange
        pushkin.http_agent_wrapper.code = 201
        pushkin.http_agent_wrapper.response_text = ""
        # First, test the first device again
        self._request(self._make_dummy_notification([DEVICE1_EXAMPLE]))
        authorization = pushkin.http_agent_wrapper.headers.get("authorization")
        self.assertEqual(authorization, DEVICE1_AUTHORIZATION)
        # now with the same pushkin, do a second request and test that the result
        # is the same as the authorization header for test_device2 to ensure
        # nothing leaks from one request to another
        self._request(self._make_dummy_notification([DEVICE2_EXAMPLE]))
        authorization = pushkin.http_agent_wrapper.headers.get("authorization")
        self.assertEqual(authorization, DEVICE2_AUTHORIZATION)
