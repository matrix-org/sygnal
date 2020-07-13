import asyncio
import logging
from asyncio import Transport
from asyncio.futures import Future
from base64 import urlsafe_b64encode
from typing import Optional, Tuple, Callable

from sygnal.exceptions import ProxyConnectError

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
        proxy_hostport: str,
        target_hostport: str,
        basic_proxy_auth: Optional[Tuple[str, str]],
    ):
        """
        Args:
            proxy_hostport (str):
                The host & port of the HTTP proxy.
                Used in the `Host` request header to the proxy.
                Examples: '127.0.3.200:8080' or `prox:8080`
            target_hostport (str):
                The host & port of the destination that the proxy should connect
                to on your behalf. Must include a port number.
                Examples: 'example.org:443'
            basic_proxy_auth ((str, str) or None):
                Pass a pair of (username, password) credentials if your HTTP
                proxy requires Proxy Basic Authentication (using a
                Proxy-Authorization: basic ... header).
        """
        self.basic_proxy_auth = basic_proxy_auth
        self.completed = False
        self.proxy_hostport = proxy_hostport
        self.target_hostport = target_hostport
        self.buffer = b""
        self.transport = None

        # This future is completed when it is safe to take back control of the
        # transport (which is also returned to indicate this).
        self.wait_for_establishment = Future()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.buffer += data
        if b"\r\n\r\n" in self.buffer:
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
                        f" whilst attempting CONNECT: {status} ({reason_phrase})"
                        "; aborting connection."
                    )

                logger.debug("Ready to switch over protocol")

                self.buffer = None
                self.wait_for_establishment.set_result(self.transport)
            except Exception as exc:
                logger.error("HTTP CONNECT failed.", exc_info=True)
                self.wait_for_establishment.set_exception(exc)

    def eof_received(self) -> Optional[bool]:
        return super().eof_received()

    def connection_made(self, transport: Transport) -> None:
        super().connection_made(transport)
        # when we get a TCP connection to the HTTP proxy, we invoke the CONNECT
        # method on it to open a tunnelled TCP connection through the proxy to
        # the other side
        transport.write(f"CONNECT {self.target_hostport} HTTP/1.1\r\n".encode())
        transport.write(f"Host: {self.proxy_hostport}\r\n".encode())
        if self.basic_proxy_auth is not None:
            # a credential pair is a urlsafe-base64-encoded pair separated by colon
            (user, password) = self.basic_proxy_auth
            encoded_credentials = urlsafe_b64encode(f"{user}:{password}".encode())
            transport.write(
                b"Proxy-Authorization: basic " + encoded_credentials + b"\r\n"
            )
        # a blank line terminates the request headers
        transport.write(b"\r\n")

        logger.debug("Initiating proxy CONNECT")

        # now we wait ...
        self.transport = transport
