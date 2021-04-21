#!/usr/bin/env python

# Copyright 2014 OpenMarket Ltd
# Copyright 2017 Vector Creations Ltd
# Copyright 2019 The Matrix.org Foundation C.I.C.
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

import os

from setuptools import find_packages, setup


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(
    name="matrix-sygnal",
    packages=find_packages(exclude=["tests", "tests.*"]),
    description="Reference Push Gateway for Matrix Notifications",
    use_scm_version=True,
    python_requires=">=3.7",
    setup_requires=["setuptools_scm"],
    install_requires=[
        "Twisted>=19.2.1",
        "prometheus_client>=0.7.0,<0.8",
        "aioapns>=1.10",
        "cryptography>=2.1.4",
        "pyyaml>=5.1.1",
        "service_identity>=18.1.0",
        "jaeger-client>=4.0.0",
        "opentracing>=2.2.0",
        "sentry-sdk>=0.10.2",
        "zope.interface>=4.6.0",
        "idna>=2.8",
        "psycopg2>=2.8.4",
        "importlib_metadata",
        "pywebpush>=1.13.0",
        "py-vapid>=1.7.0",
    ],
    extras_require={
        "dev": [
            "coverage~=5.5",
            "black==20.8b1",
            "flake8==3.9.0",
            "isort~=5.0",
            "mypy==0.812",
            "mypy-zope==0.3.0",
            "tox",
        ]
    },
    long_description=read("README.rst"),
)
