# -*- coding: utf-8 -*-
# Copyright 2025 New Vector Ltd.
# Copyright 2020 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("matrix-sygnal")
except PackageNotFoundError:
    # package is not installed
    pass
