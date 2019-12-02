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

from setuptools import setup, find_packages


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


DEPENDENCIES = [
    "attr>=0.3.1",
    "firebase-admin>=3.2.0",
    "Twisted>=19.2.1",
    "prometheus_client>=0.7.0,<0.8",
    "aioapns>=1.7",
    "pyyaml>=5.1.1",
    "service_identity>=18.1.0",
    "zope.interface>=4.6.0",
    "idna>=2.8",
    "jaeger-client>=4.0.0",
    "opentracing>=2.2.0",
]

EXTRAS = {"sentry": ["sentry-sdk>=0.10.2"], "firebase": ["firebase-admin>=3.0.0"]}

EXTRAS_ALL = []
for val in EXTRAS.values():
    EXTRAS_ALL.extend(val)

EXTRAS["all"] = list(set(EXTRAS_ALL))

setup(
    name="matrix-sygnal",
    version=read("VERSION").strip(),
    packages=find_packages(exclude=["tests", "tests.*"]),
    description="Reference Push Gateway for Matrix Notifications",
    install_requires=DEPENDENCIES,
    extras_require=EXTRAS,
)
