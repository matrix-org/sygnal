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
import pushbaby.errors

import logging
import base64
import time
import gevent

logger = logging.getLogger(__name__)

create_failed_table_query = u"""
CREATE TABLE IF NOT EXISTS apns_failed (id INTEGER PRIMARY KEY, b64token TEXT NOT NULL,
last_failure_ts INTEGER NOT NULL,
last_failure_type varchar(10) not null, last_failure_code INTEGER default -1, token_invalidated_ts INTEGER default -1);
"""

create_failed_index_query = u"""
CREATE UNIQUE INDEX IF NOT EXISTS b64token on apns_failed(b64token);
"""


class ApnsPushkin(Pushkin):
    MAX_TRIES = 2
    DELETE_FEEDBACK_AFTER_SECS = 28 * 24 * 60 * 60 # a month(ish)
    # These are the only ones of the errors returned in the APNS stream
    # that we want to feed back. Anything else is nothing to do with the
    # token.
    ERRORS_TO_FEED_BACK = (
        pushbaby.errors.INVALID_TOKEN_SIZE,
        pushbaby.errors.INVALID_TOKEN,
    )

    def __init__(self, name):
        super(ApnsPushkin, self).__init__(name);

    def setup(self, ctx):
        self.db = ctx.database
        self.certfile = self.getConfig('certfile')
        plaf = self.getConfig('platform')
        if not plaf or plaf == 'production' or plaf == 'prod':
            self.plaf = 'prod'
        elif plaf == 'sandbox':
            self.plaf = 'sandbox'
        else:
            raise PushkinSetupException("Invalid platform: %s" % plaf)

        self.db.query(create_failed_table_query)
        self.db.query(create_failed_index_query)
            
        self.pushbaby = PushBaby(certfile=self.certfile, platform=self.plaf)
        self.pushbaby.on_failed_push = self.on_failed_push
        logger.info("APNS with cert file %s on %s platform", self.certfile, self.plaf)

        # poll feedback in a little bit, not while we're busy starting up
        gevent.spawn_later(10, self.do_feedback_poll)

    def dispatchNotification(self, n):
        tokens = {}
        for d in n.devices:
            tokplaf = 'prod'
            if 'platform' in d.data:
                tokplaf = d.data['platform']
            if tokplaf == self.plaf:
                tokens[d.pushkey] = d.pushkey_ts
            else:
                logger.warn("Ignoring device of platform %s", d.data['platform'])

        # check for tokens that have previously failed
        token_set_str = u"(" + u",".join([u"?" for _ in tokens.keys()]) + u")"
        feed_back_errors_set_str =  u"(" + u",".join([u"?" for _ in ApnsPushkin.ERRORS_TO_FEED_BACK]) + u")"
        q = ("SELECT b64token,last_failure_type,last_failure_code,token_invalidated_ts "+
            "FROM apns_failed WHERE b64token IN "+token_set_str+
            " and ("+
            "(last_failure_type = 'error' and last_failure_code in "+feed_back_errors_set_str+") "+
            "or (last_failure_type = 'feedback')"+
            ")")
        args = []
        args.extend([unicode(t) for t in tokens.keys()])
        args.extend(ApnsPushkin.ERRORS_TO_FEED_BACK)
        rows = self.db.query(q, args, fetch='all')

        rejected = []
        for row in rows:
            token_invalidated_ts = row[3]
            token_pushkey_ts = tokens[row[0]]
            if token_pushkey_ts < token_invalidated_ts:
                logger.warn(
                    "Rejecting token %s with ts %d. Last failure of type '%s' code %d, invalidated at %d",
                    row[0], token_pushkey_ts, row[1], row[2], token_invalidated_ts
                )
                rejected.append(row[0])
                del tokens[row[0]]
            else:
                logger.info("Have a failure for token %s of type '%s' code %d but this token postdates it: allowing.", row[0], row[1], row[2])
                # This pushkey may be alive again, but we don't delete the
                # failure because HSes should probably have a fresh token
                # if they actually want to use it

        loc_key = None
        loc_args = None
        if n.type == 'm.room.message':
            if n.room_name:
                loc_key = 'MSG_FROM_USER_IN_ROOM'
                loc_args = [n.fromuser, n.roomName]
            elif n.room_alias:
                loc_key = 'MSG_FROM_USER_IN_ROOM'
                loc_args = [n.fromuser, n.roomAlias]
            else:
                loc_key = 'MSG_FROM_USER'
                loc_args = [n.fromuser]
        elif n.type == 'm.call.invite':
            loc_key = 'VOICE_CALL_FROM_USER'
            loc_args = [n.fromuser]

        if not loc_key:
            logger.info("Don't know how to alert for a %s", n.type)
            return rejected

        payload = {
            "alert": {
                "loc-key":loc_key
            }
        }
        if loc_args:
            payload['alert']['loc-args'] = loc_args

        prio = 10
        if n.prio == 'low':
            prio = 5

        logger.info("'%s' -> %s", payload, tokens.keys())

        tries = 0
        for t in tokens.keys():
            while tries < ApnsPushkin.MAX_TRIES:
                try:
                    res = self.pushbaby.send(payload, base64.b64decode(t), priority=prio)
                    break
                except:
                    logger.exception("Exception sending push")

                tries += 1
    
        if tries == ApnsPushkin.MAX_TRIES:
            raise NotificationDispatchException("Max retries exceeded")

        return rejected

    def on_failed_push(self, token, identifier, status):
        logger.error("Error sending push to token %s, status", token, status)
        # We store all errors (could be useful to get failures instead of digging
        # through logs) but note that not all failures mean we should stop sending
        # to that token.
        self.db.query(
            "INSERT OR REPLACE INTO apns_failed "+
            "(b64token, last_failure_ts, last_failure_type, last_failure_code, token_invalidated_ts) "+
            " VALUES (?, ?, 'error', ?, ?)",
            (base64.b64encode(token), long(time.time()), status, long(time.time()))
        )

    def do_feedback_poll(self):
        logger.info("Polling feedback...")
        try:
            feedback = self.pushbaby.get_all_feedback()
            for fb in feedback:
                self.db.query(
                    "INSERT OR REPLACE INTO apns_failed "+
                    "(b64token, last_failure_ts, last_failure_type, token_invalidated_ts) "+
                    " VALUES (?, ?, 'feedback', ?)",
                    (fb.token, long(time.time()), long(fb.ts))
                )
            logger.info("Stored %d feedback items", len(feedback))

            # great, we're good until tomorrow
            gevent.spawn_later(24 * 60 * 60, self.do_feedback_poll)
        except:
            logger.exception("Failed to poll for feedback, trying again in 10 minutes")
            gevent.spawn_later(10 * 60, self.do_feedback_poll)

        self.prune_failures()

    def prune_failures(self):
        """
        Delete any failures older than a set amount of time.
        This is the only way we delete them - we can't delete
        them once we've sent them because a token could be in use by
        more than one Home Server.
        """
        cutoff = long(time.time()) - ApnsPushkin.DELETE_FEEDBACK_AFTER_SECS
        deleted = self.db.query(
            "DELETE FROM apns_failed WHERE last_failure_ts < ?",
            (cutoff,)
        )
        logger.info("deleted %d stale items from failure table", deleted)

    def shutdown(self):
        while self.pushbaby.messages_in_flight():
            gevent.wait(timeout=1.0)
