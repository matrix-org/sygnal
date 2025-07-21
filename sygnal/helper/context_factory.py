# Copyright 2014-2016 OpenMarket Ltd
# Copyright 2019 New Vector Ltd
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

# Adapted from Synapse:
#  https://github.com/matrix-org/synapse/blob/1016f303e58b1305ed5b3572fde002e1273e0fc0/synapse/crypto/context_factory.py#L77


import logging

import idna
from OpenSSL import SSL
from service_identity import VerificationError
from service_identity.pyopenssl import verify_hostname, verify_ip_address
from twisted.internet.abstract import isIPAddress, isIPv6Address
from twisted.internet.interfaces import IOpenSSLClientConnectionCreator
from twisted.internet.ssl import CertificateOptions, TLSVersion, platformTrust
from twisted.protocols.tls import TLSMemoryBIOProtocol
from twisted.python.failure import Failure
from twisted.web.iweb import IPolicyForHTTPS
from zope.interface import implementer

logger = logging.getLogger(__name__)


@implementer(IPolicyForHTTPS)
class ClientTLSOptionsFactory:
    """Factory for Twisted SSLClientConnectionCreators that are used to make connections
    to remote servers for federation.
    Uses one of two OpenSSL context objects for all connections, depending on whether
    we should do SSL certificate verification.
    get_options decides whether we should do SSL certificate verification and
    constructs an SSLClientConnectionCreator factory accordingly.
    """

    def __init__(self) -> None:
        # Use CA root certs provided by OpenSSL
        trust_root = platformTrust()

        # "insecurelyLowerMinimumTo" is the argument that will go lower than
        # Twisted's default, which is why it is marked as "insecure" (since
        # Twisted's defaults are reasonably secure). But, since Twisted is
        # moving to TLS 1.2 by default, we want to respect the config option if
        # it is set to 1.0 (which the alternate option, raiseMinimumTo, will not
        # let us do).
        minTLS = TLSVersion.TLSv1_2

        self._verify_ssl = CertificateOptions(
            trustRoot=trust_root, insecurelyLowerMinimumTo=minTLS
        )
        self._verify_ssl_context = self._verify_ssl.getContext()
        self._verify_ssl_context.set_info_callback(self._context_info_cb)

    def get_options(self, host: bytes) -> IOpenSSLClientConnectionCreator:
        ssl_context = self._verify_ssl_context

        return SSLClientConnectionCreator(host, ssl_context)

    @staticmethod
    def _context_info_cb(ssl_connection: SSL.Connection, where: int, ret: int) -> None:
        """The 'information callback' for our openssl context object."""
        # we assume that the app_data on the connection object has been set to
        # a TLSMemoryBIOProtocol object. (This is done by SSLClientConnectionCreator)
        tls_protocol = ssl_connection.get_app_data()
        try:
            # ... we further assume that SSLClientConnectionCreator has set the
            # '_synapse_tls_verifier' attribute to a ConnectionVerifier object.
            tls_protocol._synapse_tls_verifier.verify_context_info_cb(
                ssl_connection, where
            )
        except:  # noqa: E722, taken from the twisted implementation
            logger.exception("Error during info_callback")
            f = Failure()
            tls_protocol.failVerification(f)

    def creatorForNetloc(
        self, hostname: bytes, port: int
    ) -> IOpenSSLClientConnectionCreator:
        """Implements the IPolicyForHTTPS interace so that this can be passed
        directly to agents.
        """
        return self.get_options(hostname)


@implementer(IOpenSSLClientConnectionCreator)
class SSLClientConnectionCreator:
    """Creates openssl connection objects for client connections.

    Replaces twisted.internet.ssl.ClientTLSOptions
    """

    def __init__(self, hostname: bytes, ctx: SSL.Context):
        self._ctx = ctx
        self._verifier = ConnectionVerifier(hostname)

    def clientConnectionForTLS(
        self, tls_protocol: TLSMemoryBIOProtocol
    ) -> SSL.Connection:
        context = self._ctx
        connection = SSL.Connection(context, None)

        # as per twisted.internet.ssl.ClientTLSOptions, we set the application
        # data to our TLSMemoryBIOProtocol...
        connection.set_app_data(tls_protocol)

        # ... and we also gut-wrench a '_synapse_tls_verifier' attribute into the
        # tls_protocol so that the SSL context's info callback has something to
        # call to do the cert verification.
        setattr(tls_protocol, "_synapse_tls_verifier", self._verifier)
        return connection


class ConnectionVerifier:
    """Set the SNI, and do cert verification

    This is a thing which is attached to the TLSMemoryBIOProtocol, and is called by
    the ssl context's info callback.
    """

    # This code is based on twisted.internet.ssl.ClientTLSOptions.

    def __init__(self, hostname: bytes):
        _decoded = hostname.decode("ascii")
        if isIPAddress(_decoded) or isIPv6Address(_decoded):
            self._hostnameBytes = hostname
            self._is_ip_address = True
        else:
            # twisted's ClientTLSOptions falls back to the stdlib impl here if
            # idna is not installed, but points out that lacks support for
            # IDNA2008 (http://bugs.python.org/issue17305).
            #
            # We can rely on having idna.
            self._hostnameBytes = idna.encode(hostname)
            self._is_ip_address = False

        self._hostnameASCII = self._hostnameBytes.decode("ascii")

    def verify_context_info_cb(
        self, ssl_connection: SSL.Connection, where: int
    ) -> None:
        if where & SSL.SSL_CB_HANDSHAKE_START and not self._is_ip_address:
            ssl_connection.set_tlsext_host_name(self._hostnameBytes)

        if where & SSL.SSL_CB_HANDSHAKE_DONE:
            try:
                if self._is_ip_address:
                    verify_ip_address(ssl_connection, self._hostnameASCII)
                else:
                    verify_hostname(ssl_connection, self._hostnameASCII)
            except VerificationError:
                f = Failure()
                tls_protocol = ssl_connection.get_app_data()
                tls_protocol.failVerification(f)
