# -*- coding: utf-8 -*-
# Copyright 2019-2020 The Matrix.org Foundation C.I.C.
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
# https://github.com/matrix-org/synapse/blob/6920e58136671f086536332bdd6844dff0d4b429/synapse/http/proxyagent.py

import logging
import re
from typing import Optional

from twisted.internet import defer
from twisted.internet.endpoints import HostnameEndpoint, wrapClientTLS
from twisted.internet.interfaces import IStreamClientEndpoint
from twisted.python.failure import Failure
from twisted.web.client import URI, BrowserLikePolicyForHTTPS, _AgentBase
from twisted.web.error import SchemeNotSupported
from twisted.web.iweb import IAgent
from zope.interface import implementer

from sygnal.helper.proxy import decompose_http_proxy_url
from sygnal.helper.proxy.connectproxyclient_twisted import HTTPConnectProxyEndpoint

logger = logging.getLogger(__name__)

_VALID_URI = re.compile(br"\A[\x21-\x7e]+\Z")


@implementer(IAgent)
class ProxyAgent(_AgentBase):
    """An Agent implementation which will use an HTTP proxy if one was requested

    Args:
        reactor: twisted reactor to place outgoing
            connections.

        contextFactory (IPolicyForHTTPS): A factory for TLS contexts, to control the
            verification parameters of OpenSSL.  The default is to use a
            `BrowserLikePolicyForHTTPS`, so unless you have special
            requirements you can leave this as-is.

        connectTimeout (float): The amount of time that this Agent will wait
            for the peer to accept a connection.

        bindAddress (bytes): The local address for client sockets to bind to.

        pool (HTTPConnectionPool|None): connection pool to be used. If None, a
            non-persistent pool instance will be created.
    """

    def __init__(
        self,
        reactor,
        contextFactory=BrowserLikePolicyForHTTPS(),
        connectTimeout=None,
        bindAddress=None,
        pool=None,
        proxy_url_str: Optional[str] = None,
    ):
        _AgentBase.__init__(self, reactor, pool)

        self._endpoint_kwargs = {}
        if connectTimeout is not None:
            self._endpoint_kwargs["timeout"] = connectTimeout
        if bindAddress is not None:
            self._endpoint_kwargs["bindAddress"] = bindAddress

        if proxy_url_str is not None:
            parsed_url = decompose_http_proxy_url(proxy_url_str)
            self._proxy_auth = parsed_url.credentials

            self.proxy_endpoint = HostnameEndpoint(
                reactor, parsed_url.hostname, parsed_url.port, **self._endpoint_kwargs
            )  # type: Optional[HostnameEndpoint]
        else:
            self.proxy_endpoint = None

        self._policy_for_https = contextFactory
        self._reactor = reactor

    def request(self, method, uri, headers=None, bodyProducer=None):
        """
        Issue a request to the server indicated by the given uri.

        Supports `http` and `https` schemes.

        An existing connection from the connection pool may be used or a new one may be
        created.

        See also: twisted.web.iweb.IAgent.request

        Args:
            method (bytes): The request method to use, such as `GET`, `POST`, etc

            uri (bytes): The location of the resource to request.

            headers (Headers|None): Extra headers to send with the request

            bodyProducer (IBodyProducer|None): An object which can generate bytes to
                make up the body of this request (for example, the properly encoded
                contents of a file for a file upload). Or, None if the request is to
                have no body.

        Returns:
            Deferred[IResponse]: completes when the header of the response has
                 been received (regardless of the response status code).
        """
        uri = uri.strip()
        if not _VALID_URI.match(uri):
            raise ValueError("Invalid URI {!r}".format(uri))

        parsed_uri = URI.fromBytes(uri)
        pool_key: tuple = (parsed_uri.scheme, parsed_uri.host, parsed_uri.port)
        request_path = parsed_uri.originForm

        if parsed_uri.scheme == b"http" and self.proxy_endpoint:
            # Cache *all* connections under the same key, since we are only
            # connecting to a single destination, the proxy:
            pool_key = ("http-proxy", self.proxy_endpoint)
            endpoint = self.proxy_endpoint  # type: IStreamClientEndpoint
            request_path = uri
        elif parsed_uri.scheme == b"https" and self.proxy_endpoint:
            endpoint = HTTPConnectProxyEndpoint(
                self._reactor,
                self.proxy_endpoint,
                parsed_uri.host,
                parsed_uri.port,
                self._proxy_auth,
            )
        else:
            # not using a proxy
            endpoint = HostnameEndpoint(
                self._reactor, parsed_uri.host, parsed_uri.port, **self._endpoint_kwargs
            )

        logger.debug("Requesting %s via %s", uri, endpoint)

        if parsed_uri.scheme == b"https":
            tls_connection_creator = self._policy_for_https.creatorForNetloc(
                parsed_uri.host, parsed_uri.port
            )
            endpoint = wrapClientTLS(tls_connection_creator, endpoint)
        elif parsed_uri.scheme == b"http":
            pass
        else:
            return defer.fail(
                Failure(
                    SchemeNotSupported("Unsupported scheme: %r" % (parsed_uri.scheme,))
                )
            )

        return self._requestWithEndpoint(
            pool_key, endpoint, method, parsed_uri, headers, bodyProducer, request_path
        )
