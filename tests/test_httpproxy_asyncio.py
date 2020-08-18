# -*- coding: utf-8 -*-
# Copyright 2020 The Matrix.org Foundation C.I.C.
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
import asyncio
from asyncio import AbstractEventLoop
from typing import cast

from sygnal.exceptions import ProxyConnectError
from sygnal.helper.proxy.proxy_asyncio import HttpConnectProtocol

from tests import testutils
from tests.asyncio_test_helpers import (
    MockProtocol,
    MockTransport,
    TimelessEventLoopWrapper,
)


class AsyncioHttpProxyTest(testutils.TestCase):
    def config_setup(self, config):
        super(AsyncioHttpProxyTest, self).config_setup(config)
        config["apps"]["com.example.spqr"] = {
            "type": "tests.test_pushgateway_api_v1.TestPushkin"
        }
        base_loop = asyncio.new_event_loop()
        augmented_loop = TimelessEventLoopWrapper(base_loop)  # type: ignore
        asyncio.set_event_loop(cast(AbstractEventLoop, augmented_loop))

        self.loop = augmented_loop

    def test_connect_no_credentials(self):
        """
        Tests the proxy connection procedure when there is no basic auth.
        """
        # make a fake proxy
        fake_proxy = MockTransport()
        # make a fake protocol that we fancy using through the proxy
        fake_protocol = MockProtocol()

        # create a connect proxy client
        hcp = HttpConnectProtocol(
            target_hostport=("example.org", 443),
            proxy_credentials=None,
            protocol_factory=lambda: fake_protocol,
            sslcontext=None,
            loop=None,
        )

        switch_over_task = asyncio.get_event_loop().create_task(
            hcp.switch_over_when_ready()
        )

        # if it's already done, that'd be odd
        self.assertFalse(switch_over_task.done())

        # connect the proxy client to the proxy
        fake_proxy.set_protocol(hcp)
        hcp.connection_made(fake_proxy)

        # check we got sent a reasonable, uh, hardcoded, CONNECT request
        self.assertEqual(fake_proxy.buffer, b"CONNECT example.org:443 HTTP/1.0\r\n\r\n")
        # clear
        fake_proxy.buffer = b""

        # pretend we got a happy response with some dangling bytes from the
        # target protocol
        fake_proxy.pretend_to_receive(
            b"HTTP/1.0 200 Connection Established\r\n\r\n"
            b"begin beep boop\r\n\r\n~~ :) ~~"
        )

        # advance reactor because we have to let coroutines be executed
        self.loop.advance(1.0)

        # *now* we should have switched over!
        self.assertTrue(switch_over_task.done())

        transport, protocol = switch_over_task.result()

        # check it was our protocol that was returned
        self.assertEqual(protocol, fake_protocol)

        # check our protocol received exactly the bytes meant for it
        self.assertEqual(
            fake_protocol.received_bytes, b"begin beep boop\r\n\r\n~~ :) ~~"
        )

    def test_connect_correct_credentials(self):
        """
        Tests the proxy connection procedure when there is basic auth.
        """
        # make a fake proxy
        fake_proxy = MockTransport()
        # make a fake protocol that we fancy using through the proxy
        fake_protocol = MockProtocol()

        # create a connect proxy client
        hcp = HttpConnectProtocol(
            target_hostport=("example.org", 443),
            proxy_credentials=("user", "secret"),
            protocol_factory=lambda: fake_protocol,
            sslcontext=None,
            loop=None,
        )

        switch_over_task = asyncio.get_event_loop().create_task(
            hcp.switch_over_when_ready()
        )

        # if it's already done, that'd be odd
        self.assertFalse(switch_over_task.done())

        # connect the proxy client to the proxy
        fake_proxy.set_protocol(hcp)
        hcp.connection_made(fake_proxy)

        # check we got sent a reasonable, uh, hardcoded, CONNECT request
        # with the correctly-encoded credentials
        self.assertEqual(
            fake_proxy.buffer,
            b"CONNECT example.org:443 HTTP/1.0\r\n"
            b"Proxy-Authorization: basic dXNlcjpzZWNyZXQ=\r\n\r\n",
        )
        # clear
        fake_proxy.buffer = b""

        # pretend we got a happy response with some dangling bytes from the
        # target protocol
        fake_proxy.pretend_to_receive(
            b"HTTP/1.0 200 Connection Established\r\n\r\n"
            b"begin beep boop\r\n\r\n~~ :) ~~"
        )

        # advance reactor because we have to let coroutines be executed
        self.loop.advance(1.0)

        # *now* we should have switched over!
        self.assertTrue(switch_over_task.done())

        transport, protocol = switch_over_task.result()

        # check it was our protocol that was returned
        self.assertEqual(protocol, fake_protocol)

        # check our protocol received exactly the bytes meant for it
        self.assertEqual(
            fake_protocol.received_bytes, b"begin beep boop\r\n\r\n~~ :) ~~"
        )

    def test_connect_failure(self):
        """
        Test that our task fails properly when we cannot make a connection through
        the proxy.
        """
        # make a fake proxy
        fake_proxy = MockTransport()
        # make a fake protocol that we fancy using through the proxy
        fake_protocol = MockProtocol()

        # create a connect proxy client
        hcp = HttpConnectProtocol(
            target_hostport=("example.org", 443),
            proxy_credentials=("user", "secret"),
            protocol_factory=lambda: fake_protocol,
            sslcontext=None,
            loop=None,
        )

        switch_over_task = asyncio.get_event_loop().create_task(
            hcp.switch_over_when_ready()
        )

        # if it's already done, that'd be odd
        self.assertFalse(switch_over_task.done())

        # connect the proxy client to the proxy
        fake_proxy.set_protocol(hcp)
        hcp.connection_made(fake_proxy)

        # check we got sent a reasonable, uh, hardcoded, CONNECT request
        # with the correctly-encoded credentials
        self.assertEqual(
            fake_proxy.buffer,
            b"CONNECT example.org:443 HTTP/1.0\r\n"
            b"Proxy-Authorization: basic dXNlcjpzZWNyZXQ=\r\n\r\n",
        )
        # clear
        fake_proxy.buffer = b""

        # pretend we got a happy response with some dangling bytes from the
        # target protocol
        fake_proxy.pretend_to_receive(
            b"HTTP/1.0 401 Unauthorised\r\n\r\n" b"<HTML>... some error here ...</HTML>"
        )

        # advance reactor because we have to let coroutines be executed
        self.loop.advance(1.0)

        # *now* we should be complete
        self.assertTrue(switch_over_task.done())

        # but we should have failed
        self.assertIsInstance(switch_over_task.exception(), ProxyConnectError)

        # check our protocol did not receive anything, because it was an HTTP-
        # level error, not actually a connection to our target.
        self.assertEqual(fake_protocol.received_bytes, b"")
