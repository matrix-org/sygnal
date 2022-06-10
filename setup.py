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

import os.path
from typing import Sequence

from setuptools import find_packages, setup

INSTALL_REQUIRES = [
    "aioapns>=1.10,<2.1",
    "attrs>=19.2.0",
    "cryptography>=2.6.1",
    "idna>=2.8",
    'importlib_metadata;python_version<"3.8"',
    "jaeger-client>=4.0.0",
    "matrix-common==1.0.0",
    "opentracing>=2.2.0",
    "prometheus_client>=0.7.0,<0.8",
    "py-vapid>=1.7.0",
    "pyOpenSSL>=17.5.0",
    "pywebpush>=1.13.0",
    "pyyaml>=5.1.1",
    "sentry-sdk>=0.10.2",
    "service_identity>=18.1.0",
    "Twisted>=19.7",
    'typing-extensions>=3.7.4;python_version<"3.8"',
    "zope.interface>=4.6.0",
]

EXTRAS_REQUIRE = {
    "dev": [
        "black==22.3.0",
        "coverage~=5.5",
        "flake8==3.9.0",
        "isort~=5.0",
        "mypy==0.812",
        "mypy-zope==0.3.0",
        "towncrier",
        "tox",
        "types-opentracing>=2.4.2",
        "typing-extensions>=3.7.4",
    ]
}


def read_file(path_segments: Sequence[str]) -> str:
    """Read a file from the package.

    Params:
        path_segments: a list of strings to join to make the path.
    """
    here = os.path.abspath(os.path.dirname(__file__))
    file_path = os.path.join(here, *path_segments)
    with open(file_path) as f:
        return f.read()


if __name__ == "__main__":
    setup(
        name="matrix-sygnal",
        packages=find_packages(exclude=["tests", "tests.*"]),
        description="Reference Push Gateway for Matrix Notifications",
        use_scm_version=True,
        python_requires=">=3.7",
        setup_requires=["setuptools_scm"],
        install_requires=INSTALL_REQUIRES,
        extras_require=EXTRAS_REQUIRE,
        long_description=read_file(("README.md",)),
        long_description_content_type="text/markdown",
    )
