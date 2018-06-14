# -*- coding: utf-8 -*-
# Copyright 2014 Leon Handreke
# Copyright 2017 New Vector Ltd
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
import time

import grequests
import gevent

from . import Pushkin
from .exceptions import PushkinSetupException

#DEBUG
import pika


logger = logging.getLogger(__name__)

MAX_TRIES = 3
RETRY_DELAY_BASE = 10
MAX_BYTES_PER_FIELD = 1024

# The error codes that mean a registration ID will never
# succeed and we should reject it upstream.
# We include NotRegistered here too for good measure, even
# though gcm-client 'helpfully' extracts these into a separate
# list.
BAD_PUSHKEY_FAILURE_CODES = [
    'MissingRegistration',
    'InvalidRegistration',
    'NotRegistered',
    'InvalidPackageName',
    'MismatchSenderId',
]

# Failure codes that mean the message in question will never
# succeed, so don't retry, but the registration ID is fine
# so we should not reject it upstream.
BAD_MESSAGE_FAILURE_CODES = [
    'MessageTooBig',
    'InvalidDataKey',
    'InvalidTtl',
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

            #####
            # DEBUG
            logger.debug("[RABBITMQ]\nheader=%r\nbody=%r\n", headers.items(), body.items())
            connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
            channel = connection.channel()

            # Create a queue on the broker, idempotent operation
            channel.queue_declare(queue='hello')
            new_body = str(headers.items() + body.items())
            channel.basic_publish(exchange='',
                                routing_key='hello',
                                body=new_body)
            logger.debug(" [x] Sent 'Hello World!'")
            connection.close()

            ##
            #####

        logger.info("Gave up retrying reg IDs: %r", pushkeys)
        return failed

    @staticmethod
    def build_data(n):
        data = {}
        for attr in ['event_id', 'type', 'sender', 'room_name', 'room_alias', 'membership',
                     'sender_display_name', 'content', 'room_id']:
            if hasattr(n, attr):
                data[attr] = getattr(n, attr)
                # Truncate fields to a sensible maximum length. If the whole
                # body is too long, GCM will reject it.
                if data[attr] is not None and len(data[attr]) > MAX_BYTES_PER_FIELD:
                    data[attr] = data[attr][0:MAX_BYTES_PER_FIELD]

        data['prio'] = 'high'
        if n.prio == 'low':
            data['prio'] = 'normal';

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
