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
from unittest.mock import patch, MagicMock

from aioapns.common import NotificationResult

from sygnal import apnstruncate
from tests import testutils

PUSHKIN_ID = "com.example.apns"

TEST_CERTFILE_PATH = "/path/to/my/certfile.pem"

DEVICE_EXAMPLE = {"app_id": "com.example.apns", "pushkey": "spqr", "pushkey_ts": 42}


class ApnsTestCase(testutils.TestCase):
    def setUp(self):
        self.apns_mock_class = patch("sygnal.apnspushkin.APNs").start()
        self.apns_mock = MagicMock()
        self.apns_mock_class.return_value = self.apns_mock

        # pretend our certificate exists
        patch("os.path.exists", lambda x: x == TEST_CERTFILE_PATH).start()
        # Since no certificate exists, don't try to read it.
        patch("sygnal.apnspushkin.ApnsPushkin._report_certificate_expiration").start()
        self.addCleanup(patch.stopall)

        super(ApnsTestCase, self).setUp()

        self.apns_pushkin_snotif = MagicMock()
        self.sygnal.pushkins[PUSHKIN_ID]._send_notification = self.apns_pushkin_snotif

    def config_setup(self, config):
        super(ApnsTestCase, self).config_setup(config)
        config["apps"][PUSHKIN_ID] = {"type": "apns", "certfile": TEST_CERTFILE_PATH}

    def test_payload_truncation(self):
        """
        Tests that APNS message bodies will be truncated to fit the limits of
        APNS.
        """
        # Arrange
        method = self.apns_pushkin_snotif
        method.return_value = testutils.make_async_magic_mock(
            NotificationResult("notID", "200")
        )
        self.sygnal.pushkins[PUSHKIN_ID].MAX_JSON_BODY_SIZE = 200

        # Act
        self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        # Assert
        self.assertEqual(1, method.call_count)
        ((notification_req,), _kwargs) = method.call_args
        payload = notification_req.message

        self.assertLessEqual(len(apnstruncate.json_encode(payload)), 200)

    def test_payload_truncation_test_validity(self):
        """
        This tests that L{test_payload_truncation_success} is a valid test
        by showing that not limiting the truncation size would result in a
        longer message.
        """
        # Arrange
        method = self.apns_pushkin_snotif
        method.return_value = testutils.make_async_magic_mock(
            NotificationResult("notID", "200")
        )
        self.sygnal.pushkins[PUSHKIN_ID].MAX_JSON_BODY_SIZE = 4096

        # Act
        self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        # Assert
        self.assertEqual(1, method.call_count)
        ((notification_req,), _kwargs) = method.call_args
        payload = notification_req.message

        self.assertGreater(len(apnstruncate.json_encode(payload)), 200)

    def test_expected(self):
        """
        Tests the expected case: a good response from APNS means we pass on
        a good response to the homeserver.
        """
        # Arrange
        method = self.apns_pushkin_snotif
        method.side_effect = testutils.make_async_magic_mock(
            NotificationResult("notID", "200")
        )

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        # Assert
        self.assertEqual(1, method.call_count)
        ((notification_req,), _kwargs) = method.call_args

        self.assertEqual(
            {
                "room_id": "!slw48wfj34rtnrf:example.com",
                "aps": {
                    "alert": {
                        "loc-key": "MSG_FROM_USER_IN_ROOM_WITH_CONTENT",
                        "loc-args": [
                            "Major Tom",
                            "Mission Control",
                            "I'm floating in a most peculiar way.",
                        ],
                    },
                    "badge": 3,
                    "content-available": 1,
                },
            },
            notification_req.message,
        )

        self.assertEqual({"rejected": []}, resp)

    def test_rejection(self):
        """
        Tests the rejection case: a rejection response from APNS leads to us
        passing on a rejection to the homeserver.
        """
        # Arrange
        method = self.apns_pushkin_snotif
        method.side_effect = testutils.make_async_magic_mock(
            NotificationResult("notID", "410", description="Unregistered")
        )

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        # Assert
        self.assertEqual(1, method.call_count)
        self.assertEqual({"rejected": ["spqr"]}, resp)

    def test_no_retry_on_4xx(self):
        """
        Test that we don't retry when we get a 4xx error but do not mark as
        rejected.
        """
        # Arrange
        method = self.apns_pushkin_snotif
        method.side_effect = testutils.make_async_magic_mock(
            NotificationResult("notID", "429", description="TooManyRequests")
        )

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        # Assert
        self.assertEqual(1, method.call_count)
        self.assertEqual(502, resp)

    def test_retry_on_5xx(self):
        """
        Test that we DO retry when we get a 5xx error and do not mark as
        rejected.
        """
        # Arrange
        method = self.apns_pushkin_snotif
        method.side_effect = testutils.make_async_magic_mock(
            NotificationResult("notID", "503", description="ServiceUnavailable")
        )

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        # Assert
        self.assertGreater(method.call_count, 1)
        self.assertEqual(502, resp)
