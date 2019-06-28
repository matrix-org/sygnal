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
