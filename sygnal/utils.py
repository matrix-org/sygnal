import twisted.internet.reactor
from twisted.internet import defer
from twisted.internet.defer import Deferred


async def twisted_sleep(delay, twisted_reactor=twisted.internet.reactor):
    deferred = Deferred()
    twisted_reactor.callLater(delay, deferred.callback, None)
    await deferred
