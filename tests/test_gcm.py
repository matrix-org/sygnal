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
from unittest.mock import MagicMock

from sygnal.gcmpushkin import GcmPushkin, PushkinSetupException

from tests import testutils
from tests.testutils import DummyResponse

if TYPE_CHECKING:
    from sygnal.sygnal import Sygnal

DEVICE_EXAMPLE = {"app_id": "com.example.gcm", "pushkey": "spqr", "pushkey_ts": 42}
DEVICE_EXAMPLE2 = {"app_id": "com.example.gcm", "pushkey": "spqr2", "pushkey_ts": 42}
DEVICE_EXAMPLE_APIV1 = {
    "app_id": "com.example.gcm.apiv1",
    "pushkey": "spqr",
    "pushkey_ts": 42,
}
DEVICE_EXAMPLE2_APIV1 = {
    "app_id": "com.example.gcm.apiv1",
    "pushkey": "spqr2",
    "pushkey_ts": 42,
}
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
DEVICE_EXAMPLE_APIV1_WITH_DEFAULT_PAYLOAD = {
    "app_id": "com.example.gcm.apiv1",
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
        self.preloaded_response = DummyResponse(0)
        self.preloaded_response_payload: Dict[str, Any] = {}
        self.last_request_body: Dict[str, Any] = {}
        self.last_request_headers: Dict[AnyStr, List[AnyStr]] = {}  # type: ignore[valid-type]
        self.num_requests = 0
        try:
            super().__init__(name, sygnal, config)
        except PushkinSetupException as e:
            # for FCM v1 API we get an exception because the service account file
            # does not exist, let's ignore it and move forward
            if "service_account_file" not in str(e):
                raise e

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

    async def _get_auth_header(self) -> str:
        return "token"


class GcmTestCase(testutils.TestCase):
    maxDiff = None

    def config_setup(self, config: Dict[str, Any]) -> None:
        config["apps"]["com.example.gcm"] = {
            "type": "tests.test_gcm.TestGcmPushkin",
            "api_key": "kii",
            "api_version": "legacy",
        }
        config["apps"]["com.example.gcm.ios"] = {
            "type": "tests.test_gcm.TestGcmPushkin",
            "api_key": "kii",
            "fcm_options": {"content_available": True, "mutable_content": True},
        }
        config["apps"]["com.example.gcm.apiv1"] = {
            "type": "tests.test_gcm.TestGcmPushkin",
            "api_version": "v1",
            "project_id": "example_project",
            "service_account_file": "/path/to/file.json",
            "fcm_options": {
                "apns": {
                    "payload": {
                        "aps": {
                            "content-available": 1,
                            "mutable-content": 1,
                            "alert": "",
                        },
                    },
                },
            },
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
        self.apns_pushkin_snotif = MagicMock()
        gcm = self.get_test_pushkin("com.example.gcm")
        gcm.preload_with_response(
            200, {"results": [{"message_id": "msg42", "registration_id": "spqr"}]}
        )

        # type safety: using ignore here due to mypy not handling monkeypatching,
        # see https://github.com/python/mypy/issues/2427
        gcm._request_dispatch = self.apns_pushkin_snotif  # type: ignore[assignment] # noqa: E501

        method = self.apns_pushkin_snotif
        method.side_effect = testutils.make_async_magic_mock(([], []))

        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        self.assertEqual(1, method.call_count)
        notification_req = method.call_args.args

        self.assertEqual(
            {
                "data": {
                    "event_id": "$qTOWWTEL48yPm3uT-gdNhFcoHxfKbZuqRVnnWWSkGBs",
                    "type": "m.room.message",
                    "sender": "@exampleuser:matrix.org",
                    "room_name": "Mission Control",
                    "room_alias": "#exampleroom:matrix.org",
                    "membership": None,
                    "sender_display_name": "Major Tom",
                    "content": {
                        "msgtype": "m.text",
                        "body": "I'm floating in a most peculiar way.",
                        "other": 1,
                    },
                    "room_id": "!slw48wfj34rtnrf:example.com",
                    "prio": "high",
                    "unread": 2,
                    "missed_calls": 1,
                },
                "priority": "high",
                "to": "spqr",
            },
            notification_req[2],
        )

        self.assertEqual(resp, {"rejected": []})

    def test_expected_api_v1(self) -> None:
        """
        Tests the expected case: a good response from GCM leads to a good
        response from Sygnal.
        """
        self.apns_pushkin_snotif = MagicMock()
        gcm = self.get_test_pushkin("com.example.gcm.apiv1")
        gcm.preload_with_response(
            200, {"results": [{"message_id": "msg42", "registration_id": "spqr"}]}
        )

        # type safety: using ignore here due to mypy not handling monkeypatching,
        # see https://github.com/python/mypy/issues/2427
        gcm._request_dispatch = self.apns_pushkin_snotif  # type: ignore[assignment] # noqa: E501

        method = self.apns_pushkin_snotif
        method.side_effect = testutils.make_async_magic_mock(([], []))

        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE_APIV1]))

        self.assertEqual(1, method.call_count)
        notification_req = method.call_args.args

        self.assertEqual(
            {
                "message": {
                    "data": {
                        "event_id": "$qTOWWTEL48yPm3uT-gdNhFcoHxfKbZuqRVnnWWSkGBs",
                        "type": "m.room.message",
                        "sender": "@exampleuser:matrix.org",
                        "room_name": "Mission Control",
                        "room_alias": "#exampleroom:matrix.org",
                        "membership": None,
                        "sender_display_name": "Major Tom",
                        "content_msgtype": "m.text",
                        "content_body": "I'm floating in a most peculiar way.",
                        "room_id": "!slw48wfj34rtnrf:example.com",
                        "prio": "high",
                        "unread": "2",
                        "missed_calls": "1",
                    },
                    "android": {"priority": "high"},
                    "apns": {
                        "payload": {
                            "aps": {
                                "content-available": 1,
                                "mutable-content": 1,
                                "alert": "",
                            },
                        },
                    },
                    "token": "spqr",
                }
            },
            notification_req[2],
        )

        self.assertEqual(resp, {"rejected": []})

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

    def test_expected_api_v1_with_default_payload(self) -> None:
        """
        Tests the expected case: a good response from GCM leads to a good
        response from Sygnal.
        """
        gcm = self.get_test_pushkin("com.example.gcm.apiv1")
        gcm.preload_with_response(
            200, {"results": [{"message_id": "msg42", "registration_id": "spqr"}]}
        )

        resp = self._request(
            self._make_dummy_notification([DEVICE_EXAMPLE_APIV1_WITH_DEFAULT_PAYLOAD])
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

    def test_batching_api_v1(self) -> None:
        """
        Tests that multiple GCM devices have their notification delivered to GCM
        separately, instead of being delivered together.
        """
        gcm = self.get_test_pushkin("com.example.gcm.apiv1")
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
            self._make_dummy_notification([DEVICE_EXAMPLE_APIV1, DEVICE_EXAMPLE2_APIV1])
        )

        self.assertEqual(resp, {"rejected": []})
        assert gcm.last_request_body is not None
        self.assertEqual(gcm.last_request_body["message"]["token"], "spqr2")
        self.assertEqual(gcm.num_requests, 2)

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
