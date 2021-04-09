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
import ssl
from asyncio import AbstractEventLoop, BaseTransport, Protocol, Task
from typing import Optional, Tuple, cast

from sygnal.exceptions import ProxyConnectError
from sygnal.helper.proxy.proxy_asyncio import HttpConnectProtocol

from tests import testutils
from tests.asyncio_test_helpers import (
    EchoProtocol,
    MockProtocol,
    MockTransport,
    TimelessEventLoopWrapper,
)
from tests.twisted_test_helpers import (
    create_test_cert_file,
    get_test_ca_cert_file,
    get_test_key_file,
)


class AsyncioHttpProxyTest(testutils.TestCase):
    def config_setup(self, config):
        super().config_setup(config)
        config["apps"]["com.example.spqr"] = {
            "type": "tests.test_pushgateway_api_v1.TestPushkin"
        }
        base_loop = asyncio.new_event_loop()
        augmented_loop = TimelessEventLoopWrapper(base_loop)  # type: ignore
        asyncio.set_event_loop(cast(AbstractEventLoop, augmented_loop))

        self.loop = augmented_loop

    def make_fake_proxy(
        self, host: str, port: int, proxy_credentials: Optional[Tuple[str, str]]
    ) -> Tuple[MockProtocol, MockTransport, "Task[Tuple[BaseTransport, Protocol]]"]:
        # Task[Tuple[MockTransport, MockProtocol]]
        # make a fake proxy
        fake_proxy = MockTransport()
        # make a fake protocol that we fancy using through the proxy
        fake_protocol = MockProtocol()
        # create a HTTP CONNECT proxy client protocol
        http_connect_protocol = HttpConnectProtocol(
            target_hostport=(host, port),
            proxy_credentials=proxy_credentials,
            protocol_factory=lambda: fake_protocol,
            sslcontext=None,
            loop=None,
        )
        switch_over_task = asyncio.get_event_loop().create_task(
            http_connect_protocol.switch_over_when_ready()
        )
        # check the task is not somehow already marked as done before we even
        # receive anything.
        self.assertFalse(switch_over_task.done())
        # connect the proxy client to the proxy
        fake_proxy.set_protocol(http_connect_protocol)
        http_connect_protocol.connection_made(fake_proxy)
        return fake_protocol, fake_proxy, switch_over_task

    def test_connect_no_credentials(self):
        """
        Tests the proxy connection procedure when there is no basic auth.
        """
        host = "example.org"
        port = 443
        proxy_credentials = None
        fake_protocol, fake_proxy, switch_over_task = self.make_fake_proxy(
            host, port, proxy_credentials
        )

        # Check that the proxy got the proper CONNECT request.
        self.assertEqual(fake_proxy.buffer, b"CONNECT example.org:443 HTTP/1.0\r\n\r\n")
        # Reset the proxy mock
        fake_proxy.reset_mock()

        # pretend we got a happy response with some dangling bytes from the
        # target protocol
        fake_proxy.pretend_to_receive(
            b"HTTP/1.0 200 Connection Established\r\n\r\n"
            b"begin beep boop\r\n\r\n~~ :) ~~"
        )

        # advance event loop because we have to let coroutines be executed
        self.loop.advance(1.0)

        # *now* we should have switched over from the HTTP CONNECT protocol
        # to the user protocol (in our case, a MockProtocol).
        self.assertTrue(switch_over_task.done())

        transport, protocol = switch_over_task.result()

        # check it was our protocol that was returned
        self.assertIs(protocol, fake_protocol)

        # check our protocol received exactly the bytes meant for it
        self.assertEqual(
            fake_protocol.received_bytes, b"begin beep boop\r\n\r\n~~ :) ~~"
        )

    def test_connect_correct_credentials(self):
        """
        Tests the proxy connection procedure when there is basic auth.
        """
        host = "example.org"
        port = 443
        proxy_credentials = ("user", "secret")
        fake_protocol, fake_proxy, switch_over_task = self.make_fake_proxy(
            host, port, proxy_credentials
        )

        # Check that the proxy got the proper CONNECT request with the
        # correctly-encoded credentials
        self.assertEqual(
            fake_proxy.buffer,
            b"CONNECT example.org:443 HTTP/1.0\r\n"
            b"Proxy-Authorization: basic dXNlcjpzZWNyZXQ=\r\n\r\n",
        )
        # Reset the proxy mock
        fake_proxy.reset_mock()

        # pretend we got a happy response with some dangling bytes from the
        # target protocol
        fake_proxy.pretend_to_receive(
            b"HTTP/1.0 200 Connection Established\r\n\r\n"
            b"begin beep boop\r\n\r\n~~ :) ~~"
        )

        # advance event loop because we have to let coroutines be executed
        self.loop.advance(1.0)

        # *now* we should have switched over from the HTTP CONNECT protocol
        # to the user protocol (in our case, a MockProtocol).
        self.assertTrue(switch_over_task.done())

        transport, protocol = switch_over_task.result()

        # check it was our protocol that was returned
        self.assertIs(protocol, fake_protocol)

        # check our protocol received exactly the bytes meant for it
        self.assertEqual(
            fake_protocol.received_bytes, b"begin beep boop\r\n\r\n~~ :) ~~"
        )

    def test_connect_failure(self):
        """
        Test that our task fails properly when we cannot make a connection through
        the proxy.
        """
        host = "example.org"
        port = 443
        proxy_credentials = ("user", "secret")
        fake_protocol, fake_proxy, switch_over_task = self.make_fake_proxy(
            host, port, proxy_credentials
        )

        # Check that the proxy got the proper CONNECT request with the
        # correctly-encoded credentials.
        self.assertEqual(
            fake_proxy.buffer,
            b"CONNECT example.org:443 HTTP/1.0\r\n"
            b"Proxy-Authorization: basic dXNlcjpzZWNyZXQ=\r\n\r\n",
        )
        # Reset the proxy mock
        fake_proxy.reset_mock()

        # For the sake of this test, pretend the credentials are incorrect so
        # send a sad response with a HTML error page
        fake_proxy.pretend_to_receive(
            b"HTTP/1.0 401 Unauthorised\r\n\r\n<HTML>... some error here ...</HTML>"
        )

        # advance event loop because we have to let coroutines be executed
        self.loop.advance(1.0)

        # *now* this future should have completed
        self.assertTrue(switch_over_task.done())

        # but we should have failed
        self.assertIsInstance(switch_over_task.exception(), ProxyConnectError)

        # check our protocol did not receive anything, because it was an HTTP-
        # level error, not actually a connection to our target.
        self.assertEqual(fake_protocol.received_bytes, b"")


class AsyncioHttpProxyTLSTest(testutils.TestCase):
    """Test that using a HTTPS proxy works.

    This is a bit convoluted to try and test that we don't hit a race where the
    new client protocol can receive data before `connection_made` is called,
    which can cause problems if it tries to write to the connection that it
    hasn't been given yet.
    """

    def config_setup(self, config):
        super().config_setup(config)
        config["apps"]["com.example.spqr"] = {
            "type": "tests.test_pushgateway_api_v1.TestPushkin"
        }
        self.base_loop = asyncio.new_event_loop()
        augmented_loop = TimelessEventLoopWrapper(self.base_loop)  # type: ignore
        asyncio.set_event_loop(cast(AbstractEventLoop, augmented_loop))

        self.loop = augmented_loop

        self.proxy_context = ssl.create_default_context()
        self.proxy_context.load_verify_locations(get_test_ca_cert_file())
        self.proxy_context.set_ciphers("DEFAULT")

    def make_fake_proxy(
        self,
        host: str,
        port: int,
        proxy_credentials: Optional[Tuple[str, str]],
    ) -> Tuple[EchoProtocol, MockTransport, "Task[Tuple[BaseTransport, Protocol]]"]:
        # Task[Tuple[MockTransport, MockProtocol]]

        # make a fake proxy
        fake_proxy = MockTransport()

        # We connect with an echo protocol to test that we can always write when
        # we receive data.
        fake_protocol = EchoProtocol()

        # create a HTTP CONNECT proxy client protocol
        http_connect_protocol = HttpConnectProtocol(
            target_hostport=(host, port),
            proxy_credentials=proxy_credentials,
            protocol_factory=lambda: fake_protocol,
            sslcontext=self.proxy_context,
            loop=None,
        )
        switch_over_task = self.loop.create_task(
            http_connect_protocol.switch_over_when_ready()
        )
        # check the task is not somehow already marked as done before we even
        # receive anything.
        self.assertFalse(switch_over_task.done())
        # connect the proxy client to the proxy
        fake_proxy.set_protocol(http_connect_protocol)
        http_connect_protocol.connection_made(fake_proxy)
        return fake_protocol, fake_proxy, switch_over_task

    def test_connect_no_credentials(self):
        """
        Tests the proxy connection procedure when there is no basic auth.
        """
        host = "example.org"
        port = 443
        proxy_credentials = None
        fake_protocol, fake_proxy, switch_over_task = self.make_fake_proxy(
            host, port, proxy_credentials
        )

        # Check that the proxy got the proper CONNECT request.
        self.assertEqual(fake_proxy.buffer, b"CONNECT example.org:443 HTTP/1.0\r\n\r\n")
        # Reset the proxy mock
        fake_proxy.reset_mock()

        # pretend we got a happy response
        fake_proxy.pretend_to_receive(b"HTTP/1.0 200 Connection Established\r\n\r\n")

        # Since we're talking TLS we need to create a server TLS connection that
        # we can use to talk to each other.
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(
            create_test_cert_file([b"DNS:example.org"]), keyfile=get_test_key_file()
        )
        context.set_ciphers("DEFAULT")

        # Note that we have to use a different event loop wrapper here as we
        # want that server side setup to finish before the client side setup, so
        # that we can trigger any races.
        server_loop = TimelessEventLoopWrapper(self.base_loop)  # type: ignore
        server_transport = MockTransport()
        proxy_ft = server_loop.create_task(
            server_loop.start_tls(
                server_transport,
                MockProtocol(),
                context,
                server_hostname=host,
                server_side=True,
            )
        )

        # Advance event loop because we have to let coroutines be executed
        self.loop.advance(1.0)
        server_loop.advance(1.0)

        # We manually copy the bytes between the fake_proxy transport and our
        # created TLS transport. We do this for each step in the TLS handshake.

        # Client -> Server
        server_transport.pretend_to_receive(fake_proxy.buffer)
        fake_proxy.buffer = b""

        # Server -> Client
        fake_proxy.pretend_to_receive(server_transport.buffer)
        server_transport.buffer = b""

        # Client -> Server
        server_transport.pretend_to_receive(fake_proxy.buffer)
        fake_proxy.buffer = b""

        # We *only* advance the server side loop so that we can send data before
        # the client has called `connection_made` on the new protocol.
        server_loop.advance(0.1)

        # Server -> Client application data.
        server_plain_transport = proxy_ft.result()
        server_plain_transport.write(b"begin beep boop\r\n\r\n~~ :) ~~")
        fake_proxy.pretend_to_receive(server_transport.buffer)
        server_transport.buffer = b""

        self.loop.advance(1.0)

        # *now* we should have switched over from the HTTP CONNECT protocol
        # to the user protocol (in our case, a MockProtocol).
        self.assertTrue(switch_over_task.done())

        transport, protocol = switch_over_task.result()

        # check it was our protocol that was returned
        self.assertIs(protocol, fake_protocol)

        # check our protocol received exactly the bytes meant for it
        self.assertEqual(
            fake_protocol.received_bytes, b"begin beep boop\r\n\r\n~~ :) ~~"
        )
