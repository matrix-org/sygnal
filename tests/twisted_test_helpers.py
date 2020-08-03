import logging
import os
import subprocess

import attr
from OpenSSL import SSL
from OpenSSL.SSL import Connection
from twisted.internet.interfaces import IOpenSSLServerConnectionCreator
from twisted.internet.ssl import Certificate, trustRootFromCertificates
from twisted.web.client import BrowserLikePolicyForHTTPS  # noqa: F401
from twisted.web.iweb import IPolicyForHTTPS  # noqa: F401
from zope.interface.declarations import implementer

logger = logging.getLogger(__name__)


@attr.s(cmp=False)
class FakeTransport(object):
    """
    A twisted.internet.interfaces.ITransport implementation which sends all its data
    straight into an IProtocol object: it exists to connect two IProtocols together.

    To use it, instantiate it with the receiving IProtocol, and then pass it to the
    sending IProtocol's makeConnection method:

        server = HTTPChannel()
        client.makeConnection(FakeTransport(server, self.reactor))

    If you want bidirectional communication, you'll need two instances.
    """

    other = attr.ib()
    """The Protocol object which will receive any data written to this transport.

    :type: twisted.internet.interfaces.IProtocol
    """

    _reactor = attr.ib()
    """Test reactor

    :type: twisted.internet.interfaces.IReactorTime
    """

    _protocol = attr.ib(default=None)
    """The Protocol which is producing data for this transport. Optional, but if set
    will get called back for connectionLost() notifications etc.
    """

    disconnecting = False
    disconnected = False
    connected = True
    buffer = attr.ib(default=b"")
    producer = attr.ib(default=None)
    autoflush = attr.ib(default=True)

    def getPeer(self):
        return None

    def getHost(self):
        return None

    def loseConnection(self, reason=None):
        if not self.disconnecting:
            logger.info("FakeTransport: loseConnection(%s)", reason)
            self.disconnecting = True
            if self._protocol:
                self._protocol.connectionLost(reason)

            # if we still have data to write, delay until that is done
            if self.buffer:
                logger.info(
                    "FakeTransport: Delaying disconnect until buffer is flushed"
                )
            else:
                self.connected = False
                self.disconnected = True

    def abortConnection(self):
        logger.info("FakeTransport: abortConnection()")

        if not self.disconnecting:
            self.disconnecting = True
            if self._protocol:
                self._protocol.connectionLost(None)

        self.disconnected = True

    def pauseProducing(self):
        if not self.producer:
            return

        self.producer.pauseProducing()

    def resumeProducing(self):
        if not self.producer:
            return
        self.producer.resumeProducing()

    def unregisterProducer(self):
        if not self.producer:
            return

        self.producer = None

    def registerProducer(self, producer, streaming):
        self.producer = producer
        self.producerStreaming = streaming

        def _produce():
            d = self.producer.resumeProducing()
            d.addCallback(lambda x: self._reactor.callLater(0.1, _produce))

        if not streaming:
            self._reactor.callLater(0.0, _produce)

    def write(self, byt):
        if self.disconnecting:
            raise Exception("Writing to disconnecting FakeTransport")

        self.buffer = self.buffer + byt

        # always actually do the write asynchronously. Some protocols (notably the
        # TLSMemoryBIOProtocol) get very confused if a read comes back while they are
        # still doing a write. Doing a callLater here breaks the cycle.
        if self.autoflush:
            self._reactor.callLater(0.0, self.flush)

    def writeSequence(self, seq):
        for x in seq:
            self.write(x)

    def flush(self, maxbytes=None):
        if not self.buffer:
            # nothing to do. Don't write empty buffers: it upsets the
            # TLSMemoryBIOProtocol
            return

        if self.disconnected:
            return

        if getattr(self.other, "transport") is None:
            # the other has no transport yet; reschedule
            if self.autoflush:
                self._reactor.callLater(0.0, self.flush)
            return

        if maxbytes is not None:
            to_write = self.buffer[:maxbytes]
        else:
            to_write = self.buffer

        logger.info("%s->%s: %s", self._protocol, self.other, to_write)

        try:
            self.other.dataReceived(to_write)
        except Exception as e:
            logger.exception("Exception writing to protocol: %s", e)
            return

        self.buffer = self.buffer[len(to_write) :]
        if self.buffer and self.autoflush:
            self._reactor.callLater(0.0, self.flush)

        if not self.buffer and self.disconnecting:
            logger.info("FakeTransport: Buffer now empty, completing disconnect")
            self.disconnected = True


def get_test_https_policy():
    """Get a test IPolicyForHTTPS which trusts the test CA cert

    Returns:
        IPolicyForHTTPS
    """
    ca_file = get_test_ca_cert_file()
    with open(ca_file) as stream:
        content = stream.read()
    cert = Certificate.loadPEM(content)
    trust_root = trustRootFromCertificates([cert])
    return BrowserLikePolicyForHTTPS(trustRoot=trust_root)


def get_test_ca_cert_file():
    """Get the path to the test CA cert

    The keypair is generated with:

        openssl genrsa -out ca.key 2048
        openssl req -new -x509 -key ca.key -days 3650 -out ca.crt \
            -subj '/CN=synapse test CA'
    """
    return os.path.join(os.path.dirname(__file__), "tls/ca.crt")


def get_test_key_file():
    """get the path to the test key

    The key file is made with:

        openssl genrsa -out server.key 2048
    """
    return os.path.join(os.path.dirname(__file__), "tls/server.key")


cert_file_count = 0

CONFIG_TEMPLATE = b"""\
[default]
basicConstraints = CA:FALSE
keyUsage=nonRepudiation, digitalSignature, keyEncipherment
subjectAltName = %(sanentries)s
"""


def create_test_cert_file(sanlist):
    """build an x509 certificate file

    Args:
        sanlist: list[bytes]: a list of subjectAltName values for the cert

    Returns:
        str: the path to the file
    """
    global cert_file_count
    csr_filename = "server.csr"
    cnf_filename = "server.%i.cnf" % (cert_file_count,)
    cert_filename = "server.%i.crt" % (cert_file_count,)
    cert_file_count += 1

    # first build a CSR
    subprocess.check_call(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            get_test_key_file(),
            "-subj",
            "/",
            "-out",
            csr_filename,
        ]
    )

    # now a config file describing the right SAN entries
    sanentries = b",".join(sanlist)
    with open(cnf_filename, "wb") as f:
        f.write(CONFIG_TEMPLATE % {b"sanentries": sanentries})

    # finally the cert
    ca_key_filename = os.path.join(os.path.dirname(__file__), "tls/ca.key")
    ca_cert_filename = get_test_ca_cert_file()
    subprocess.check_call(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            csr_filename,
            "-CA",
            ca_cert_filename,
            "-CAkey",
            ca_key_filename,
            "-set_serial",
            "1",
            "-extfile",
            cnf_filename,
            "-out",
            cert_filename,
        ]
    )

    return cert_filename


@implementer(IOpenSSLServerConnectionCreator)
class TestServerTLSConnectionFactory(object):
    """An SSL connection creator which returns connections which present a certificate
    signed by our test CA."""

    def __init__(self, sanlist):
        """
        Args:
            sanlist: list[bytes]: a list of subjectAltName values for the cert
        """
        self._cert_file = create_test_cert_file(sanlist)

    def serverConnectionForTLS(self, tlsProtocol):
        ctx = SSL.Context(SSL.TLSv1_METHOD)
        ctx.use_certificate_file(self._cert_file)
        ctx.use_privatekey_file(get_test_key_file())
        return Connection(ctx, None)
