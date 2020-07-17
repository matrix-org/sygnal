import asyncio
import logging
from asyncio import BaseTransport
from asyncio.futures import Future
from asyncio.transports import Transport
from base64 import urlsafe_b64encode
from ssl import SSLContext
from typing import Callable, Optional, Union

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
        self, proxy_url_parts, target_hostport: str,
    ):
        """
        Args:
            proxy_url_parts (ParseResult):
                The URL of the HTTP proxy after being parsed by urlparse.
                Used in the `Host` request header to the proxy, and for the
                extraction of basic authentication credentials (if required).

            target_hostport (str):
                The host & port of the destination that the proxy should connect
                to on your behalf. Must include a port number.
                Examples: 'example.org:443'
        """
        self.completed = False
        self.target_hostport = target_hostport
        self.buffer = b""
        self.transport: Transport = None  # type: ignore
        self.proxy_url_parts = proxy_url_parts

        # This future is completed when it is safe to take back control of the
        # transport (which is also returned to indicate this).
        self.wait_for_establishment: Future[Transport] = Future()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.buffer += data
        if b"\r\n\r\n" not in self.buffer:
            # we haven't finished the headers yet
            return

        # The response headers are terminated by a double CRLF.
        # NB we want want 'in' instead of 'endswith'
        #  as no guarantee error page won't come immediately.

        # warning: note this won't work if the remote host talks through
        # the tunnel first.
        # (This is OK because:
        #   - in cleartext HTTP, the client sends the request before the
        #     server utters a word
        #   - in TLS, the client talks first by sending a client hello
        #   - we aren't interested in using anything other than TLS over this
        #     proxy, anyway
        # )

        # All HTTP header lines are terminated by CRLF.
        # the first line of the response headers is the Status Line
        try:
            lines = self.buffer.split(b"\r\n")
            status_line = lines[0]
            # maxsplit=2 denotes the number of separators, not the № items
            # StatusLine ← HTTPVersion SP StatusCode SP ReasonPhrase
            # None of the fields may contain CRLF, and only ReasonPhrase may
            # contain SP.
            [http_version, status, reason_phrase] = status_line.split(
                b" ", maxsplit=2
            )
            logger.debug(
                "CONNECT response from proxy: hv=%s, r=%s, rp=%s",
                http_version,
                status,
                reason_phrase,
            )
            if status != b"200":
                # 200 Successful (aka Connection Established) is what we want
                # if it is not what we have, then we don't have a tunnel
                logger.error(
                    "Error from HTTP Proxy"
                    " whilst attempting CONNECT: %s (%s);"
                    "aborting connection.",
                    status,
                    reason_phrase,
                )
                self.transport.close()
                raise ProxyConnectError(
                    "Error from HTTP Proxy"
                    f" whilst attempting CONNECT: {status.decode()}"
                    f" ({reason_phrase.decode()}); aborting connection."
                )

            logger.debug("Ready to switch over protocol")

            self.buffer = None  # type: ignore
            self.wait_for_establishment.set_result(self.transport)
        except Exception as exc:
            logger.error("HTTP CONNECT failed.", exc_info=True)
            self.wait_for_establishment.set_exception(exc)

    def eof_received(self) -> Optional[bool]:
        return super().eof_received()

    def connection_made(self, transport: BaseTransport) -> None:
        if not isinstance(transport, Transport):
            raise ValueError("transport must be a proper Transport")

        super().connection_made(transport)
        # when we get a TCP connection to the HTTP proxy, we invoke the CONNECT
        # method on it to open a tunnelled TCP connection through the proxy to
        # the other side
        transport.write(f"CONNECT {self.target_hostport} HTTP/1.1\r\n".encode())
        parts = self.proxy_url_parts
        transport.write(f"Host: {parts.hostname}:{parts.port or 80}\r\n".encode())
        if parts.username is not None and parts.password is not None:
            # a credential pair is a urlsafe-base64-encoded pair separated by colon
            encoded_credentials = urlsafe_b64encode(
                f"{parts.username}:{parts.password}".encode()
            )
            transport.write(
                b"Proxy-Authorization: basic " + encoded_credentials + b"\r\n"
            )
        # a blank line terminates the request headers
        transport.write(b"\r\n")

        logger.debug("Initiating proxy CONNECT")

        # now we wait ...
        self.transport = transport


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

        def make_protocol():
            proxy_setup_protocol = HttpConnectProtocol(
                proxy_url_parts, f"{host}:{port}"
            )
            return proxy_setup_protocol

        # create a raw TCP connection to the proxy
        # (N.B. if we want to ever use TLS to the proxy [e.g. to protect the proxy
        # credentials], we can ask this to give us a TLS connection).
        transport, connect_protocol = await self._wrapped_loop.create_connection(
            make_protocol, proxy_url_parts.hostname, proxy_url_parts.port
        )

        assert isinstance(connect_protocol, HttpConnectProtocol)

        # wait for the HTTP Proxy CONNECT sequence to complete
        await connect_protocol.wait_for_establishment

        if ssl:
            if isinstance(ssl, SSLContext):
                sslcontext = ssl
            else:
                sslcontext = SSLContext()

            # be careful not to use the `transport` ever again after passing it
            # to start_tls — we overwrite our variable with the TLS-wrapped
            # transport to avoid that!
            transport = await self._wrapped_loop.start_tls(
                transport, connect_protocol, sslcontext
            )

        # construct the desired protocol and hand over the transport to it
        new_protocol = protocol_factory()
        # wire up transport to call `data_received` etc. on the new transport
        transport.set_protocol(new_protocol)
        # let the protocol know it has been connected to the transport
        new_protocol.connection_made(transport)
        # fulfil our contract and hand back the user's protocol and the
        # underlying transport
        return transport, new_protocol

    def __getattr__(self, item):
        """
        We use this to delegate other method calls to the real EventLoop.
        """
        return getattr(self._wrapped_loop, item)
