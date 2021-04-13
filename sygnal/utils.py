# -*- coding: utf-8 -*-
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
import json
import re
from logging import LoggerAdapter

from twisted.internet.defer import Deferred


async def twisted_sleep(delay, twisted_reactor):
    """
    Creates a Deferred which will fire in a set time.
    This allows you to `await` on it and have an async analogue to
    L{time.sleep}.
    Args:
        delay: Delay in seconds
        twisted_reactor: Reactor to use for sleeping.

    Returns:
        a Deferred which fires in `delay` seconds.
    """
    deferred = Deferred()
    twisted_reactor.callLater(delay, deferred.callback, None)
    await deferred


class NotificationLoggerAdapter(LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['request_id']}] {msg}", kwargs


def glob_to_regex(glob):
    """Converts a glob to a compiled regex object.

    The regex is anchored at the beginning and end of the string.

    Args:
        glob (str)

    Returns:
        re.RegexObject
    """
    res = ""
    for c in glob:
        if c == "*":
            res = res + ".*"
        elif c == "?":
            res = res + "."
        else:
            res = res + re.escape(c)

    # \A anchors at start of string, \Z at end of string
    return re.compile(r"\A" + res + r"\Z", re.IGNORECASE)


def _reject_invalid_json(val):
    """Do not allow Infinity, -Infinity, or NaN values in JSON."""
    raise ValueError(f"Invalid JSON value: {val!r}")


# a custom JSON decoder which will reject Python extensions to JSON.
json_decoder = json.JSONDecoder(parse_constant=_reject_invalid_json)
