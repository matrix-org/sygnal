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
from typing import TYPE_CHECKING, Any, AnyStr, Dict, List, Tuple

from sygnal.gcmpushkin import GcmPushkin

from tests import testutils
from tests.testutils import DummyResponse

if TYPE_CHECKING:
    from sygnal.sygnal import Sygnal

DEVICE_EXAMPLE = {"app_id": "com.example.gcm", "pushkey": "spqr", "pushkey_ts": 42}
DEVICE_EXAMPLE2 = {"app_id": "com.example.gcm", "pushkey": "spqr2", "pushkey_ts": 42}
DEVICE_EXAMPLE_WITH_DEFAULT_PAYLOAD = {
    "app_id": "com.example.gcm",
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

DEVICE_EXAMPLE_WITH_BAD_DEFAULT_PAYLOAD = {
    "app_id": "com.example.gcm",
    "pushkey": "badpayload",
    "pushkey_ts": 42,
    "data": {
        "default_payload": None,
    },
}

DEVICE_EXAMPLE_IOS = {
    "app_id": "com.example.gcm.ios",
    "pushkey": "spqr",
    "pushkey_ts": 42,
}


class TestGcmPushkin(GcmPushkin):
    """
    A GCM pushkin with the ability to make HTTP requests removed and instead
    can be preloaded with virtual requests.
    """

    def __init__(self, name: str, sygnal: "Sygnal", config: Dict[str, Any]):
        super().__init__(name, sygnal, config)
        self.preloaded_response = DummyResponse(0)
        self.preloaded_response_payload: Dict[str, Any] = {}
        self.last_request_body: Dict[str, Any] = {}
        self.last_request_headers: Dict[AnyStr, List[AnyStr]] = {}  # type: ignore[valid-type]
        self.num_requests = 0

    def preload_with_response(
        self, code: int, response_payload: Dict[str, Any]
    ) -> None:
        """
        Preloads a fake GCM response.
        """
        self.preloaded_response = DummyResponse(code)
        self.preloaded_response_payload = response_payload

    async def _perform_http_request(  # type: ignore[override]
        self, body: Dict[str, Any], headers: Dict[AnyStr, List[AnyStr]]
    ) -> Tuple[DummyResponse, str]:
        self.last_request_body = body
        self.last_request_headers = headers
        self.num_requests += 1
        return self.preloaded_response, json.dumps(self.preloaded_response_payload)


class GcmTestCase(testutils.TestCase):
    def config_setup(self, config: Dict[str, Any]) -> None:
        config["apps"]["com.example.gcm"] = {
            "type": "tests.test_gcm.TestGcmPushkin",
            "api_key": "kii",
        }
        config["apps"]["com.example.gcm.ios"] = {
            "type": "tests.test_gcm.TestGcmPushkin",
            "api_key": "kii",
            "fcm_options": {"content_available": True, "mutable_content": True},
        }

    def get_test_pushkin(self, name: str) -> TestGcmPushkin:
        pushkin = self.sygnal.pushkins[name]
        assert isinstance(pushkin, TestGcmPushkin)
        return pushkin

    def test_expected(self) -> None:
        """
        Tests the expected case: a good response from GCM leads to a good
        response from Sygnal.
        """
        gcm = self.get_test_pushkin("com.example.gcm")
        gcm.preload_with_response(
            200, {"results": [{"message_id": "msg42", "registration_id": "spqr"}]}
        )

        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        self.assertEqual(resp, {"rejected": []})
        self.assertEqual(gcm.num_requests, 1)

    def test_expected_with_default_payload(self) -> None:
        """
        Tests the expected case: a good response from GCM leads to a good
        response from Sygnal.
        """
        gcm = self.get_test_pushkin("com.example.gcm")
        gcm.preload_with_response(
            200, {"results": [{"message_id": "msg42", "registration_id": "spqr"}]}
        )

        resp = self._request(
            self._make_dummy_notification([DEVICE_EXAMPLE_WITH_DEFAULT_PAYLOAD])
        )

        self.assertEqual(resp, {"rejected": []})
        self.assertEqual(gcm.num_requests, 1)

    def test_misformed_default_payload_rejected(self) -> None:
        """
        Tests that a non-dict default_payload is rejected.
        """
        gcm = self.get_test_pushkin("com.example.gcm")
        gcm.preload_with_response(
            200, {"results": [{"message_id": "msg42", "registration_id": "badpayload"}]}
        )

        resp = self._request(
            self._make_dummy_notification([DEVICE_EXAMPLE_WITH_BAD_DEFAULT_PAYLOAD])
        )

        self.assertEqual(resp, {"rejected": ["badpayload"]})
        self.assertEqual(gcm.num_requests, 0)

    def test_rejected(self) -> None:
        """
        Tests the rejected case: a pushkey rejected to GCM leads to Sygnal
        informing the homeserver of the rejection.
        """
        gcm = self.get_test_pushkin("com.example.gcm")
        gcm.preload_with_response(
            200, {"results": [{"registration_id": "spqr", "error": "NotRegistered"}]}
        )

        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        self.assertEqual(resp, {"rejected": ["spqr"]})
        self.assertEqual(gcm.num_requests, 1)

    def test_batching(self) -> None:
        """
        Tests that multiple GCM devices have their notification delivered to GCM
        together, instead of being delivered separately.
        """
        gcm = self.get_test_pushkin("com.example.gcm")
        gcm.preload_with_response(
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
        assert gcm.last_request_body is not None
        self.assertEqual(gcm.last_request_body["registration_ids"], ["spqr", "spqr2"])
        self.assertEqual(gcm.num_requests, 1)

    def test_batching_individual_failure(self) -> None:
        """
        Tests that multiple GCM devices have their notification delivered to GCM
        together, instead of being delivered separately,
        and that if only one device ID is rejected, then only that device is
        reported to the homeserver as rejected.
        """
        gcm = self.get_test_pushkin("com.example.gcm")
        gcm.preload_with_response(
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
        assert gcm.last_request_body is not None
        self.assertEqual(gcm.last_request_body["registration_ids"], ["spqr", "spqr2"])
        self.assertEqual(gcm.num_requests, 1)

    def test_fcm_options(self) -> None:
        """
        Tests that the config option `fcm_options` allows setting a base layer
        of options to pass to FCM, for example ones that would be needed for iOS.
        """
        gcm = self.get_test_pushkin("com.example.gcm.ios")
        gcm.preload_with_response(
            200, {"results": [{"registration_id": "spqr_new", "message_id": "msg42"}]}
        )

        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE_IOS]))

        self.assertEqual(resp, {"rejected": []})
        assert gcm.last_request_body is not None
        self.assertEqual(gcm.last_request_body["mutable_content"], True)
        self.assertEqual(gcm.last_request_body["content_available"], True)
