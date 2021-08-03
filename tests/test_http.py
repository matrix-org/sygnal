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
from unittest.mock import MagicMock, patch

from aioapns.common import NotificationResult

from tests import testutils

PUSHKIN_ID_1 = "com.example.apns"
PUSHKIN_ID_2 = "*.example.*"
PUSHKIN_ID_3 = "com.example.a*"

TEST_CERTFILE_PATH = "/path/to/my/certfile.pem"

# Specific app id
DEVICE_EXAMPLE_SPECIFIC = {
    "app_id": "com.example.apns",
    "pushkey": "spqr",
    "pushkey_ts": 42,
}

# Only one time matching app id (with PUSHKIN_ID_2)
DEVICE_EXAMPLE_MATCHING = {
    "app_id": "com.example.bpns",
    "pushkey": "spqr",
    "pushkey_ts": 42,
}

# More than one times matching app id (with PUSHKIN_ID_2 and PUSHKIN_ID_3)
DEVICE_EXAMPLE_AMBIGIOUS = {
    "app_id": "com.example.apns2",
    "pushkey": "spqr",
    "pushkey_ts": 42,
}


class HttpTestCase(testutils.TestCase):
    def setUp(self):
        self.apns_mock_class = patch("sygnal.apnspushkin.APNs").start()
        self.apns_mock = MagicMock()
        self.apns_mock_class.return_value = self.apns_mock

        # pretend our certificate exists
        patch("os.path.exists", lambda x: x == TEST_CERTFILE_PATH).start()
        # Since no certificate exists, don't try to read it.
        patch("sygnal.apnspushkin.ApnsPushkin._report_certificate_expiration").start()
        self.addCleanup(patch.stopall)

        super(HttpTestCase, self).setUp()

        self.apns_pushkin_snotif = MagicMock()
        for key, value in self.sygnal.pushkins.items():
            value._send_notification = self.apns_pushkin_snotif

    def config_setup(self, config):
        super(HttpTestCase, self).config_setup(config)
        config["apps"][PUSHKIN_ID_1] = {"type": "apns", "certfile": TEST_CERTFILE_PATH}
        config["apps"][PUSHKIN_ID_2] = {"type": "apns", "certfile": TEST_CERTFILE_PATH}
        config["apps"][PUSHKIN_ID_3] = {"type": "apns", "certfile": TEST_CERTFILE_PATH}

    def test_with_specific_appid(self):
        """
        Tests the expected case: A specific app id must be processed.
        """
        # Arrange
        method = self.apns_pushkin_snotif
        method.side_effect = testutils.make_async_magic_mock(
            NotificationResult("notID", "200")
        )

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE_SPECIFIC]))

        # Assert
        # method should be called one time
        self.assertEqual(1, method.call_count)

        self.assertEqual({"rejected": []}, resp)

    def test_with_matching_appid(self):
        """
        Tests the matching case: A matching app id (only one time) must be processed.
        """
        # Arrange
        method = self.apns_pushkin_snotif
        method.side_effect = testutils.make_async_magic_mock(
            NotificationResult("notID", "200")
        )

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE_MATCHING]))

        # Assert
        # method should be called one time
        self.assertEqual(1, method.call_count)

        self.assertEqual({"rejected": []}, resp)

    def test_with_ambigious_appid(self):
        """
        Tests the rejection case: An ambigious app id should be rejected without
        processing.
        """
        # Arrange
        method = self.apns_pushkin_snotif

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE_AMBIGIOUS]))

        # Assert
        # must be rejected without calling the method
        self.assertEqual(0, method.call_count)
        self.assertEqual({"rejected": ["spqr"]}, resp)
