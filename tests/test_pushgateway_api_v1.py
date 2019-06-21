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

from sygnal.exceptions import NotificationDispatchException, TemporaryNotificationDispatchException
from sygnal.notifications import Pushkin

from tests import testutils

DEVICE_RAISE_EXCEPTION = {
    "app_id": "com.example.spqr", "pushkey": "raise_exception", "pushkey_ts": 1234,
}

DEVICE_REMOTE_ERROR = {
    "app_id": "com.example.spqr", "pushkey": "remote_error", "pushkey_ts": 1234,
}

DEVICE_TEMPORARY_REMOTE_ERROR = {
    "app_id": "com.example.spqr", "pushkey": "temporary_remote_error", "pushkey_ts": 1234,
}

DEVICE_REJECTED = {
    "app_id": "com.example.spqr", "pushkey": "reject", "pushkey_ts": 1234,
}

DEVICE_ACCEPTED = {
    "app_id": "com.example.spqr", "pushkey": "accept", "pushkey_ts": 1234,
}


class TestPushkin(Pushkin):
    async def dispatch_notification(self, n, device, context):
        if device.pushkey == 'raise_exception':
            raise Exception("Bad things have occurred!")
        elif device.pushkey == 'remote_error':
            raise NotificationDispatchException("Synthetic failure")
        elif device.pushkey == 'temporary_remote_error':
            raise TemporaryNotificationDispatchException("Synthetic failure")
        elif device.pushkey == 'reject':
            return [device.pushkey]
        elif device.pushkey == 'accept':
            return []
        raise Exception(f"Unexpected fall-through. {device.pushkey}")


class PushGatewayApiV1TestCase(testutils.TestCase):

    def config_setup(self, config):
        super(PushGatewayApiV1TestCase, self).config_setup(config)
        config['apps']['com.example.spqr.type'] = 'tests.test_pushgateway_api_v1.TestPushkin'

    def test_good_requests_give_200(self):
        # 200 codes cause the result to be parsed instead of returning the code
        self.assertNot(isinstance(self._request(self._make_dummy_notification([
            DEVICE_ACCEPTED, DEVICE_REJECTED
        ])), int))

    def test_accepted_devices_are_not_rejected(self):
        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_ACCEPTED
        ])), {'rejected': []})

    def test_rejected_devices_are_rejected(self):
        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_REJECTED
        ])), {'rejected': [DEVICE_REJECTED['pushkey']]})

    def test_only_rejected_devices_are_rejected(self):
        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_REJECTED, DEVICE_ACCEPTED
        ])), {'rejected': [DEVICE_REJECTED['pushkey']]})

    def test_bad_requests_give_400(self):
        # TODO further needed
        self.assertEquals(self._request({}), 400)

    def test_exceptions_give_500(self):
        # TODO further needed

        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_RAISE_EXCEPTION
        ])), 500)

        # we also check that a successful device doesn't hide the exception
        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_ACCEPTED, DEVICE_RAISE_EXCEPTION
        ])), 500)

        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_RAISE_EXCEPTION, DEVICE_ACCEPTED
        ])), 500)

    def test_remote_errors_give_502(self):
        # TODO further needed

        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_REMOTE_ERROR
        ])), 502)

        # we also check that a successful device doesn't hide the exception
        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_ACCEPTED, DEVICE_REMOTE_ERROR
        ])), 502)

        self.assertEquals(self._request(self._make_dummy_notification([
            DEVICE_REMOTE_ERROR, DEVICE_ACCEPTED
        ])), 502)

# Not valid. Retrying is GCM-specific.
# def test_temporary_remote_errors_cause_retries(self):
#     # TODO further needed
#
#     request = self._make_request(self._make_dummy_notification([
#         DEVICE_TEMPORARY_REMOTE_ERROR
#     ]))
#
#     self.assertNot(request.finished > 0)
#
#     resource = self.v1api.site.getResourceFor(request)
#     rendered = resource.render(request)
#
#     self.assertEquals(rendered, NOT_DONE_YET)
#
#     self.assertNot(request.finished > 0)
#     self.sygnal.reactor.advanceClock(10)
#     self.assertNot(request.finished > 0)
#     self.sygnal.reactor.advanceClock(1000)
#     self.assertTrue(request.finished > 0)
#
#     self.assertEquals(self._collect_request(request, rendered), 502)
