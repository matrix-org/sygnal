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

from sygnal.fcmpushkin import FcmPushkin

from tests import testutils
from tests.testutils import DummyResponse

DEVICE_EXAMPLE = {"app_id": "com.example.fcm", "pushkey": "spqr", "pushkey_ts": 42}
DEVICE_EXAMPLE2 = {"app_id": "com.example.fcm", "pushkey": "spqr2", "pushkey_ts": 42}
DEVICE_EXAMPLE_WITH_DEFAULT_PAYLOAD = {
    "app_id": "com.example.fcm",
    "pushkey": "spqr",
    "pushkey_ts": 42,
    "data": {
        "default_payload": {
            "aps": {
                "mutable-content": 1,
                "alert": {"loc-key": "SINGLE_UNREAD", "loc-args": []},
            }
        }
    },
}
DEVICE_EXAMPLE_IOS = {
    "app_id": "com.example.fcm.ios",
    "pushkey": "spqr",
    "pushkey_ts": 42,
}


class TestFcmPushkin(FcmPushkin):
    """
    A FCM pushkin with the ability to make HTTP requests removed and instead
    can be preloaded with virtual requests.
    """

    def __init__(self, name, sygnal, config):
        super().__init__(name, sygnal, config)
        self.preloaded_response = None
        self.preloaded_response_payload = None
        self.last_request_body = None
        self.last_request_headers = None
        self.num_requests = 0

    def preload_with_response(self, code, response_payload):
        """
        Preloads a fake FCM response.
        """
        self.preloaded_response = DummyResponse(code)
        self.preloaded_response_payload = response_payload

    async def _perform_http_request(self, body, headers):
        self.last_request_body = body
        self.last_request_headers = headers
        self.num_requests += 1
        return self.preloaded_response, json.dumps(self.preloaded_response_payload)


class FcmTestCase(testutils.TestCase):
    def config_setup(self, config):
        config["apps"]["com.example.fcm"] = {
            "type": "tests.test_fcm.TestFcmPushkin",
            "api_key": "kii",
        }
        config["apps"]["com.example.fcm.ios"] = {
            "type": "tests.test_fcm.TestFcmPushkin",
            "api_key": "kii",
            "fcm_options": {"content_available": True, "mutable_content": True},
        }

    def test_expected(self):
        """
        Tests the expected case: a good response from FCM leads to a good
        response from Sygnal.
        """
        fcm = self.sygnal.pushkins["com.example.fcm"]
        fcm.preload_with_response(
            200, {"results": [{"message_id": "msg42", "registration_id": "spqr"}]}
        )

        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        self.assertEqual(resp, {"rejected": []})
        self.assertEqual(fcm.num_requests, 1)

    def test_expected_with_default_payload(self):
        """
        Tests the expected case: a good response from FCM leads to a good
        response from Sygnal.
        """
        fcm = self.sygnal.pushkins["com.example.fcm"]
        fcm.preload_with_response(
            200, {"results": [{"message_id": "msg42", "registration_id": "spqr"}]}
        )

        resp = self._request(
            self._make_dummy_notification([DEVICE_EXAMPLE_WITH_DEFAULT_PAYLOAD])
        )

        self.assertEqual(resp, {"rejected": []})
        self.assertEqual(fcm.num_requests, 1)

    def test_rejected(self):
        """
        Tests the rejected case: a pushkey rejected to FCM leads to Sygnal
        informing the homeserver of the rejection.
        """
        fcm = self.sygnal.pushkins["com.example.fcm"]
        fcm.preload_with_response(
            200, {"results": [{"registration_id": "spqr", "error": "NotRegistered"}]}
        )

        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        self.assertEqual(resp, {"rejected": ["spqr"]})
        self.assertEqual(fcm.num_requests, 1)

    def test_batching(self):
        """
        Tests that multiple FCM devices have their notification delivered to FCM
        together, instead of being delivered separately.
        """
        fcm = self.sygnal.pushkins["com.example.fcm"]
        fcm.preload_with_response(
            200,
            {
                "results": [
                    {"registration_id": "spqr", "message_id": "msg42"},
                    {"registration_id": "spqr2", "message_id": "msg42"},
                ]
            },
        )

        resp = self._request(
            self._make_dummy_notification([DEVICE_EXAMPLE, DEVICE_EXAMPLE2])
        )

        self.assertEqual(resp, {"rejected": []})
        self.assertEqual(fcm.last_request_body["registration_ids"], ["spqr", "spqr2"])
        self.assertEqual(fcm.num_requests, 1)

    def test_batching_individual_failure(self):
        """
        Tests that multiple FCM devices have their notification delivered to FCM
        together, instead of being delivered separately,
        and that if only one device ID is rejected, then only that device is
        reported to the homeserver as rejected.
        """
        fcm = self.sygnal.pushkins["com.example.fcm"]
        fcm.preload_with_response(
            200,
            {
                "results": [
                    {"registration_id": "spqr", "message_id": "msg42"},
                    {"registration_id": "spqr2", "error": "NotRegistered"},
                ]
            },
        )

        resp = self._request(
            self._make_dummy_notification([DEVICE_EXAMPLE, DEVICE_EXAMPLE2])
        )

        self.assertEqual(resp, {"rejected": ["spqr2"]})
        self.assertEqual(fcm.last_request_body["registration_ids"], ["spqr", "spqr2"])
        self.assertEqual(fcm.num_requests, 1)

    def test_fcm_options(self):
        """
        Tests that the config option `fcm_options` allows setting a base layer
        of options to pass to FCM, for example ones that would be needed for iOS.
        """
        fcm = self.sygnal.pushkins["com.example.fcm.ios"]
        fcm.preload_with_response(
            200, {"results": [{"registration_id": "spqr_new", "message_id": "msg42"}]}
        )

        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE_IOS]))

        self.assertEqual(resp, {"rejected": []})
        self.assertEqual(fcm.last_request_body["mutable_content"], True)
        self.assertEqual(fcm.last_request_body["content_available"], True)
