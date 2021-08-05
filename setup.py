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
from os import PathLike
from setuptools import find_packages, setup
from typing import Union

#
# Please see dependencies.py for the list of dependencies!
#

here = os.path.abspath(os.path.dirname(__file__))


def read_file(path_segments):
    """Read a file from the package. Takes a list of strings to join to
    make the path"""
    file_path = os.path.join(here, *path_segments)
    with open(file_path) as f:
        return f.read()


def exec_file(path_segments):
    """Execute a single python file to get the variables defined in it"""
    result = {}
    code = read_file(path_segments)
    exec(code, result)
    return result


dependencies = exec_file(("dependencies.py",))

setup(
    name="matrix-sygnal",
    packages=find_packages(exclude=["tests", "tests.*"]),
    description="Reference Push Gateway for Matrix Notifications",
    use_scm_version=True,
    python_requires=">=3.7",
    setup_requires=["setuptools_scm"],
    install_requires=dependencies["INSTALL_REQUIRES"],
    extras_require=dependencies["EXTRAS_REQUIRE"],
    long_description=read_file(("README.rst",)),
)
