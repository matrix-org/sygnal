import asyncio
import logging

from .notifications import Pushkin

logger = logging.getLogger(__name__)


class DummyPushkin(Pushkin):
    async def dispatchNotification(self, n, device):
        prefix = self.getConfig("prefix")
        delay = float(self.getConfig("delay"))
        logger.info(f"DUMMY: SENDING {prefix} {self.name} {n}")
        await asyncio.sleep(delay)

        rejected = not device.pushkey.startswith(prefix)

        if rejected:
            logger.info(f"DUMMY: REJECTED {prefix} {self.name} {n}")
            return [device.pushkey]
        else:
            logger.info(f"DUMMY: ACCEPTED {prefix} {self.name} {n}")
            return []
