import asyncio
from asyncio import Transport
from base64 import urlsafe_b64encode
from typing import Optional, Tuple


class HttpConnectProtocol(asyncio.Protocol):
    """
    This is for use with asyncio's Protocol and Transport API.

    It performs the setup of a HTTP CONNECT proxy connection, then hands over
    to another asyncio.Protocol.

    For Twisted, see connectproxyclient.py instead.
    """

    def __init__(
        self,
        proxy_address: str,
        target_address: str,
        protocol_on_top: asyncio.Protocol,
        basic_proxy_auth: Optional[Tuple[str, str]],
    ):
        self.basic_proxy_auth = basic_proxy_auth
        self.completed = False
        self.proxy_address = proxy_address
        self.target_address = target_address
        self.protocol_on_top = protocol_on_top
        self.buffer = b""
        self.transport = None

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.buffer += data
        if self.buffer.endswith(b"\r\n\r\n"):
            # warning: note this won't work if the remote host talks through
            # the tunnel first.
            # (This is OK because:
            #   - in cleartext HTTP, the client sends the request before the
            #     server utters a word
            #   - in TLS, the client talks first by sending a client hello
            #   - we aren't interested in using anything other than TLS over this
            #     proxy, anyway
            # )

            # end of HTTP response headers (from the HTTP proxy) :)
            # the first line of the response headers is the Status Line
            # the response headers are terminated by a double CRLF.
            # All HTTP lines are terminated by CRLF.
            lines = self.buffer.split(b"\r\n")
            status_line = lines[0]
            # maxsplit=2 denotes the number of separators, not the № items
            # StatusLine ← HTTPVersion SP StatusCode SP ReasonPhrase
            # None of the fields may contain CRLF, and only ReasonPhrase may
            # contain SP.
            [http_version, status, reason_phrase] = status_line.split(b" ", maxsplit=2)
            print(f"from proxy: hv={http_version}, s={status}, rp={reason_phrase}")
            if status != b"200":
                # 200 Successful (aka Connection Established) is what we want
                # if it is not what we have, then we don't have a tunnel
                print(f"ERROR from HTTP Proxy: {status} ({reason_phrase})")
                self.transport.close()
                return

            print("switching over protocol")
            self.transport.set_protocol(self.protocol_on_top)
            self.buffer = None

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
            encoded_credentials = urlsafe_b64encode(f"{user}:{password}")
            transport.write(
                f"Proxy-Authorization: basic {encoded_credentials}\r\n".encode()
            )
        # a blank line terminates the request headers
        transport.write(b"\r\n")

        # now we wait ...
        self.transport = transport
