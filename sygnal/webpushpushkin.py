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
from base64 import urlsafe_b64encode
from hashlib import blake2s
from io import BytesIO
from typing import List, Optional, Pattern
from urllib.parse import urlparse

from prometheus_client import Gauge, Histogram
from py_vapid import Vapid, VapidException
from pywebpush import webpush
from twisted.internet.defer import DeferredSemaphore
from twisted.web.client import FileBodyProducer, HTTPConnectionPool, readBody
from twisted.web.http_headers import Headers

from sygnal.helper.context_factory import ClientTLSOptionsFactory
from sygnal.helper.proxy.proxyagent_twisted import ProxyAgent

from .exceptions import PushkinSetupException
from .notifications import ConcurrencyLimitedPushkin
from .utils import glob_to_regex

QUEUE_TIME_HISTOGRAM = Histogram(
    "sygnal_webpush_queue_time",
    "Time taken waiting for a connection to WebPush endpoint",
)

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_webpush_request_time", "Time taken to send HTTP request to WebPush endpoint"
)

PENDING_REQUESTS_GAUGE = Gauge(
    "sygnal_pending_webpush_requests",
    "Number of WebPush requests waiting for a connection",
)

ACTIVE_REQUESTS_GAUGE = Gauge(
    "sygnal_active_webpush_requests", "Number of WebPush requests in flight"
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONNECTIONS = 20
DEFAULT_TTL = 15 * 60  # in seconds
# Max payload size is 4096
MAX_BODY_LENGTH = 1000
MAX_CIPHERTEXT_LENGTH = 2000


class WebpushPushkin(ConcurrencyLimitedPushkin):
    """
    Pushkin that relays notifications to Google/Firebase Cloud Messaging.
    """

    UNDERSTOOD_CONFIG_FIELDS = {
        "type",
        "max_connections",
        "vapid_private_key",
        "vapid_contact_email",
        "allowed_endpoints",
        "ttl",
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
        self.http_request_factory = HttpRequestFactory()

        self.allowed_endpoints = None  # type: Optional[List[Pattern]]
        allowed_endpoints = self.get_config("allowed_endpoints")
        if allowed_endpoints:
            if not isinstance(allowed_endpoints, list):
                raise PushkinSetupException(
                    "'allowed_endpoints' should be a list or not set"
                )
            self.allowed_endpoints = list(map(glob_to_regex, allowed_endpoints))
        privkey_filename = self.get_config("vapid_private_key")
        if not privkey_filename:
            raise PushkinSetupException("'vapid_private_key' not set in config")
        if not os.path.exists(privkey_filename):
            raise PushkinSetupException("path in 'vapid_private_key' does not exist")
        try:
            self.vapid_private_key = Vapid.from_file(private_key_file=privkey_filename)
        except VapidException as e:
            raise PushkinSetupException("invalid 'vapid_private_key' file") from e
        self.vapid_contact_email = self.get_config("vapid_contact_email")
        if not self.vapid_contact_email:
            raise PushkinSetupException("'vapid_contact_email' not set in config")
        self.ttl = self.get_config("ttl", DEFAULT_TTL)
        if not isinstance(self.ttl, int):
            raise PushkinSetupException("'ttl' must be an int if set")

    async def _dispatch_notification_unlimited(self, n, device, context):
        p256dh = device.pushkey
        if not isinstance(device.data, dict):
            logger.warn(
                "Rejecting pushkey %s; device.data is not a dict", device.pushkey
            )
            return [device.pushkey]

        # drop notifications without an event id if requested,
        # see https://github.com/matrix-org/sygnal/issues/186
        if device.data.get("events_only") is True and not n.event_id:
            return []

        endpoint = device.data.get("endpoint")
        auth = device.data.get("auth")
        endpoint_domain = urlparse(endpoint).netloc
        if self.allowed_endpoints:
            allowed = any(
                regex.fullmatch(endpoint_domain) for regex in self.allowed_endpoints
            )
            if not allowed:
                logger.error(
                    "push gateway %s is not in allowed_endpoints, blocking request",
                    endpoint_domain,
                )
                # abort, but don't reject push key
                return []

        if not p256dh or not endpoint or not auth:
            logger.warn(
                "Rejecting pushkey; subscription info incomplete "
                + "(p256dh: %s, endpoint: %s, auth: %s)",
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

        # web push only supports normal and low priority, so assume normal if absent
        low_priority = n.prio == "low"
        # allow dropping earlier notifications in the same room if requested
        topic = None
        if n.room_id and device.data.get("only_last_per_room") is True:
            # ask for a 22 byte hash, so the base64 of it is 32,
            # the limit webpush allows for the topic
            topic = urlsafe_b64encode(
                blake2s(n.room_id.encode(), digest_size=22).digest()
            )

        # note that webpush modifies vapid_claims, so make sure it's only used once
        vapid_claims = {
            "sub": "mailto:{}".format(self.vapid_contact_email),
        }
        # we use the semaphore to actually limit the number of concurrent
        # requests, since the HTTPConnectionPool will actually just lead to more
        # requests being created but not pooled – it does not perform limiting.
        with QUEUE_TIME_HISTOGRAM.time():
            with PENDING_REQUESTS_GAUGE.track_inprogress():
                await self.connection_semaphore.acquire()
        try:
            with SEND_TIME_HISTOGRAM.time():
                with ACTIVE_REQUESTS_GAUGE.track_inprogress():
                    request = webpush(
                        subscription_info=subscription_info,
                        data=data,
                        ttl=self.ttl,
                        vapid_private_key=self.vapid_private_key,
                        vapid_claims=vapid_claims,
                        requests_session=self.http_request_factory,
                    )
                    response = await request.execute(
                        self.http_agent, low_priority, topic
                    )
                    response_text = (await readBody(response)).decode()
        finally:
            self.connection_semaphore.release()

        reject_pushkey = self._handle_response(
            response, response_text, device.pushkey, endpoint_domain
        )
        if reject_pushkey:
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

        if n.content and isinstance(n.content, dict):
            content = n.content.copy()
            # we can't show formatted_body in a notification anyway on web
            # so remove it
            content.pop("formatted_body", None)
            body = content.get("body")
            # make some attempts to not go over the max payload length
            if isinstance(body, str) and len(body) > MAX_BODY_LENGTH:
                content["body"] = body[0 : MAX_BODY_LENGTH - 1] + "…"
            ciphertext = content.get("ciphertext")
            if isinstance(ciphertext, str) and len(ciphertext) > MAX_CIPHERTEXT_LENGTH:
                content.pop("ciphertext", None)
            payload["content"] = content

        return payload

    def _handle_response(self, response, response_text, pushkey, endpoint_domain):
        """
        Logs and determines the outcome of the response

        Returns:
            Boolean whether the puskey should be rejected
        """
        ttl_response_headers = response.headers.getRawHeaders(b"TTL")
        if ttl_response_headers:
            try:
                ttl_given = int(ttl_response_headers[0])
                if ttl_given != self.ttl:
                    logger.info(
                        "requested TTL of %d to endpoint %s but got %d",
                        self.ttl,
                        endpoint_domain,
                        ttl_given,
                    )
            except ValueError:
                pass
        # permanent errors
        if response.code == 404 or response.code == 410:
            logger.warn(
                "Rejecting pushkey %s; subscription is invalid on %s: %d: %s",
                pushkey,
                endpoint_domain,
                response.code,
                response_text,
            )
            return True
        # and temporary ones
        if response.code >= 400:
            logger.warn(
                "webpush request failed for pushkey %s; %s responded with %d: %s",
                pushkey,
                endpoint_domain,
                response.code,
                response_text,
            )
        elif response.code != 201:
            logger.info(
                "webpush request for pushkey %s didn't respond with 201; "
                + "%s responded with %d: %s",
                pushkey,
                endpoint_domain,
                response.code,
                response_text,
            )
        return False


class HttpRequestFactory:
    """
    Provide a post method that matches the API expected from pywebpush.
    """

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
        return HttpDelayedRequest(endpoint, data, headers)


class HttpDelayedRequest:
    """
    Captures the values received from pywebpush for the endpoint request.
    The request isn't immediately executed, to allow adding headers
    not supported by pywebpush, like Topic and Urgency.

    Also provides the interface that pywebpush expects from a response object.
    pywebpush expects a synchronous API, while we use an asynchronous API.

    To keep pywebpush happy we present it with some hardcoded values that
    make its assertions pass even though the HTTP request has not yet been
    made.

    Attributes:
        status_code (int):
            Defined to be 200 so the pywebpush check to see if is below 202
            passes.
        text (str):
            Set to None as pywebpush references this field for its logging.
    """

    status_code = 200
    text = None

    def __init__(self, endpoint, data, vapid_headers):
        self.endpoint = endpoint
        self.data = data
        self.vapid_headers = vapid_headers

    def execute(self, http_agent, low_priority, topic):
        body_producer = FileBodyProducer(BytesIO(self.data))
        # Convert the headers to the camelcase version.
        headers = {
            b"User-Agent": ["sygnal"],
            b"Content-Encoding": [self.vapid_headers["content-encoding"]],
            b"Authorization": [self.vapid_headers["authorization"]],
            b"TTL": [self.vapid_headers["ttl"]],
            b"Urgency": ["low" if low_priority else "normal"],
        }
        if topic:
            headers[b"Topic"] = [topic]
        return http_agent.request(
            b"POST",
            self.endpoint.encode(),
            headers=Headers(headers),
            bodyProducer=body_producer,
        )
