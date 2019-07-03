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

from twisted.internet.defer import Deferred, DeferredList


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


def collect_all_deferreds(deferreds):
    deferred = Deferred()
    dlist = DeferredList(deferreds, consumeErrors=True, fireOnOneErrback=True)

    def on_success(results):
        ret_val = []

        for (was_successful, result) in results:
            assert was_successful
            ret_val.append(result)

        deferred.callback(ret_val)

    dlist.addCallback(on_success)
    dlist.addErrback(deferred.errback)

    return deferred
