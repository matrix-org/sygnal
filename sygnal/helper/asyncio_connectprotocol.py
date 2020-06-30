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

    It performs the setup of a HTTP CONNECT proxy connection, then hands over
    to another asyncio.Protocol.

    For Twisted, see twisted_connectproxyclient.py instead.
    """

    def __init__(
        self,
        proxy_address: str,
        target_address: str,
        protocol_factory: Callable[[], Tuple[asyncio.Protocol, asyncio.Protocol]],
        basic_proxy_auth: Optional[Tuple[str, str]],
    ):
        """
        Args:
            proxy_address (str):
                The address of the HTTP proxy.
                Used in the `Host` request header to the proxy.
                Examples: '127.0.3.200:8080' or `prox:8080`
            target_address (str):
                The address of the destination that the proxy should connect to
                on your behalf. Must include a port number.
                Examples: 'example.org:443'
            protocol_factory (() -> (Protocol, Protocol)):
                A factory which should return a (bottom, top) pair of Protocols,
                where the bottom and top are the bottom and top of a protocol
                stack that will replace this HTTP Connect Proxy Protocol once
                a tunnel is established.
                (They may be the same if your protocol stack is 1-tall.)
                Why? Well, you may wish to open a TLS-wrapped HTTP connection
                in this proxy connection, which means your factory will need
                to create a HttpProtocol (say) and an SSLProtocol to wrap it in.
                The stack will change according to this diagram:

                                                 +------------------+
                                                 |  HTTP Protocol   |(a)
                +---------------------+          +------------------+
                | HTTP Proxy Protocol |   ===>   | SSL/TLS Protocol |(b)
                +---------------------+----------+------------------+
                |              Underlying TCP Transport             |
                +---------------------------------------------------+

                It's clear that this HTTP Proxy Protocol needs to know about the
                SSL Protocol (b) so that it can switch the underlying transport's
                protocol over.
                However, the user of this class presumably wanted the HTTP
                Protocol (a) for a reason, so we have to pass it back somehow
                (via the `wait_for_establishment` Future in this case).

                That's why the factory needs to return both the top and bottom
                protocol of the stack (even if it is an unfortunately ugly
                design).

                Pseudo-example:
                ```
                def factory():
                    http = HttpProtocol(...)
                    ssl = SSLProtocol(app_protocol=http, ...)
                    return (ssl, http)
                ```

            basic_proxy_auth ((str, str) or None):
                Pass a pair of (username, password) credentials if your HTTP
                proxy requires Proxy Basic Authentication (using a
                Proxy-Authorization: basic ... header).
        """
        self.basic_proxy_auth = basic_proxy_auth
        self.completed = False
        self.proxy_address = proxy_address
        self.target_address = target_address
        self.protocol_factory = protocol_factory
        self.buffer = b""
        self.transport = None
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

                logger.debug("Switching over protocol")
                new_protocol, top_protocol = self.protocol_factory()
                self.transport.set_protocol(new_protocol)
                new_protocol.connection_made(self.transport)
                self.buffer = None
                self.wait_for_establishment.set_result(top_protocol)
            except Exception as exc:
                logger.error("HTTP CONNECT failed.", exc_info=True)
                self.wait_for_establishment.set_exception(exc)

    def eof_received(self) -> Optional[bool]:
        return super().eof_received()

    def connection_made(self, transport: Transport) -> None:
        super().connection_made(transport)
        # when we get a TCP connection to the HTTP proxy, we invoke the CONNECT
        # method on it to open a tunneled TCP connection through the proxy to
        # the other side
        transport.write(f"CONNECT {self.target_address} HTTP/1.1\r\n".encode())
        transport.write(f"Host: {self.proxy_address}\r\n".encode())
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
