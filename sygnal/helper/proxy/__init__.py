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
from urllib.parse import ParseResult, urlparse


def decompose_http_proxy_url(proxy_url: str) -> ParseResult:
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
        The result of `urlparse` on that URL, after having checked the
        conditions mentioned above.
    """
    url = urlparse(proxy_url, scheme="http")

    if not url.hostname:
        raise RuntimeError("Proxy URL did not contain a hostname! Please specify one.")

    if url.scheme != "http":
        raise RuntimeError(
            f"Unknown proxy scheme {url.scheme}; only 'http' is supported."
        )

    return url
