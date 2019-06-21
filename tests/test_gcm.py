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

from sygnal.gcmpushkin import GcmPushkin
from tests import testutils

DEVICE_EXAMPLE = {
    "app_id": "com.example.gcm", "pushkey": "spqr", "pushkey_ts": 42,
}


class TestGcmPushkin(GcmPushkin):

    def __init__(self, name, sygnal, config):
        super().__init__(name, sygnal, config)
        self.preloaded_response = None
        self.last_request = None

    def preload_with_response(self, response):
        self.preloaded_response = response

    async def _perform_http_request(self, body, headers):
        print("!!!")
        return self.preloaded_response


class GcmTestCase(testutils.TestCase):
    def config_setup(self, config):
        super(GcmTestCase, self).config_setup(config)
        config['apps']['com.example.gcm.type'] = 'tests.test_gcm.TestGcmPushkin'
        config['apps']['com.example.gcm.apikey'] = 'kii'

    def test_expected(self):
        gcm = self.sygnal.pushkins['com.example.gcm']
        gcm.preload_with_response({})

        self.assertEquals(
            self._request(self._make_dummy_notification([DEVICE_EXAMPLE])),
            {'rejected': []}
        )
