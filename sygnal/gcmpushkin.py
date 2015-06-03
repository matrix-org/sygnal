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

import gcmclient
import gevent

from . import Pushkin
from .exceptions import PushkinSetupException


logger = logging.getLogger(__name__)


MAX_TRIES = 3


class GcmPushkin(Pushkin):

    def __init__(self, name):
        super(GcmPushkin, self).__init__(name)

    def setup(self, ctx):
        self.db = ctx.database

        api_key = self.getConfig('apiKey')
        if not api_key:
            raise PushkinSetupException("No API key set in config")
        self.gcm = gcmclient.GCM(api_key)
        self.canonical_reg_id_store = CanonicalRegIdStore(self.db)

    def dispatchNotification(self, n):
        pushkeys = [device.pushkey for device in n.devices if device.app_id == self.name]
        # Resolve canonical IDs for all pushkeys
        pushkeys = [canonical_reg_id or reg_id for (reg_id, canonical_reg_id) in
                    self.canonical_reg_id_store.get_canonical_ids(pushkeys).items()]

        data = GcmPushkin.build_data(n)

        # TODO: Implement collapse_key to queue only one message per room.
        request = gcmclient.JSONMessage(pushkeys, data)
        failed = []

        for retry in range(0, MAX_TRIES):
            response = self.gcm.send(request)

            for reg_id, msg_id in response.success.items():
                logger.debug(
                    "Successfully sent notification %s to %s as %s",
                    n.id, reg_id, msg_id)

            for reg_id, canonical_reg_id in response.canonical.items():
                self.canonical_reg_id_store.set_canonical_id(reg_id, canonical_reg_id)

            failed.extend(response.failed.keys())

            if not response.needs_retry():
                break

            request = response.retry()
            gevent.wait(timeout=response.delay(retry))
        else:
            failed.extend(response.unavailable)

        return failed

    @staticmethod
    def build_data(n):
        data = {}
        for attr in ['id', 'type', 'sender', 'room_name', 'room_alias', 'prio', 'membership',
                     'sender_display_name', 'content', 'room_id']:
            if hasattr(n, attr):
                data[attr] = getattr(n, attr)

        # Flatten because GCM can't handle nested objects
        if getattr(n, 'content', None):
            data['msgtype'] = n.content["msgtype"]
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
