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
import logging
from asyncio import AbstractEventLoop, BaseTransport
from asyncio.futures import Future
from asyncio.protocols import Protocol
from asyncio.transports import Transport
from base64 import urlsafe_b64encode
from ssl import Purpose, SSLContext, create_default_context
from typing import Callable, Optional, Tuple, Union

from sygnal.exceptions import ProxyConnectError
from sygnal.helper.proxy import decompose_http_proxy_url

logger = logging.getLogger(__name__)


class HttpConnectProtocol(asyncio.Protocol):
    """
    This is for use with asyncio's Protocol and Transport API.

    It performs the setup of a HTTP CONNECT proxy connection, then the calling
     code is responsible for handing over to another asyncio.Protocol.

    For Twisted, see twisted_connectproxyclient.py instead.

    The intended usage of this class is to use it in a protocol factory in
    `AbstractEventLoop.create_connection`, then await `wait_for_establishment`
    and hand the transport over to another protocol, potentially wrapping it in
    TLS with `AbstractEventLoop.start_tls`.

    Once the connection is made, the `HttpConnectProtocol` is redundant and can
    be forgotten about; the protocol stack might look like:

                before                           after
                                                 +------------------+
                                                 |  HTTP Protocol   |
                +---------------------+          +------------------+
                | HTTP Proxy Protocol |   ===>   | SSL/TLS Protocol |
                +---------------------+----------+------------------+
                |              Underlying TCP Transport             |
                +---------------------------------------------------+
        (assuming a proxied HTTPS connection is what you were after)
    """

    def __init__(
        self,
        target_hostport: Tuple[str, int],
        proxy_credentials: Optional[Tuple[str, str]],
        protocol_factory: Callable[[], Protocol],
        sslcontext: Optional[SSLContext],
        loop: Optional[AbstractEventLoop] = None,
    ):
        """
        Args:
            target_hostport:
                The host & port of the destination that the proxy should connect
                to on your behalf.
                Examples: ('example.org', 443)

            proxy_credentials:
                An optional (username, password) tuple of strings to pass to the proxy.

            protocol_factory:
                A 0-argument function which, when called, returns a Protocol
                to switch over to.

            sslcontext:
                If TLS is desired after the connection is completed, pass an
                SSLContext here, making sure it is safe for your purposes —
                see ssl.create_default_context's documentation as a starting
                point.

            loop (optional):
                An asyncio EventLoop to use; if not provided, the default will
                be used.
        """
        # set to True when we have called `switch_over_when_ready`.
        self._switch_over_called = False

        # (host, port) of the target that we want a tunnel to
        self._target_hostport = target_hostport

        # buffer for the HTTP response that comes back from the HTTP proxy
        self._response_buffer = b""

        # underlying transport
        self._transport: Transport = None  # type: ignore

        # the proxy's credentials as a string pair, or None
        self._proxy_credentials = proxy_credentials

        # function of () -> Protocol, to be called once when we switch over to
        # this protocol
        self._protocol_factory = protocol_factory

        # optional SSLContext if TLS is desired, None otherwise
        self._sslcontext = sslcontext

        # asyncio EventLoop
        self._event_loop = loop or asyncio.get_event_loop()

        # This future is completed when it is safe to take back control of the
        # transport.
        # It completes with leftover bytes for the next protocol.
        self._tunnel_established_future: Future[bytes] = Future()

    async def switch_over_when_ready(self) -> Tuple[BaseTransport, Protocol]:
        """
        Waits until we are connected to the remote (i.e. that our CONNECT
        request succeeds).
        Then constructs the requested protocol and attaches it to the transport,
        potentially wrapping it in TLS first.
        Returns:
            the transport followed by the constructed protocol that uses it
            Note: the transport may be an SSLTransport; it is not necessarily
                the same one used to communicate with the proxy directly.
        """

        if self._switch_over_called:
            raise RuntimeError(
                "Can only use `HttpConnectProtocol.switch_over_when_ready` once."
            )
        self._switch_over_called = True

        left_over_bytes = await self._tunnel_established_future
        # construct the desired protocol and hand over the transport to it
        new_protocol = self._protocol_factory()

        if self._sslcontext:
            if left_over_bytes:
                # in TLS, the client transmits first, so this is theoretically
                # unreachable
                raise RuntimeError("Left over bytes should not occur with TLS")

            # be careful not to use the `transport` ever again after passing it
            # to start_tls — we overwrite our variable with the TLS-wrapped
            # transport to avoid that!
            transport = await self._event_loop.start_tls(
                self._transport,
                new_protocol,
                self._sslcontext,
                server_hostname=self._target_hostport[0],
            )

            # start_tls does NOT call connection_made on new_protocol, so we
            # must do it ourselves
            new_protocol.connection_made(transport)
        else:
            # no wrapping required for non-TLS
            transport = self._transport
            # wire up transport to call `data_received` etc. on the new transport
            transport.set_protocol(new_protocol)
            # let the protocol know it has been connected to the transport
            new_protocol.connection_made(transport)

            if left_over_bytes:
                # pass over dangling bytes if applicable
                new_protocol.data_received(left_over_bytes)

        return transport, new_protocol

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self._response_buffer += data
        if b"\r\n\r\n" not in self._response_buffer:
            # we haven't finished the headers yet
            return

        # The response headers are terminated by a double CRLF.
        # NB we want want 'in' instead of 'endswith'
        #  as no guarantee error page (or even bytes from the target server)
        #  won't come immediately.

        # All HTTP header lines are terminated by CRLF.
        # the first line of the response headers is the Status Line
        try:
            response_header, dangling_bytes = self._response_buffer.split(
                b"\r\n\r\n", maxsplit=1
            )
            lines = response_header.split(b"\r\n")
            status_line = lines[0]
            # maxsplit=2 denotes the number of separators, not the № items
            # StatusLine ← HTTPVersion SP StatusCode SP ReasonPhrase
            # None of the fields may contain CRLF, and only ReasonPhrase may
            # contain SP.
            [http_version, status, reason_phrase] = status_line.split(b" ", maxsplit=2)
            logger.debug(
                "CONNECT response from proxy: hv=%s, r=%s, rp=%s",
                http_version,
                status,
                reason_phrase,
            )
            if status != b"200":
                # 200 Successful (aka Connection Established) is what we want
                # if it is not what we have, then we don't have a tunnel
                self._transport.close()
                raise ProxyConnectError(
                    "Error from HTTP Proxy"
                    f" whilst attempting CONNECT: {status.decode()}"
                    f" ({reason_phrase.decode()}); aborting connection."
                )

            logger.debug("Ready to switch over protocol")

            self._response_buffer = None  # type: ignore
            # TLS doesn't seem to allow the server to talk before the client begins
            # the handshake, but plain HTTP/2 seems like the server can talk first
            # and who knows what the future holds?
            # (we may wish to use other protocols or TLS might change)
            # So we must also keep the left-over bytes to hand to the next Protocol
            self._tunnel_established_future.set_result(dangling_bytes)
        except Exception as exc:
            self._tunnel_established_future.set_exception(exc)

    def connection_made(self, transport: BaseTransport) -> None:
        if not isinstance(transport, Transport):
            raise ValueError("transport must be a proper Transport")

        super().connection_made(transport)
        # when we get a TCP connection to the HTTP proxy, we invoke the CONNECT
        # method on it to open a tunnelled TCP connection through the proxy to
        # the other side
        host, port = self._target_hostport
        transport.write(f"CONNECT {host}:{port} HTTP/1.0\r\n".encode())
        if self._proxy_credentials:
            username, password = self._proxy_credentials
            # a credential pair is a urlsafe-base64-encoded pair separated by colon
            encoded_credentials = urlsafe_b64encode(f"{username}:{password}".encode())
            transport.write(
                b"Proxy-Authorization: basic " + encoded_credentials + b"\r\n"
            )
        # a blank line terminates the request headers
        transport.write(b"\r\n")

        logger.debug("Initiating proxy CONNECT")

        # now we wait ...
        self._transport = transport


class ProxyingEventLoopWrapper:
    """
    This is a wrapper for an asyncio.AbstractEventLoop which intercepts calls to
    create_connection and transparently tunnels them through an HTTP CONNECT
    proxy.
    """

    def __init__(
        self, wrapped_loop: asyncio.AbstractEventLoop, proxy_url_str: str,
    ):
        """
        Args:
            wrapped_loop:
                the underlying Event Loop to wrap
            proxy_url_str (str):
                The address of the HTTP proxy to use.
                Used to connect to the proxy, as well as in the `Host` request
                header to the proxy, and for the extraction of basic
                authentication credentials (if required).

                Examples: 'http://127.0.3.200:8080'
                       or 'http://user:secret@prox:8080'
        """
        self._wrapped_loop = wrapped_loop
        self.proxy_url_str = proxy_url_str

    async def create_connection(
        self,
        protocol_factory: Callable[[], asyncio.Protocol],
        host: str,
        port: int,
        ssl: Union[bool, SSLContext] = False,
    ):
        proxy_url_parts = decompose_http_proxy_url(self.proxy_url_str)

        sslcontext: Optional[SSLContext]

        if ssl:
            if isinstance(ssl, SSLContext):
                sslcontext = ssl
            else:
                sslcontext = create_default_context(Purpose.SERVER_AUTH)
        else:
            sslcontext = None

        def make_protocol():
            proxy_setup_protocol = HttpConnectProtocol(
                (host, port),
                proxy_url_parts.credentials,
                protocol_factory,
                sslcontext,
                loop=self._wrapped_loop,
            )
            return proxy_setup_protocol

        # enforced by decompose_http_proxy_url
        assert proxy_url_parts.hostname is not None

        # create a raw TCP connection to the proxy
        # (N.B. if we want to ever use TLS to the proxy [e.g. to protect the proxy
        # credentials], we can ask this to give us a TLS connection).

        transport, connect_protocol = await self._wrapped_loop.create_connection(
            make_protocol, proxy_url_parts.hostname, proxy_url_parts.port
        )

        assert isinstance(connect_protocol, HttpConnectProtocol)

        # wait for the HTTP Proxy CONNECT sequence to complete,
        # and get the transport (which may be an SSLTransport rather than the
        # original) and user protocol.
        transport, user_protocol = await connect_protocol.switch_over_when_ready()

        return transport, user_protocol

    def __getattr__(self, item):
        """
        We use this to delegate other method calls to the real EventLoop.
        """
        return getattr(self._wrapped_loop, item)
