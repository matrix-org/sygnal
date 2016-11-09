# -*- coding: utf-8 -*-
# Copyright 2014 Leon Handreke
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

from __future__ import absolute_import, division, print_function, unicode_literals

import logging

import grequests
import gevent

from . import Pushkin
from .exceptions import PushkinSetupException


logger = logging.getLogger(__name__)

GCM_URL = "https://gcm-http.googleapis.com/gcm/send"
MAX_TRIES = 3
RETRY_DELAY_BASE = 10

# The error codes that mean a registration ID will never
# succeed and we should reject it upstream.
# We include NotRegistered here too for good measure, even
# though gcm-client 'helpfully' extracts these into a separate
# list.
PERMANENT_FAILURE_CODES = [
    'MissingRegistration',
    'InvalidRegistration',
    'NotRegistered',
    'InvalidPackageName',
    'MismatchSenderId',
    'MessageTooBig',
]

class GcmPushkin(Pushkin):

    def __init__(self, name):
        super(GcmPushkin, self).__init__(name)

    def setup(self, ctx):
        self.db = ctx.database

        self.api_key = self.getConfig('apiKey')
        if not self.api_key:
            raise PushkinSetupException("No API key set in config")
        self.canonical_reg_id_store = CanonicalRegIdStore(self.db)

    def dispatchNotification(self, n):
        pushkeys = [device.pushkey for device in n.devices if device.app_id == self.name]
        # Resolve canonical IDs for all pushkeys
        pushkeys = [canonical_reg_id or reg_id for (reg_id, canonical_reg_id) in
                    self.canonical_reg_id_store.get_canonical_ids(pushkeys).items()]

        data = GcmPushkin.build_data(n)
        headers = {
            "User-Agent": "sygnal",
            "Content-Type": "application/json",
            "Authorization": "key=%s" % (self.api_key,)
        }

        # TODO: Implement collapse_key to queue only one message per room.
        failed = []

        logger.info("%r => %r", data, pushkeys);

        for retry_number in range(0, MAX_TRIES):
            body = {
                "data": data,
                "priority": 'normal' if n.prio == 'low' else 'high',
            }
            if len(pushkeys) == 1:
                body['to'] = pushkeys[0]
            else:
                body['registration_ids'] = pushkeys

            req = grequests.post(
                GCM_URL, json=body, headers=headers
            )
            req.send()

            if req.response.status_code / 100 == 5:
                success = False
                logger.debug("%d from server, waiting to try again", response.status_code)
            elif req.response.status_code == 400:
                logger.error(
                    "%d from server, we have sent something invalid! Error: %r",
                    response.status_code,
                    response.json(),
                )
                # permanent failure: give up
                return failed
            elif req.response.status_code == 401:
                logger.error(
                    "401 from server! Our API key is invalid? Error: %r",
                    response.json(),
                )
                # permanent failure: give up
                return failed
            elif req.response.status_code / 100 == 2:
                resp_object = req.response.json()
                if 'results' not in resp_object:
                    logger.error(
                        "%d from server but response contained no 'results' key: %r",
                        req.response.status_code, req.response.text,
                    )
                if len(resp_object['results']) < len(pushkeys):
                    logger.error(
                        "Sent %d notifications but only got %d responses!",
                        len(n.devices), len(resp_object['results'])
                    )

                new_pushkeys = []
                for i, result in enumerate(resp_object['results']):
                    if 'registration_id' in result:
                        self.canonical_reg_id_store.set_canonical_id(
                            pushkeys[i], result['registration_id']
                        )
                    if 'error' in result:
                        logger.warn("Error for pushkey %s: %s", pushkeys[i], result['error'])
                        if result['error'] in PERMANENT_FAILURE_CODES:
                            logger.info(
                                "Reg ID %r has permanently failed with code %r",
                                 pushkeys[i], result['error']
                            )
                            failed.append(pushkeys[i])
                        else:
                            logger.info(
                                "Reg ID %r has temporarily failed with code %r",
                                 pushkeys[i], result['error']
                            )
                            new_pushkeys.append(pushkeys[i])
                if len(new_pushkeys) == 0:
                    return failed
                pushkeys = new_pushkeys

            retry_delay = RETRY_DELAY_BASE * (2 ** retry_number)
            if 'retry-after' in req.response.headers:
                try:
                    retry_delay = int(req.response.headers['retry-after'])
                except:
                    pass
            logger.info("Retrying in %d seconds", retry_delay)
            gevent.wait(timeout=retry_delay)

        logger.info("Gave up retrying reg IDs: %r", pushkeys)
        return failed

    @staticmethod
    def build_data(n):
        data = {}
        for attr in ['id', 'type', 'sender', 'room_name', 'room_alias', 'membership',
                     'sender_display_name', 'content', 'room_id']:
            if hasattr(n, attr):
                data[attr] = getattr(n, attr)

        data['prio'] = 'high'
        if n.prio == 'low':
            data['prio'] = 'normal';

        # Flatten because GCM can't handle nested objects
        if getattr(n, 'content', None):
            if "msgtype" in n.content:
                data['msgtype'] = n.content["msgtype"]
            if "body" in n.content:
                data['body'] = n.content["body"]

        if getattr(n, 'counts', None):
            data['unread'] = n.counts.unread
            data['missed_calls'] = n.counts.missed_calls

        return data


class CanonicalRegIdStore(object):

    TABLE_CREATE_QUERY = """
        CREATE TABLE IF NOT EXISTS gcm_canonical_reg_id (
            reg_id TEXT PRIMARY KEY,
            canonical_reg_id TEXT NOT NULL);"""

    def __init__(self, db):
        self.db = db
        self.db.query(self.TABLE_CREATE_QUERY)

    def set_canonical_id(self, reg_id, canonical_reg_id):
        self.db.query(
            "INSERT OR REPLACE INTO gcm_canonical_reg_id VALUES (?, ?);",
            (reg_id, canonical_reg_id))

    def get_canonical_ids(self, reg_ids):
        # TODO: Use one DB query
        return {reg_id: self._get_canonical_id(reg_id) for reg_id in reg_ids}

    def _get_canonical_id(self, reg_id):
        rows = self.db.query(
            "SELECT canonical_reg_id FROM gcm_canonical_reg_id WHERE reg_id = ?;",
            (reg_id, ), fetch='all')
        if rows:
            return rows[0][0]
