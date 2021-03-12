# -*- coding: utf-8 -*-
# Copyright 2021 The Matrix.org Foundation C.I.C.
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

from pywebpush import webpush, WebPushException
from py_vapid import Vapid
from opentracing import logs, tags
from prometheus_client import Counter, Gauge, Histogram
from twisted.enterprise.adbapi import ConnectionPool
from twisted.internet.defer import DeferredSemaphore
from twisted.web.client import FileBodyProducer, HTTPConnectionPool, readBody
from twisted.web.http_headers import Headers

from sygnal.exceptions import (
    NotificationDispatchException,
    TemporaryNotificationDispatchException,
)
from sygnal.helper.context_factory import ClientTLSOptionsFactory
from sygnal.helper.proxy.proxyagent_twisted import ProxyAgent
from sygnal.utils import NotificationLoggerAdapter, twisted_sleep

from .exceptions import PushkinSetupException
from .notifications import ConcurrencyLimitedPushkin

logger = logging.getLogger(__name__)

MAX_TRIES = 3
RETRY_DELAY_BASE = 10
MAX_BYTES_PER_FIELD = 1024

DEFAULT_MAX_CONNECTIONS = 20


class WebpushPushkin(ConcurrencyLimitedPushkin):
    """
    Pushkin that relays notifications to Google/Firebase Cloud Messaging.
    """

    UNDERSTOOD_CONFIG_FIELDS = {
        "type",
        "max_connections",
        "vapid_private_key",
        "vapid_contact_email",
    } | ConcurrencyLimitedPushkin.UNDERSTOOD_CONFIG_FIELDS

    def __init__(self, name, sygnal, config):
        super(WebpushPushkin, self).__init__(name, sygnal, config)

        nonunderstood = self.cfg.keys() - self.UNDERSTOOD_CONFIG_FIELDS
        if nonunderstood:
            logger.warning(
                "The following configuration fields are not understood: %s",
                nonunderstood,
            )

        self.http_pool = HTTPConnectionPool(reactor=sygnal.reactor)
        self.max_connections = self.get_config(
            "max_connections", DEFAULT_MAX_CONNECTIONS
        )
        self.connection_semaphore = DeferredSemaphore(self.max_connections)
        self.http_pool.maxPersistentPerHost = self.max_connections

        tls_client_options_factory = ClientTLSOptionsFactory()

        # use the Sygnal global proxy configuration
        proxy_url = sygnal.config.get("proxy")

        self.http_agent = ProxyAgent(
            reactor=sygnal.reactor,
            pool=self.http_pool,
            contextFactory=tls_client_options_factory,
            proxy_url_str=proxy_url,
        )
        self.http_agent_wrapper = HttpAgentWrapper(self.http_agent)

        privkey_filename = self.get_config("vapid_private_key")
        self.vapid_private_key = Vapid.from_file(private_key_file=self.get_config("vapid_private_key"))
        vapid_contact_email = self.get_config("vapid_contact_email")
        self.vapid_claims = {"sub": "mailto:{}".format(vapid_contact_email)}

    async def _dispatch_notification_unlimited(self, n, device, context):
        p256dh = device.pushkey
        endpoint = device.data["endpoint"]
        auth = device.data["auth"]
        subscription_info = {
            'endpoint': endpoint,
            'keys': {
                'p256dh': p256dh,
                'auth': auth
            }
        }
        payload = WebpushPushkin._build_payload(n, device)
        data = json.dumps(payload)
        try:
            response_wrapper = webpush(
                subscription_info=subscription_info,
                data=data,
                vapid_private_key=self.vapid_private_key,
                vapid_claims=self.vapid_claims,
                requests_session=self.http_agent_wrapper
            )
            response = await response_wrapper.deferred
            response_text = (await readBody(response)).decode()

        except Exception as exception:
            raise TemporaryNotificationDispatchException(
                "webpush request failure"
            ) from exception

        failed_pushkeys = []
        # assume 4xx is permanent and 5xx is temporary
        if 400 <= response.code < 500:
            failed_pushkeys.append(device.pushkey)
        return failed_pushkeys

    @staticmethod
    def _build_payload(n, device):
        """
        Build the payload data to be sent.
        Args:
            n: Notification to build the payload for.
            device (Device): Device information to which the constructed payload
            will be sent.

        Returns:
            JSON-compatible dict
        """
        payload = {}

        if device.data:
            payload.update(device.data.get("default_payload", {}))

        # if type is m.room.message, add content.msgtype and content.body
        if getattr(n, "type", None) == "m.room.message" and getattr(n, "content", None):
            content = n.content
            for attr in ["msgtype", "body"]:
                if getattr(content, attr, None):
                    payload[attr] = getattr(content, attr)

        for attr in [
            "room_id",
            "room_name",
            "room_alias",
            "membership",
            "sender",
            "sender_display_name",
            "event_id",
            "user_is_target",
            "type",
        ]:
            if getattr(n, attr, None):
                payload[attr] = getattr(n, attr)

        if getattr(n, "counts", None):
            counts = n.counts
            for attr in ["unread", "missed_calls"]:
                if getattr(counts, attr, None):
                    payload[attr] = getattr(counts, attr)

        return payload

class HttpAgentWrapper:
    def __init__(self, http_agent):
        self.http_agent = http_agent

    def post(self, endpoint, data, headers, timeout):
        logger.info("HttpAgentWrapper: POST %s", endpoint)
        body_producer = FileBodyProducer(BytesIO(data))
        headers = {
            b"User-Agent": ["sygnal"],
            b"Content-Encoding": [headers["content-encoding"]],
            b"Authorization": [headers["authorization"]],
            b"TTL": [headers["ttl"]],
        }
        deferred = self.http_agent.request(
            b"POST",
            endpoint.encode(),
            headers=Headers(headers),
            bodyProducer=body_producer,
        )
        return HttpResponseWrapper(deferred)

class HttpResponseWrapper:
    def __init__(self, deferred):
        self.deferred = deferred
        self.status_code = 0
        self.text = None

