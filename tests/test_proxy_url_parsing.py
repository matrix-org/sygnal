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
import unittest

from sygnal.helper.proxy import HttpProxyUrl, decompose_http_proxy_url


class ProxyUrlTestCase(unittest.TestCase):
    def test_decompose_http_proxy_url(self):
        parts = decompose_http_proxy_url("http://example.org")
        self.assertEqual(parts, HttpProxyUrl("example.org", 80, None))

        parts = decompose_http_proxy_url("http://example.org:8080")
        self.assertEqual(parts, HttpProxyUrl("example.org", 8080, None))

        parts = decompose_http_proxy_url("http://bob:secretsquirrel@example.org")
        self.assertEqual(
            parts, HttpProxyUrl("example.org", 80, ("bob", "secretsquirrel"))
        )

        parts = decompose_http_proxy_url("http://bob:secretsquirrel@example.org:8080")
        self.assertEqual(
            parts, HttpProxyUrl("example.org", 8080, ("bob", "secretsquirrel"))
        )

    def test_decompose_username_only(self):
        """
        We do not support usernames without passwords for now — this tests the
        current behaviour, though (it ignores the username).
        """

        parts = decompose_http_proxy_url("http://bob@example.org:8080")
        self.assertEqual(parts, HttpProxyUrl("example.org", 8080, None))

    def test_decompose_http_proxy_url_failure(self):
        # test that non-HTTP schemes raise an exception
        self.assertRaises(
            RuntimeError, lambda: decompose_http_proxy_url("ftp://example.org")
        )

        # test that the lack of a hostname raises an exception
        self.assertRaises(RuntimeError, lambda: decompose_http_proxy_url("http://"))
