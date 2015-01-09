# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
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


from . import Pushkin
from .exceptions import PushkinSetupException, NotificationDispatchException

from pushbaby import PushBaby

import logging
import base64

logger = logging.getLogger(__name__)

class ApnsPushkin(Pushkin):
    MAX_TRIES = 2
    ADDRESSES = {'prod': 'push_production', 'sandbox': 'push_sandbox'}

    def __init__(self, name):
        super(ApnsPushkin, self).__init__(name);

    def setup(self, cfg):
        self.certfile = self.getConfig('certfile')
        plaf = self.getConfig('platform')
        if not plaf or plaf == 'production':
            self.plaf = 'prod'
        elif plaf == 'sandbox':
            self.plaf = 'sandbox'
        else:
            raise PushkinSetupException("Invalid platform: %s" % plaf)
            
        self.pushbaby = PushBaby(certfile=self.certfile, platform="prod")
        self.pushbaby.on_failed_push = self.on_failed_push
        logger.info("APNS with cert file %s on %s platform", self.certfile, self.plaf)

    def dispatchNotification(self, n):
        tokens = []
        for d in n.devices:
            if 'platform' in d.data and d.data['platform'] == self.plaf:
                tokens.append(base64.b64decode(d.pushkey))
            else:
                logger.warn("Ignoring device of platform %s", d.data['platform'])

        alert = None
        if n.type == 'm.room.message':
            alert = "Message from %s." % (n.fromuser)

        if not alert:
            logger.info("Don't know how to alert for a %s", n.type)
            return

        payload = {
            "alert": {
                "body": alert
            }
        }

        logger.info("%s -> %s", alert, [base64.b64encode(t) for t in tokens])

        tries = 0
        for t in tokens:
            while tries < ApnsPushkin.MAX_TRIES:
                try:
                    res = self.pushbaby.send(payload, t)
                    break
                except:
                    logger.exception("Exception sending push")

                tries += 1
    
        if tries == ApnsPushkin.MAX_TRIES:
            raise NotificationDispatchException("Max retries exceeded")

    def on_failed_push(self, token, identifier, status):
        logger.error("Error sending push to token %s, status", token, status)

