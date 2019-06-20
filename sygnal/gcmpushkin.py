# -*- coding: utf-8 -*-
# Copyright 2014 Leon Handreke
# Copyright 2017 New Vector Ltd
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
import asyncio
import json
import logging
import time
import traceback
from io import BytesIO

from prometheus_client import Histogram
from twisted.web.client import HTTPConnectionPool, Agent, FileBodyProducer, readBody
from twisted.web.http_headers import Headers

from .exceptions import PushkinSetupException
from .notifications import Pushkin

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_gcm_request_time",
    "Time taken to send HTTP request",
)

logger = logging.getLogger(__name__)

# GCM_URL = b"https://fcm.googleapis.com/fcm/send"
GCM_URL = b"http://localhost:8000/10qmd681/fcm/send"
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

DEFAULT_MAX_CONNECTIONS = 20


class GcmPushkin(Pushkin):

    def __init__(self, name):
        super(GcmPushkin, self).__init__(name)

        self.http_agent = None
        self.http_pool = None
        self.db = None
        self.api_key = None
        self.canonical_reg_id_store = None

    def setup(self, sygnal):  # todo eliminate setup class
        # todo (all) do docstrings incl on classes. Use Synapse docstring syntax (Google format?)
        self.http_pool = HTTPConnectionPool(sygnal.reactor)
        self.http_pool.maxPersistentPerHost = self.getConfig("max_connections") or DEFAULT_MAX_CONNECTIONS

        self.http_agent = Agent(sygnal.reactor, pool=self.http_pool)

        self.db = sygnal.database

        self.api_key = self.getConfig('apiKey')
        if not self.api_key:
            raise PushkinSetupException("No API key set in config")
        self.canonical_reg_id_store = CanonicalRegIdStore(self.db)

    async def dispatchNotification(self, n, device):  # TODO use snake_case
        pushkeys = [device.pushkey for device in n.devices if device.app_id == self.name]
        # Resolve canonical IDs for all pushkeys

        reg_id_mappings = await self.canonical_reg_id_store.get_canonical_ids(pushkeys)

        pushkeys = [canonical_reg_id or reg_id for (reg_id, canonical_reg_id) in
                    reg_id_mappings.items()]

        if pushkeys[0] != device.pushkey:
            # Only send notifications once, to all devices at once.
            # TODO(rei) check this carefully, including tests
            return []

        data = GcmPushkin.build_data(n)
        headers = {
            b"User-Agent": ["sygnal"],
            b"Content-Type": ["application/json"],
            b"Authorization": ["key=%s" % (self.api_key,)]
        }

        # TODO: Implement collapse_key to queue only one message per room.
        failed = []

        # todo count status codes in prometheus

        for retry_number in range(0, MAX_TRIES):
            body = {
                "data": data,
                "priority": 'normal' if n.prio == 'low' else 'high',
            }
            if len(pushkeys) == 1:
                body['to'] = pushkeys[0]
            else:
                body['registration_ids'] = pushkeys

            logger.info("Sending (attempt %i): %r => %r", retry_number, data, pushkeys)
            poke_start_time = time.time()

            response = None
            with SEND_TIME_HISTOGRAM.time():  # <---
                body_producer = FileBodyProducer(BytesIO(json.dumps(body).encode()))
                try:
                    # responseA = self.http_agent.request(b'POST', GCM_URL,
                    #                                         headers=Headers(headers), bodyProducer=body_producer)
                    responseA = self.http_agent.request(b'POST', GCM_URL,
                                                        headers=Headers(headers),
                                                        bodyProducer=body_producer)
                    response = await responseA.asFuture(asyncio.get_event_loop())
                except Exception as exception:  # todo pull out this code. Reraise as custom exception
                    # raise CustomExc from exception
                    logger.debug("Request failed, waiting to try again:")
                    traceback.print_exc()
                    logger.exception(exception)

            logger.debug("GCM request took %f seconds", time.time() - poke_start_time)

            if response is not None:
                response_text = (await readBody(response)).decode()

            if response is None:
                pass
            elif response.code / 100 == 5:  # todo 500 <= response.code < 600
                logger.debug("%d from server, waiting to try again", response.code)
            elif response.code == 400:
                logger.error(
                    "%d from server, we have sent something invalid! Error: %r",
                    response.code,
                    response_text,
                )
                # permanent failure: give up
                raise Exception("Invalid request")  # todo <-- don't use Exception(…)
            elif response.code == 401:
                logger.error(
                    "401 from server! Our API key is invalid? Error: %r",
                    response_text,
                )
                # permanent failure: give up
                raise Exception("Not authorized to push")  # todo <-- don't use Exception(…)
            elif response.code / 100 == 2:  # todo 200 <= response.code < 300
                # todo context object. Assign IDs to requests. Don't log sensitive info
                # todo OpenTracing -> do context, get it almost for free
                resp_object = json.loads(response_text)
                if 'results' not in resp_object:
                    logger.error(
                        "%d from server but response contained no 'results' key: %r",
                        response.status_code, response.text,
                    )
                if len(resp_object['results']) < len(pushkeys):
                    logger.error(
                        "Sent %d notifications but only got %d responses!",
                        len(n.devices), len(resp_object['results'])
                    )

                # determine which pushkeys to retry or forget about
                new_pushkeys = []
                for i, result in enumerate(resp_object['results']):
                    if 'registration_id' in result:
                        await self.canonical_reg_id_store.set_canonical_id(
                            pushkeys[i], result['registration_id']
                        )
                    if 'error' in result:
                        logger.warning("Error for pushkey %s: %s", pushkeys[i], result['error'])
                        if result['error'] in BAD_PUSHKEY_FAILURE_CODES:
                            logger.info(
                                "Reg ID %r has permanently failed with code %r: rejecting upstream",
                                pushkeys[i], result['error']
                            )
                            failed.append(pushkeys[i])
                        elif result['error'] in BAD_MESSAGE_FAILURE_CODES:
                            logger.info(
                                "Message for reg ID %r has permanently failed with code %r",
                                pushkeys[i], result['error']
                            )
                        else:
                            logger.info(
                                "Reg ID %r has temporarily failed with code %r",
                                pushkeys[i], result['error']
                            )
                            new_pushkeys.append(pushkeys[i])
                if len(new_pushkeys) == 0:
                    # we are done – no more retries needed
                    return failed
                pushkeys = new_pushkeys

            retry_delay = RETRY_DELAY_BASE * (2 ** retry_number)
            if response and 'retry-after' in response.headers:
                try:
                    retry_delay = int(response.headers['retry-after'])
                except (KeyError, ValueError):
                    pass
            logger.info("Retrying in %d seconds", retry_delay)
            await asyncio.sleep(retry_delay)

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
        asyncio.get_event_loop().run_until_complete(self.db.query(self.TABLE_CREATE_QUERY))

    async def set_canonical_id(self, reg_id, canonical_reg_id):
        await self.db.query(
            "INSERT OR REPLACE INTO gcm_canonical_reg_id VALUES (?, ?);",
            (reg_id, canonical_reg_id))

    async def get_canonical_ids(self, reg_ids):
        # TODO: Use one DB query
        # TODO parallelise these queries or indeed combine them into 1
        return {reg_id: await self._get_canonical_id(reg_id) for reg_id in reg_ids}

    async def _get_canonical_id(self, reg_id):
        rows = await self.db.query(
            "SELECT canonical_reg_id FROM gcm_canonical_reg_id WHERE reg_id = ?;",
            (reg_id,), fetch='all')
        if rows:
            return rows[0][0]
