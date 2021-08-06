#!/usr/bin/env python
#
# Copyright 2021 The Matrix.org Foundation C.I.C.
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
from typing import Any, Dict, Sequence

"""
This script outputs a list of requirements, which, if fed to pip install -r,
will install the oldest versions of the dependencies specified by the
setup.py file in the repository root.
"""


here = os.path.abspath(os.path.dirname(__file__))


def read_file(path_segments: Sequence[str]) -> str:
    """Read a file from the package.

    Params:
        path_segments: a list of strings to join to make the path.
    """
    file_path = os.path.join(here, *path_segments)
    with open(file_path) as f:
        return f.read()


def exec_file(path_segments: Sequence[str]) -> Dict[str, Any]:
    """Execute a single python file to get the variables defined in it.

    Params:
        path_segments: a list of strings to join to make the path of the
            Python file to execute.
    """
    result: Dict[str, Any] = {}
    code = read_file(path_segments)
    exec(code, result)
    return result


if __name__ == "__main__":
    dependencies = exec_file(("..", "setup.py"))
    for requirement in dependencies["INSTALL_REQUIRES"]:
        print(requirement.replace(">=", "=="))
