from urllib.parse import urlparse


def decompose_http_proxy_url(proxy_url):
    url = urlparse(proxy_url, scheme="http")

    if not url.hostname:
        raise RuntimeError("Proxy URL did not contain a hostname! Please specify one.")

    if url.scheme != "http":
        raise RuntimeError(
            f"Unknown proxy scheme {url.scheme}; only 'http' is supported."
        )

    return url
