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
from typing import NamedTuple, Optional, Tuple
from urllib.parse import urlparse

"""
    HttpProxyUrl represents a HTTP proxy URL and no more.

    hostname is a string with the pure hostname (or IP address).
    port is always an integer; a default port number used if necessary.
    credentials is None or a tuple of (username, password) strings.
"""
HttpProxyUrl = NamedTuple(
    "HttpProxyUrl",
    [("hostname", str), ("port", int), ("credentials", Optional[Tuple[str, str]])],
)


def decompose_http_proxy_url(proxy_url: str) -> HttpProxyUrl:
    """
    Given a HTTP proxy URL, breaks it down into components and checks that it
    has a hostname (otherwise it is not right useful to us trying to find a
    proxy) and asserts that the URL has the 'http' scheme as that is all we
    support.

    Args:
        proxy_url:
            The proxy URL, as a string.
            e.g. 'http://user:password@prox:8080' or just 'http://prox' or
                anything in between.

    Returns:
        A `HttpProxyUrl` namedtuple with the separate information relevant for
        connecting to a proxy.
    """
    url = urlparse(proxy_url, scheme="http")

    if not url.hostname:
        raise RuntimeError("Proxy URL did not contain a hostname! Please specify one.")

    if url.scheme != "http":
        raise RuntimeError(
            f"Unknown proxy scheme {url.scheme}; only 'http' is supported."
        )

    credentials = None
    if url.username and url.password:
        credentials = (url.username, url.password)

    return HttpProxyUrl(
        hostname=url.hostname, port=url.port or 80, credentials=credentials
    )
