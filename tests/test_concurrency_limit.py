# -*- coding: utf-8 -*-
# Copyright 2019–2020 The Matrix.org Foundation C.I.C.
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

from sygnal.notifications import ConcurrencyLimitedPushkin
from sygnal.utils import twisted_sleep

from tests.testutils import TestCase

DEVICE_GCM1_EXAMPLE = {
    "app_id": "com.example.gcm",
    "pushkey": "spqrg",
    "pushkey_ts": 42,
}
DEVICE_GCM2_EXAMPLE = {
    "app_id": "com.example.gcm",
    "pushkey": "spqrh",
    "pushkey_ts": 42,
}
DEVICE_APNS_EXAMPLE = {
    "app_id": "com.example.apns",
    "pushkey": "spqra",
    "pushkey_ts": 42,
}


class SlowConcurrencyLimitedDummyPushkin(ConcurrencyLimitedPushkin):
    async def _dispatch_notification_unlimited(self, n, device, context):
        """
        We will deliver the notification to the mighty nobody
        and we will take one second to do it, because we are slow!
        """
        await twisted_sleep(1.0, self.sygnal.reactor)
        return []


class ConcurrencyLimitTestCase(TestCase):
    def config_setup(self, config):
        super(ConcurrencyLimitTestCase, self).config_setup(config)
        config["apps"]["com.example.gcm"] = {
            "type": "tests.test_concurrency_limit.SlowConcurrencyLimitedDummyPushkin",
            "inflight_request_limit": 1,
        }
        config["apps"]["com.example.apns"] = {
            "type": "tests.test_concurrency_limit.SlowConcurrencyLimitedDummyPushkin",
            "inflight_request_limit": 1,
        }

    def test_passes_under_limit_one(self):
        """
        Tests that a push notification succeeds if it is under the limit.
        """
        resp = self._request(self._make_dummy_notification([DEVICE_GCM1_EXAMPLE]))

        self.assertEqual(resp, {"rejected": []})

    def test_passes_under_limit_multiple_no_interfere(self):
        """
        Tests that 2 push notifications succeed if they are to different
        pushkins (so do not hit a per-pushkin limit).
        """
        resp = self._request(
            self._make_dummy_notification([DEVICE_GCM1_EXAMPLE, DEVICE_APNS_EXAMPLE])
        )

        self.assertEqual(resp, {"rejected": []})

    def test_fails_when_limit_hit(self):
        """
        Tests that 1 of 2 push notifications fail if they are to the same pushkins
        (so do hit the per-pushkin limit of 1).
        """
        resp = self._multi_requests(
            [
                self._make_dummy_notification([DEVICE_GCM1_EXAMPLE]),
                self._make_dummy_notification([DEVICE_GCM2_EXAMPLE]),
            ]
        )

        # request 0 will succeed
        self.assertEqual(resp[0], {"rejected": []})

        # request 1 will fail because request 0 has filled the limit
        self.assertEqual(resp[1], 502)
