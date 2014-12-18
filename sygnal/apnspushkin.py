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


from sygnal import Pushkin, PushkinSetupException

import apns_clerk

import logging
import base64
import binascii

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
            
        self.sess = apns_clerk.Session()
        logger.info("APNS with cert file %s on %s platform", self.certfile, self.plaf)

    def dispatchNotification(self, n):
        tokens = []
        for d in n.devices:
            if 'platform' in d.data and d.data['platform'] == self.plaf:
                # The apns library takes its tokens as hex strings, but Matrix
                # uses base64 encoding (because it's much more efficient and
                # the encoder is built in as of iOS7) so base64 decode and hex
                # encode. Perhaps the library should just take binary strings...
                tokens.append(binascii.b2a_hex(base64.b64decode(d.pushkey)))
            else:
                logger.warn("Ignoring device of platform %s", d.data['platform'])

        alert = None
        if n.type == 'm.room.message':
            alert = "Message from %s" % (n.fromuser)

        if not alert:
            logger.info("Don't know how to alert for a %s", n.type)
            return
        msg = apns_clerk.Message(tokens, alert)
        # truncate if > 256 bytes (apnsclient doesn't do this)
        truncated = False
        while len(msg.get_json_payload()) > 255:
            alert = alert[:-1]
            msg = apns_clerk.Message(tokens, alert)
            if not truncated:
                # add ellipsis to show we've truncated
                # (which is 3 bytes, ironically), so chop an extra
                # character here which is the *minimum* we'll need to
                # take off, but probably need to go around more times.)
                alert = alert[:-1]+u"\u2026"
                truncated = True
        logger.info("%s -> %s", alert, tokens)

        conn = self.sess.get_connection(
            ApnsPushkin.ADDRESSES[self.plaf],
            cert_file=self.certfile
        )
        srv = apns_clerk.APNs(conn)

        tries = 0
        while tries < ApnsPushkin.MAX_TRIES:
            res = srv.send(msg)
            for token, reason in res.failed.items():
                code, errmsg = reason
                # 'failed' are permanent failures
                # we should report back to the HS and
                # remove the pusher (when we have an interface for that)
                logger.warn("Permanent APNS failure to %s: %s", token, errmsg);
            for code, errmsg in res.errors:
                logger.warn("Temporary APNS failure to %s", token, errmsg);
            if not res.needs_retry():
                break
            msg = res.retry()
            tries += 1
    
        if res.needs_retry():
            raise NotificationDispatchException("Max retries exceeded")
