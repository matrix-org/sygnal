INSTALL_REQUIRES = [
    "Twisted>=19.7",
    "prometheus_client>=0.7.0,<0.8",
    "aioapns>=1.10",
    "cryptography>=2.6.1",
    "pyyaml>=5.1.1",
    "service_identity>=18.1.0",
    "jaeger-client>=4.0.0",
    "opentracing>=2.2.0",
    "sentry-sdk>=0.10.2",
    "zope.interface>=4.6.0",
    "idna>=2.8",
    "importlib_metadata",
    "pywebpush>=1.13.0",
    "py-vapid>=1.7.0",
]

EXTRAS_REQUIRE = {
    "dev": [
        "coverage~=5.5",
        "black==21.6b0",
        "flake8==3.9.0",
        "isort~=5.0",
        "mypy==0.812",
        "mypy-zope==0.3.0",
        "tox",
    ]
}
