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

from twisted.internet.address import IPv6Address
from twisted.internet.testing import StringTransport

from sygnal.exceptions import (
    NotificationDispatchException,
    TemporaryNotificationDispatchException,
)
from sygnal.notifications import Pushkin

from tests import testutils

DEVICE_RAISE_EXCEPTION = {
    "app_id": "com.example.spqr",
    "pushkey": "raise_exception",
    "pushkey_ts": 1234,
}

DEVICE_REMOTE_ERROR = {
    "app_id": "com.example.spqr",
    "pushkey": "remote_error",
    "pushkey_ts": 1234,
}

DEVICE_TEMPORARY_REMOTE_ERROR = {
    "app_id": "com.example.spqr",
    "pushkey": "temporary_remote_error",
    "pushkey_ts": 1234,
}

DEVICE_REJECTED = {
    "app_id": "com.example.spqr",
    "pushkey": "reject",
    "pushkey_ts": 1234,
}

DEVICE_ACCEPTED = {
    "app_id": "com.example.spqr",
    "pushkey": "accept",
    "pushkey_ts": 1234,
}


class TestPushkin(Pushkin):
    """
    A synthetic Pushkin with simple rules.
    """

    async def dispatch_notification(self, n, device, context):
        if device.pushkey == "raise_exception":
            raise Exception("Bad things have occurred!")
        elif device.pushkey == "remote_error":
            raise NotificationDispatchException("Synthetic failure")
        elif device.pushkey == "temporary_remote_error":
            raise TemporaryNotificationDispatchException("Synthetic failure")
        elif device.pushkey == "reject":
            return [device.pushkey]
        elif device.pushkey == "accept":
            return []
        raise Exception(f"Unexpected fall-through. {device.pushkey}")


class PushGatewayApiV1TestCase(testutils.TestCase):
    def config_setup(self, config):
        """
        Set up a TestPushkin for the test.
        """
        super(PushGatewayApiV1TestCase, self).config_setup(config)
        config["apps"]["com.example.spqr"] = {
            "type": "tests.test_pushgateway_api_v1.TestPushkin"
        }

    def test_good_requests_give_200(self):
        """
        Test that good requests give a 200 response code.
        """
        # 200 codes cause the result to be parsed instead of returning the code
        self.assertNot(
            isinstance(
                self._request(
                    self._make_dummy_notification([DEVICE_ACCEPTED, DEVICE_REJECTED])
                ),
                int,
            )
        )

    def test_accepted_devices_are_not_rejected(self):
        """
        Test that devices which are accepted by the Pushkin
        do not lead to a rejection being returned to the homeserver.
        """
        self.assertEqual(
            self._request(self._make_dummy_notification([DEVICE_ACCEPTED])),
            {"rejected": []},
        )

    def test_rejected_devices_are_rejected(self):
        """
        Test that devices which are rejected by the Pushkin
        DO lead to a rejection being returned to the homeserver.
        """
        self.assertEqual(
            self._request(self._make_dummy_notification([DEVICE_REJECTED])),
            {"rejected": [DEVICE_REJECTED["pushkey"]]},
        )

    def test_only_rejected_devices_are_rejected(self):
        """
        Test that devices which are rejected by the Pushkin
        are the only ones to have a rejection returned to the homeserver,
        even if other devices feature in the request.
        """
        self.assertEqual(
            self._request(
                self._make_dummy_notification([DEVICE_REJECTED, DEVICE_ACCEPTED])
            ),
            {"rejected": [DEVICE_REJECTED["pushkey"]]},
        )

    def test_bad_requests_give_400(self):
        """
        Test that bad requests lead to a 400 Bad Request response.
        """
        self.assertEqual(self._request({}), 400)

    def test_exceptions_give_500(self):
        """
        Test that internal exceptions/errors lead to a 500 Internal Server Error
        response.
        """

        self.assertEqual(
            self._request(self._make_dummy_notification([DEVICE_RAISE_EXCEPTION])), 500
        )

        # we also check that a successful device doesn't hide the exception
        self.assertEqual(
            self._request(
                self._make_dummy_notification([DEVICE_ACCEPTED, DEVICE_RAISE_EXCEPTION])
            ),
            500,
        )

        self.assertEqual(
            self._request(
                self._make_dummy_notification([DEVICE_RAISE_EXCEPTION, DEVICE_ACCEPTED])
            ),
            500,
        )

    def test_remote_errors_give_502(self):
        """
        Test that errors caused by remote services such as GCM or APNS
        lead to a 502 Bad Gateway response.
        """

        self.assertEqual(
            self._request(self._make_dummy_notification([DEVICE_REMOTE_ERROR])), 502
        )

        # we also check that a successful device doesn't hide the exception
        self.assertEqual(
            self._request(
                self._make_dummy_notification([DEVICE_ACCEPTED, DEVICE_REMOTE_ERROR])
            ),
            502,
        )

        self.assertEqual(
            self._request(
                self._make_dummy_notification([DEVICE_REMOTE_ERROR, DEVICE_ACCEPTED])
            ),
            502,
        )

    def test_overlong_requests_are_rejected(self):
        # as a control case, first send a regular request.

        # connect the site to a fake transport.
        transport = StringTransport()
        protocol = self.site.buildProtocol(IPv6Address("TCP", "::1", "2345"))
        protocol.makeConnection(transport)

        protocol.dataReceived(
            b"POST / HTTP/1.1\r\n"
            b"Connection: close\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"0\r\n"
            b"\r\n"
        )

        # we should get a 404
        self.assertRegex(transport.value().decode(), r"^HTTP/1\.1 404 ")

        # now send an oversized request
        transport = StringTransport()
        protocol = self.site.buildProtocol(IPv6Address("TCP", "::1", "2345"))
        protocol.makeConnection(transport)

        protocol.dataReceived(
            b"POST / HTTP/1.1\r\n"
            b"Connection: close\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
        )

        # we deliberately send all the data in one big chunk, to ensure that
        # twisted isn't buffering the data in the chunked transfer decoder.
        # we start with the chunk size, in hex. (We won't actually send this much)
        protocol.dataReceived(b"10000000\r\n")
        sent = 0
        while not transport.disconnected:
            self.assertLess(sent, 0x10000000, "connection did not drop")
            protocol.dataReceived(b"\0" * 1024)
            sent += 1024

        # default max upload size is 512K, so it should drop on the next buffer after
        # that.
        self.assertEqual(sent, 513 * 1024)
