#!/usr/bin/env python

# Copyright 2014 OpenMarket Ltd
# Copyright 2017 Vector Creations Ltd
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

setup(
    name="matrix-sygnal",
    version=read("VERSION").strip(),
    packages=find_packages(exclude=["tests", "tests.*"]),
    description="Reference Push Gateway for Matrix Notifications",
    install_requires=[
        "flask==0.10.1",
        "gevent>=1.0.1",
        "pushbaby>=0.0.9",
        "grequests",
        "paho-mqtt",
    ],
    long_description=read("README.rst"),
)
