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
import tempfile
from typing import TYPE_CHECKING, Any, AnyStr, Dict, List, Tuple
from unittest.mock import MagicMock

from sygnal.exceptions import TemporaryNotificationDispatchException
from sygnal.gcmpushkin import APIVersion, GcmPushkin

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


class TestCredentials:
    def __init__(self) -> None:
        self.valid = False

    @property
    def token(self) -> str:
        if self.valid:
            return "myaccesstoken"
        else:
            raise Exception()

    async def refresh(self, request: Any) -> None:
        self.valid = True


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
        if self.api_version is APIVersion.V1:
            self.credentials = TestCredentials()  # type: ignore[assignment]

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

    async def _refresh_credentials(self) -> None:
        assert self.credentials is not None
        if not self.credentials.valid:
            await self.credentials.refresh(self.google_auth_request)


FAKE_SERVICE_ACCOUNT_FILE = b"""
{
  "type": "service_account",
  "project_id": "project_id",
  "private_key_id": "private_key_id",
  "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC0PwE6TeTHjD5R\\nY2nOw1rsTgQZ38LCR2CLtx36n+LUkgej/9b+fwC88oKIqJKjUwn43JEOhf4rbA/a\\nqo4jVoLgv754G5+7Glfarr3/rqg+AVT75x6J5DRvhIYpDXwMIUqLAAbfk3TTFNJn\\n2ctrkBF2ZP9p3mzZ3NRjU63Wbf3LBpRqs8jdFEQu8JAecG8VKV1mboJIXG3hwqFN\\nJmcpC/+sWaxB5iMgSqy0w/rGFs6ZbZF6D10XYvf40lEEk9jQIovT+QD4+6GTlroT\\nbOk8uIwxFQcwMFpXj4MktqVNSNyiuuttptIvBWcMWHlaabXrR89vqUFe1g1Jx4GL\\nCF89RrcLAgMBAAECggEAPUYZ3b8zId78JGDeTEq+8wwGeuFFbRQkrvpeN5/41Xib\\nHlZPuQ5lqtXqKBjeWKVXA4G/0icc45gFv7kxPrQfI9YrItuJLmrjKNU0g+HVEdcU\\nE9pa2Fd6t9peXUBXRixfEee9bm3LTiKK8IDqlTNRrGTjKxNQ/7MBhI6izv1vRH/x\\n8i0o1xxNdqstHZ9wBFKYO9w8UQjtfzckkBNDLkaJ/WN0BoRubmUiV1+KwAyyBr6O\\nRnnZ9Tvy8VraSNSdJhX36ai36y18/sT6PWOp99zHYuDyz89KIz1la/fT9eSoR0Jy\\nYePmTEi+9pWhvtpAkqJkRxe5IDz71JVsQ07KoVfzaQKBgQDzKKUd/0ujhv/B9MQf\\nHcwSeWu/XnQ4hlcwz8dTWQjBV8gv9l4yBj9Pra62rg/tQ7b5XKMt6lv/tWs1IpdA\\neMsySY4972VPrmggKXgCnyKckDUYydNtHAIj9buo6AV8rONaneYnGv5wpSsf3q2c\\nOZrkamRgbBkI+B2mZ2obH1oVlQKBgQC9w9HkrDMvZ5L/ilZmpsvoHNFlQwmDgNlN\\n0ej5QGID5rljRM3CcLNHdyQiKqvLA9MCpPEXb2vVJPdmquD12A7a9s0OwxB/dtOD\\nykofcTY0ZHEM1HEyYJGmdK4FvZuNU4o2/D268dePjtj1Xw3c5fs0bcDiGQMtjWlz\\n5hjBzMsyHwKBgGjrIsPcwlBfEcAo0u7yNnnKNnmuUcuJ+9kt7j3Cbwqty80WKvK+\\ny1agBIECfhDMZQkXtbk8JFIjf4y/zi+db1/VaTDEORy2jmtCOWw4KgEQIDj/7OBp\\nc2r8vupUovl2x+rzsrkw5pTIT+FCffqoyHLCjWkle2/pTzHb8Waekoo5AoGAbELk\\nYy5uwTO45Hr60fOEzzZpq/iz28dNshz4agL2KD2gNGcTcEO1tCbfgXKQsfDLmG2b\\ncgBKJ77AOl1wnDEYQIme8TYOGnojL8Pfx9Jh10AaUvR8Y/49+hYFFhdXQCiR6M69\\nNQM2NJuNYWdKVGUMjJu0+AjHDFzp9YonQ6Ffp4cCgYEAmVALALCjU9GjJymgJ0lx\\nD9LccVHMwf9NmR/sMg0XNePRbCEcMDHKdtVJ1zPGS5txuxY3sRb/tDpv7TfuitrU\\nAw0/2ooMzunaoF/HXo+C/+t+pfuqPqLK4sCCyezUlMfCcaPdwXN2FmbgsaFHfe7I\\n7sGEnS/d8wEgydMiptJEf9s=\\n-----END PRIVATE KEY-----\\n",
  "client_email": "firebase-adminsdk@project_id.iam.gserviceaccount.com",
  "client_id": "client_id",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk%40project_id.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}
"""


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
        self.service_account_file = tempfile.NamedTemporaryFile()
        self.service_account_file.write(FAKE_SERVICE_ACCOUNT_FILE)
        self.service_account_file.flush()
        config["apps"]["com.example.gcm.apiv1"] = {
            "type": "tests.test_gcm.TestGcmPushkin",
            "api_version": "v1",
            "project_id": "example_project",
            "service_account_file": self.service_account_file.name,
            "fcm_options": {
                "android": {
                    "notification": {
                        "body": {
                            "test body",
                        },
                    },
                },
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

    def tearDown(self) -> None:
        self.service_account_file.close()

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
                    "android": {
                        "notification": {
                            "body": {
                                "test body",
                            },
                        },
                        "priority": "high",
                    },
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
        assert notification_req[3] is not None
        self.assertEqual(
            notification_req[3].get("Authorization"), ["Bearer myaccesstoken"]
        )

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

    def test_api_v1_retry(self) -> None:
        """
        Tests that a Firebase response of 502 results in Sygnal retrying.
        Also checks the notification message to ensure it is sane after retrying
        multiple times.
        """
        self.gcm_pushkin_snotif = MagicMock()

        gcm = self.get_test_pushkin("com.example.gcm.apiv1")

        # type safety: using ignore here due to mypy not handling monkeypatching,
        # see https://github.com/python/mypy/issues/2427
        gcm._request_dispatch = self.gcm_pushkin_snotif  # type: ignore[assignment] # noqa: E501

        async def side_effect(*_args: Any, **_kwargs: Any) -> None:
            raise TemporaryNotificationDispatchException(
                "GCM server error, hopefully temporary.", custom_retry_delay=None
            )

        method = self.gcm_pushkin_snotif
        method.side_effect = side_effect

        _resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE_APIV1]))

        self.assertEqual(3, method.call_count)
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
                    "android": {
                        "notification": {
                            "body": {
                                "test body",
                            },
                        },
                        "priority": "high",
                    },
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

    def test_api_v1_large_fields(self) -> None:
        """
        Tests the gcm pushkin truncates unusually large fields. Includes large
        fields both at the top level of `data`, and nested within `content`.
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

        resp = self._request(
            self._make_dummy_notification_large_fields([DEVICE_EXAMPLE_APIV1])
        )

        self.assertEqual(1, method.call_count)
        notification_req = method.call_args.args

        # The values for `room_name` & `content_other` should be truncated from the original.
        self.assertEqual(
            {
                "message": {
                    "data": {
                        "event_id": "$qTOWWTEL48yPm3uT-gdNhFcoHxfKbZuqRVnnWWSkGBs",
                        "type": "m.room.message",
                        "sender": "@exampleuser:matrix.org",
                        "room_name": "xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooox…",
                        "room_alias": "#exampleroom:matrix.org",
                        "membership": None,
                        "sender_display_name": "Major Tom",
                        "content_msgtype": "m.text",
                        "content_body": "I'm floating in a most peculiar way.",
                        "content_other": "xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxoooooooooo\
xxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxxooooooooooxxxxxxxxxx\
ooooooooooxxxxxxxxxx🦉oooooo£xxxxxxxx☻oo🦉…",
                        "room_id": "!slw48wfj34rtnrf:example.com",
                        "prio": "high",
                        "unread": "2",
                        "missed_calls": "1",
                    },
                    "android": {
                        "notification": {
                            "body": {
                                "test body",
                            },
                        },
                        "priority": "high",
                    },
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
        assert notification_req[3] is not None
        self.assertEqual(
            notification_req[3].get("Authorization"), ["Bearer myaccesstoken"]
        )
