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
import json
import logging
import time
from io import BytesIO
from json import JSONDecodeError

from prometheus_client import Histogram
from twisted.web.client import HTTPConnectionPool, Agent, FileBodyProducer, readBody
from twisted.web.http_headers import Headers

from sygnal.exceptions import (
    TemporaryNotificationDispatchException,
    NotificationDispatchException,
)
from sygnal.notifications import NotificationLoggerAdapter
from sygnal.utils import twisted_sleep
from .exceptions import PushkinSetupException
from .notifications import Pushkin

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_gcm_request_time", "Time taken to send HTTP request"
)

logger = logging.getLogger(__name__)

GCM_URL = b"https://fcm.googleapis.com/fcm/send"
MAX_TRIES = 3
RETRY_DELAY_BASE = 10
MAX_BYTES_PER_FIELD = 1024

# The error codes that mean a registration ID will never
# succeed and we should reject it upstream.
# We include NotRegistered here too for good measure, even
# though gcm-client 'helpfully' extracts these into a separate
# list.
BAD_PUSHKEY_FAILURE_CODES = [
    "MissingRegistration",
    "InvalidRegistration",
    "NotRegistered",
    "InvalidPackageName",
    "MismatchSenderId",
]

# Failure codes that mean the message in question will never
# succeed, so don't retry, but the registration ID is fine
# so we should not reject it upstream.
BAD_MESSAGE_FAILURE_CODES = ["MessageTooBig", "InvalidDataKey", "InvalidTtl"]

DEFAULT_MAX_CONNECTIONS = 20


class GcmPushkin(Pushkin):
    def __init__(self, name, sygnal, config):
        super(GcmPushkin, self).__init__(name, sygnal, config)

        self.http_agent = None
        self.http_pool = None
        self.db = None
        self.canonical_reg_id_store = None

        self.api_key = self.get_config("api_key")
        if not self.api_key:
            raise PushkinSetupException("No API key set in config")

    async def start(self, sygnal):
        # todo (all) do docstrings incl on classes. Use Synapse docstring syntax (Google format?)
        self.http_pool = HTTPConnectionPool(sygnal.reactor)
        self.http_pool.maxPersistentPerHost = (
            self.get_config("max_connections", DEFAULT_MAX_CONNECTIONS)
        )

        self.http_agent = Agent(sygnal.reactor, pool=self.http_pool)

        self.db = sygnal.database

        logger.debug("About to set up CanonicalRegId Store")
        self.canonical_reg_id_store = CanonicalRegIdStore()
        await self.canonical_reg_id_store.setup(self.db)
        logger.debug("Finished setting up CanonicalRegId Store")

    async def _perform_http_request(self, body, headers):
        body_producer = FileBodyProducer(BytesIO(json.dumps(body).encode()))
        try:
            response = await self.http_agent.request(
                b"POST", GCM_URL, headers=Headers(headers), bodyProducer=body_producer
            )
        except Exception as exception:
            raise TemporaryNotificationDispatchException(
                "GCM request failure"
            ) from exception
        response_text = (await readBody(response)).decode()
        return response, response_text

    async def _request_dispatch(self, n, log, body, headers, pushkeys):
        poke_start_time = time.time()

        failed = []

        with SEND_TIME_HISTOGRAM.time():
            response, response_text = await self._perform_http_request(body, headers)

        log.debug("GCM request took %f seconds", time.time() - poke_start_time)

        if 500 <= response.code < 600:
            log.debug("%d from server, waiting to try again", response.code)

            retry_after = None

            for header_value in response.headers.getRawHeader(
                    b"retry-after", default=[]
            ):
                retry_after = int(header_value)

            raise TemporaryNotificationDispatchException(
                "GCM server error, hopefully temporary.", custom_retry_delay=retry_after
            )
        elif response.code == 400:
            log.error(
                "%d from server, we have sent something invalid! Error: %r",
                response.code,
                response_text,
            )
            # permanent failure: give up
            raise NotificationDispatchException("Invalid request")
        elif response.code == 401:
            log.error(
                "401 from server! Our API key is invalid? Error: %r", response_text
            )
            # permanent failure: give up
            raise NotificationDispatchException("Not authorised to push")
        elif 200 <= response.code < 300:
            # todo OpenTracing -> do context, get it almost for free
            try:
                resp_object = json.loads(response_text)
            except JSONDecodeError:
                raise NotificationDispatchException("Invalid JSON response from GCM.")
            if "results" not in resp_object:
                log.error(
                    "%d from server but response contained no 'results' key: %r",
                    response.code,
                    response_text,
                )
            if len(resp_object["results"]) < len(pushkeys):
                log.error(
                    "Sent %d notifications but only got %d responses!",
                    len(n.devices),
                    len(resp_object["results"]),
                )

            # determine which pushkeys to retry or forget about
            new_pushkeys = []
            for i, result in enumerate(resp_object["results"]):
                if "registration_id" in result:
                    await self.canonical_reg_id_store.set_canonical_id(
                        pushkeys[i], result["registration_id"]
                    )
                if "error" in result:
                    log.warning(
                        "Error for pushkey %s: %s", pushkeys[i], result["error"]
                    )
                    if result["error"] in BAD_PUSHKEY_FAILURE_CODES:
                        log.info(
                            "Reg ID %r has permanently failed with code %r: rejecting upstream",
                            pushkeys[i],
                            result["error"],
                        )
                        failed.append(pushkeys[i])
                    elif result["error"] in BAD_MESSAGE_FAILURE_CODES:
                        log.info(
                            "Message for reg ID %r has permanently failed with code %r",
                            pushkeys[i],
                            result["error"],
                        )
                    else:
                        log.info(
                            "Reg ID %r has temporarily failed with code %r",
                            pushkeys[i],
                            result["error"],
                        )
                        new_pushkeys.append(pushkeys[i])
            return failed, new_pushkeys

    async def dispatch_notification(self, n, device, context):
        log = NotificationLoggerAdapter(logger, {"request_id": context.request_id})

        pushkeys = [
            device.pushkey for device in n.devices if device.app_id == self.name
        ]
        # Resolve canonical IDs for all pushkeys

        if pushkeys[0] != device.pushkey:
            # Only send notifications once, to all devices at once.
            # TODO(rei) check this carefully, including tests
            return []

        reg_id_mappings = await self.canonical_reg_id_store.get_canonical_ids(pushkeys)

        reg_id_mappings = {
            reg_id: canonical_reg_id or reg_id
            for (reg_id, canonical_reg_id) in reg_id_mappings.items()
        }

        inverse_reg_id_mappings = {v: k for (k, v) in reg_id_mappings.items()}

        data = GcmPushkin.build_data(n)
        headers = {
            b"User-Agent": ["sygnal"],
            b"Content-Type": ["application/json"],
            b"Authorization": ["key=%s" % (self.api_key,)],
        }

        # TODO: Implement collapse_key to queue only one message per room.
        failed = []

        # todo count status codes in prometheus

        body = {"data": data, "priority": "normal" if n.prio == "low" else "high"}

        for retry_number in range(0, MAX_TRIES):
            mapped_pushkeys = [reg_id_mappings[pk] for pk in pushkeys]

            if len(pushkeys) == 1:
                body["to"] = mapped_pushkeys[0]
            else:
                body["registration_ids"] = mapped_pushkeys

            log.info(
                "Sending (attempt %i): %r => %r", retry_number, data, mapped_pushkeys
            )

            try:
                new_failed, new_pushkeys = await self._request_dispatch(
                    n, log, body, headers, mapped_pushkeys
                )
                pushkeys = new_pushkeys
                failed += [
                    inverse_reg_id_mappings[canonical_pk] for canonical_pk in new_failed
                ]
                if len(pushkeys) == 0:
                    break
            except TemporaryNotificationDispatchException as exc:
                retry_delay = RETRY_DELAY_BASE * (2 ** retry_number)
                if exc.custom_retry_delay is not None:
                    retry_delay = exc.custom_retry_delay

                log.exception(
                    "Temporary failure, will retry in %d seconds", retry_delay
                )

                await twisted_sleep(retry_delay)

        if len(pushkeys) > 0:
            log.info("Gave up retrying reg IDs: %r", pushkeys)
        return failed

    @staticmethod
    def build_data(n):
        data = {}
        for attr in [
            "event_id",
            "type",
            "sender",
            "room_name",
            "room_alias",
            "membership",
            "sender_display_name",
            "content",
            "room_id",
        ]:
            if hasattr(n, attr):
                data[attr] = getattr(n, attr)
                # Truncate fields to a sensible maximum length. If the whole
                # body is too long, GCM will reject it.
                if data[attr] is not None and len(data[attr]) > MAX_BYTES_PER_FIELD:
                    data[attr] = data[attr][0:MAX_BYTES_PER_FIELD]

        data["prio"] = "high"
        if n.prio == "low":
            data["prio"] = "normal"

        if getattr(n, "counts", None):
            data["unread"] = n.counts.unread
            data["missed_calls"] = n.counts.missed_calls

        return data


class CanonicalRegIdStore(object):
    TABLE_CREATE_QUERY = """
        CREATE TABLE IF NOT EXISTS gcm_canonical_reg_id (
            reg_id TEXT PRIMARY KEY,
            canonical_reg_id TEXT NOT NULL);"""

    def __init__(self):
        self.db = None

    async def setup(self, db):
        """
        Prepares, if necessary, the database for storing canonical registration IDs.

        Separate method from the constructor because we wait for an async request to complete,
        so it must be an `async def` method.

        Args:
            db (Database): database to prepare

        """
        self.db = db
        await self.db.query(self.TABLE_CREATE_QUERY)

    async def set_canonical_id(self, reg_id, canonical_reg_id):
        await self.db.query(
            "INSERT OR REPLACE INTO gcm_canonical_reg_id VALUES (?, ?);",
            (reg_id, canonical_reg_id),
        )

    async def get_canonical_ids(self, reg_ids):
        # TODO: Use one DB query
        return {reg_id: await self.get_canonical_id(reg_id)
                for reg_id in reg_ids}

    async def get_canonical_id(self, reg_id):
        rows = await self.db.query(
            "SELECT canonical_reg_id"
            " FROM gcm_canonical_reg_id"
            " WHERE reg_id = ?;",
            (reg_id,),
            fetch="all",
        )

        if rows:
            return rows[0][0]
