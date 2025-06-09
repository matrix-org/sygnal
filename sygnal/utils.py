# -*- coding: utf-8 -*-
# Copyright 2025 New Vector Ltd.
# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
# Please see LICENSE files in the repository root for full details.
#
# Originally licensed under the Apache License, Version 2.0:
# <http://www.apache.org/licenses/LICENSE-2.0>.
import json
from logging import LoggerAdapter
from typing import TYPE_CHECKING, Any, MutableMapping, Tuple

from twisted.internet.defer import Deferred

if TYPE_CHECKING:
    from sygnal.sygnal import SygnalReactor


async def twisted_sleep(delay: float, twisted_reactor: "SygnalReactor") -> None:
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
    deferred: Deferred[None] = Deferred()
    twisted_reactor.callLater(delay, deferred.callback, None)
    await deferred


class NotificationLoggerAdapter(LoggerAdapter):
    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> Tuple[str, MutableMapping[str, Any]]:
        assert self.extra
        return f"[{self.extra['request_id']}] {msg}", kwargs


def _reject_invalid_json(val: Any) -> None:
    """Do not allow Infinity, -Infinity, or NaN values in JSON."""
    raise ValueError(f"Invalid JSON value: {val!r}")


# a custom JSON decoder which will reject Python extensions to JSON.
json_decoder = json.JSONDecoder(parse_constant=_reject_invalid_json)
