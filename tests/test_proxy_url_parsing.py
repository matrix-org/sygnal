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

    def test_decompose_http_proxy_url_failure(self):
        # test that non-HTTP schemes raise an exception
        self.assertRaises(
            RuntimeError, lambda: decompose_http_proxy_url("ftp://example.org")
        )

        # test that the lack of a hostname raises an exception
        self.assertRaises(RuntimeError, lambda: decompose_http_proxy_url("http://"))
