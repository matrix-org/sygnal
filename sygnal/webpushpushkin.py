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
import os.path
from io import BytesIO

from prometheus_client import Gauge, Histogram
from py_vapid import Vapid
from pywebpush import webpush
from twisted.internet.defer import DeferredSemaphore
from twisted.web.client import FileBodyProducer, HTTPConnectionPool, readBody
from twisted.web.http_headers import Headers

from sygnal.helper.context_factory import ClientTLSOptionsFactory
from sygnal.helper.proxy.proxyagent_twisted import ProxyAgent

from .exceptions import PushkinSetupException
from .notifications import ConcurrencyLimitedPushkin

QUEUE_TIME_HISTOGRAM = Histogram(
    "sygnal_webpush_queue_time",
    "Time taken waiting for a connection to webpush endpoint",
)

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_webpush_request_time", "Time taken to send HTTP request to webpush endpoint"
)

PENDING_REQUESTS_GAUGE = Gauge(
    "sygnal_pending_webpush_requests",
    "Number of webpush requests waiting for a connection",
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
        try:
            self.vapid_private_key = Vapid.from_file(private_key_file=privkey_filename)
        except BaseException as e:
            raise PushkinSetupException("invalid 'vapid_private_key' file") from e
        vapid_contact_email = self.get_config("vapid_contact_email")
        if not vapid_contact_email:
            raise PushkinSetupException("'vapid_contact_email' not set in config")
        self.vapid_claims = {"sub": "mailto:{}".format(vapid_contact_email)}

    async def _dispatch_notification_unlimited(self, n, device, context):
        p256dh = device.pushkey
        if not isinstance(device.data, dict):
            logger.warn(
                "device.data is not a dict for pushkey %s, rejecting pushkey", p256dh
            )
            return [device.pushkey]

        endpoint = device.data.get("endpoint")
        auth = device.data.get("auth")

        if not p256dh or not endpoint or not auth:
            logger.warn(
                "subscription info incomplete "
                + "(p256dh: %s, endpoint: %s, auth: %s), rejecting pushkey",
                p256dh,
                endpoint,
                auth,
            )
            return [device.pushkey]

        subscription_info = {
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
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
                        requests_session=self.http_agent_wrapper,
                    )
                    response = await response_wrapper.deferred
                    await readBody(response)
        finally:
            self.connection_semaphore.release()

        # assume 4xx is permanent and 5xx is temporary
        if 400 <= response.code < 500:
            return [device.pushkey]
        return []

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

        default_payload = device.data.get("default_payload")
        if isinstance(default_payload, dict):
            payload.update(default_payload)

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
            "content",
        ]:
            value = getattr(n, attr, None)
            if value:
                payload[attr] = value

        counts = getattr(n, "counts", None)
        if counts is not None:
            for attr in ["unread", "missed_calls"]:
                count_value = getattr(counts, attr, None)
                if count_value is not None:
                    payload[attr] = count_value

        return payload


class HttpAgentWrapper:
    """
    Provide a post method that matches the API expected from pywebpush.
    """
    def __init__(self, http_agent):
        self.http_agent = http_agent

    def post(self, endpoint, data, headers, timeout):
        """
        Convert the requests-like API to a Twisted API call.

        Args:
            endpoint (str):
                The full http url to post to
            data (bytes):
                the (encrypted) binary body of the request
            headers (py_vapid.CaseInsensitiveDict):
                A (costume) dictionary with the headers.
            timeout (int)
                Ignored for now
        """
        body_producer = FileBodyProducer(BytesIO(data))
        # Convert the headers to the camelcase version.
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
    """
    Provide a response object that matches the API expected from pywebpush.
    pywebpush expects a synchronous API, while we use an asynchronous API.

    To keep pywebpush happy we present it with some hardcoded values that
    make its assertions pass while the async network call is happening
    in the background.

    Attributes:
        deferred (Deferred):
            The deferred to await the actual response after calling pywebpush.
        status_code (int):
            Defined to be 200 so the pywebpush check to see if is below 202
            passes.
        text (str):
            Set to None as pywebpush references this field for its logging.
    """
    status_code = 200
    text = None

    def __init__(self, deferred):
        self.deferred = deferred
