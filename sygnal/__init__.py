# -*- coding: utf-8 -*-
# Copyright 2025 New Vector Ltd.
# Copyright 2025 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

import sys
from importlib.metadata import PackageNotFoundError, version
from os import environ

try:
    __version__ = version("matrix-sygnal")
except PackageNotFoundError:
    # package is not installed
    pass

if environ.get("RUN_DESPITE_UNSUPPORTED") != "Y":
    # Update your remotes folks.
    announcement = """
    Sygnal is no longer being developed under the matrix-org organization. See the
    README.md for more details.

    Please update your git remote to pull from element-hq/sygnal:

       git remote set-url origin git@github.com:element-hq/sygnal.git
    """
    print(announcement)
    sys.exit(1)
