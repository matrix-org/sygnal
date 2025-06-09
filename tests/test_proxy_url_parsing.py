# -*- coding: utf-8 -*-
# Copyright 2025 New Vector Ltd.
# Copyright 2020 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
import unittest

from sygnal.helper.proxy import HttpProxyUrl, decompose_http_proxy_url


class ProxyUrlTestCase(unittest.TestCase):
    def test_decompose_http_proxy_url(self) -> None:
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

    def test_decompose_username_only(self) -> None:
        """
        We do not support usernames without passwords for now — this tests the
        current behaviour, though (it ignores the username).
        """

        parts = decompose_http_proxy_url("http://bob@example.org:8080")
        self.assertEqual(parts, HttpProxyUrl("example.org", 8080, None))

    def test_decompose_http_proxy_url_failure(self) -> None:
        # test that non-HTTP schemes raise an exception
        self.assertRaises(
            RuntimeError, lambda: decompose_http_proxy_url("ftp://example.org")
        )

        # test that the lack of a hostname raises an exception
        self.assertRaises(RuntimeError, lambda: decompose_http_proxy_url("http://"))
