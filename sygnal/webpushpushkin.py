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
import os.path;
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

from sygnal.helper.context_factory import ClientTLSOptionsFactory
from sygnal.helper.proxy.proxyagent_twisted import ProxyAgent

from .exceptions import PushkinSetupException
from .notifications import ConcurrencyLimitedPushkin

QUEUE_TIME_HISTOGRAM = Histogram(
    "sygnal_webpush_queue_time", "Time taken waiting for a connection to webpush endpoint"
)

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_webpush_request_time", "Time taken to send HTTP request to webpush endpoint"
)

PENDING_REQUESTS_GAUGE = Gauge(
    "sygnal_pending_webpush_requests", "Number of webpush requests waiting for a connection"
)

ACTIVE_REQUESTS_GAUGE = Gauge(
    "sygnal_active_webpush_requests", "Number of webpush requests in flight"
)

logger = logging.getLogger(__name__)

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
        if not privkey_filename:
            raise PushkinSetupException("'vapid_private_key' not set in config")
        if not os.path.exists(privkey_filename):
            raise PushkinSetupException("path in 'vapid_private_key' does not exist")
        self.vapid_private_key = Vapid.from_file(private_key_file=privkey_filename)
        vapid_contact_email = self.get_config("vapid_contact_email")
        if not vapid_contact_email:
            raise PushkinSetupException("'vapid_contact_email' not set in config")
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

        # we use the semaphore to actually limit the number of concurrent
        # requests, since the HTTPConnectionPool will actually just lead to more
        # requests being created but not pooled – it does not perform limiting.
        with QUEUE_TIME_HISTOGRAM.time():
            with PENDING_REQUESTS_GAUGE.track_inprogress():
                await self.connection_semaphore.acquire()

        try:
            with SEND_TIME_HISTOGRAM.time():
                with ACTIVE_REQUESTS_GAUGE.track_inprogress():
                    response_wrapper = webpush(
                        subscription_info=subscription_info,
                        data=data,
                        vapid_private_key=self.vapid_private_key,
                        vapid_claims=self.vapid_claims,
                        requests_session=self.http_agent_wrapper
                    )
                    response = await response_wrapper.deferred
                    await readBody(response)
        finally:
            self.connection_semaphore.release()

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

        for attr in [
            "room_id",
            "room_name",
            "room_alias",
            "membership",
            "event_id",
            "sender",
            "sender_display_name",
            "user_is_target",
            "type",
            "content"
        ]:
            if getattr(n, attr, None):
                payload[attr] = getattr(n, attr)

        if getattr(n, "counts", None):
            counts = n.counts
            for attr in ["unread", "missed_calls"]:
                if getattr(counts, attr, None) != None:
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

