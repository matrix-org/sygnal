# -*- coding: utf-8 -*-
# Copyright 2014 Leon Handreke
# Copyright 2017 New Vector Ltd
# Copyright 2019-2020 The Matrix.org Foundation C.I.C.
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
import os
import time
from enum import Enum
from io import BytesIO
from typing import TYPE_CHECKING, Any, AnyStr, Dict, List, Optional, Tuple

# We are using an unstable async google-auth API, but it's there since 3+ years
# https://github.com/googleapis/google-auth-library-python/issues/613
import aiohttp
import google.auth.transport._aiohttp_requests
from google.auth._default_async import load_credentials_from_file
from google.oauth2._credentials_async import Credentials
from opentracing import Span, logs, tags
from prometheus_client import Counter, Gauge, Histogram
from twisted.internet.defer import Deferred, DeferredSemaphore
from twisted.web.client import FileBodyProducer, HTTPConnectionPool, readBody
from twisted.web.http_headers import Headers
from twisted.web.iweb import IResponse

from sygnal.exceptions import (
    NotificationDispatchException,
    NotificationQuotaDispatchException,
    PushkinSetupException,
    TemporaryNotificationDispatchException,
)
from sygnal.helper.context_factory import ClientTLSOptionsFactory
from sygnal.helper.proxy.proxyagent_twisted import ProxyAgent
from sygnal.notifications import (
    ConcurrencyLimitedPushkin,
    Device,
    Notification,
    NotificationContext,
)
from sygnal.utils import NotificationLoggerAdapter, json_decoder, twisted_sleep

if TYPE_CHECKING:
    from sygnal.sygnal import Sygnal

QUEUE_TIME_HISTOGRAM = Histogram(
    "sygnal_gcm_queue_time", "Time taken waiting for a connection to GCM"
)

SEND_TIME_HISTOGRAM = Histogram(
    "sygnal_gcm_request_time", "Time taken to send HTTP request to GCM"
)

PENDING_REQUESTS_GAUGE = Gauge(
    "sygnal_pending_gcm_requests", "Number of GCM requests waiting for a connection"
)

ACTIVE_REQUESTS_GAUGE = Gauge(
    "sygnal_active_gcm_requests", "Number of GCM requests in flight"
)

RESPONSE_STATUS_CODES_COUNTER = Counter(
    "sygnal_gcm_status_codes",
    "Number of HTTP response status codes received from GCM",
    labelnames=["pushkin", "code"],
)

logger = logging.getLogger(__name__)

GCM_URL = b"https://fcm.googleapis.com/fcm/send"
GCM_URL_V1 = "https://fcm.googleapis.com/v1/projects/{ProjectID}/messages:send"
MAX_TRIES = 3
RETRY_DELAY_BASE = 10
RETRY_DELAY_BASE_QUOTA_EXCEEDED = 60
MAX_BYTES_PER_FIELD = 1024
MAX_FIREBASE_MESSAGE_SIZE = 4096

# Subtract 1 since the combined size of the other non-overflowing fields will push it over the
# edge otherwise.
MAX_NOTIFICATION_OVERFLOW_FIELDS = MAX_FIREBASE_MESSAGE_SIZE / MAX_BYTES_PER_FIELD - 1

AUTH_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

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


class APIVersion(Enum):
    Legacy = "legacy"
    V1 = "v1"


class GcmPushkin(ConcurrencyLimitedPushkin):
    """
    Pushkin that relays notifications to Google/Firebase Cloud Messaging.
    """

    UNDERSTOOD_CONFIG_FIELDS = {
        "type",
        "api_key",
        "api_version",
        "fcm_options",
        "max_connections",
        "project_id",
        "service_account_file",
    } | ConcurrencyLimitedPushkin.UNDERSTOOD_CONFIG_FIELDS

    def __init__(self, name: str, sygnal: "Sygnal", config: Dict[str, Any]) -> None:
        super().__init__(name, sygnal, config)

        nonunderstood = set(self.cfg.keys()).difference(self.UNDERSTOOD_CONFIG_FIELDS)
        if len(nonunderstood) > 0:
            logger.warning(
                "The following configuration fields are not understood: %s",
                nonunderstood,
            )

        self.http_pool = HTTPConnectionPool(reactor=sygnal.reactor)
        self.max_connections = self.get_config(
            "max_connections", int, DEFAULT_MAX_CONNECTIONS
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

        self.api_version = APIVersion.Legacy
        version_str = self.get_config("api_version", str)
        if not version_str:
            logger.warning(
                "API version not set in config, defaulting to %s",
                self.api_version.value,
            )
        else:
            try:
                self.api_version = APIVersion(version_str)
            except ValueError:
                raise PushkinSetupException(
                    "Invalid API version set in config",
                    version_str,
                )

        if self.api_version is APIVersion.Legacy:
            self.api_key = self.get_config("api_key", str)
            if not self.api_key:
                raise PushkinSetupException("No API key set in config")

        self.project_id = self.get_config("project_id", str)
        if self.api_version is APIVersion.V1 and not self.project_id:
            raise PushkinSetupException(
                "Must configure `project_id` when using FCM api v1",
            )

        self.credentials: Optional[Credentials] = None

        if self.api_version is APIVersion.V1:
            self.service_account_file = self.get_config("service_account_file", str)
            if not self.service_account_file:
                raise PushkinSetupException(
                    "Must configure `service_account_file` when using FCM api v1",
                )
            try:
                self.credentials, _ = load_credentials_from_file(
                    str(self.service_account_file),
                    scopes=AUTH_SCOPES,
                )
            except google.auth.exceptions.DefaultCredentialsError as e:
                raise PushkinSetupException(
                    f"`service_account_file` must be valid: {str(e)}",
                )

            session = None
            if proxy_url:
                # `ClientSession` can't directly take the proxy URL, so we need to
                # set the usual env var and use `trust_env=True`
                os.environ["HTTPS_PROXY"] = proxy_url
                session = aiohttp.ClientSession(trust_env=True, auto_decompress=False)

            self.google_auth_request = google.auth.transport._aiohttp_requests.Request(
                session=session
            )

        # Use the fcm_options config dictionary as a foundation for the body;
        # this lets the Sygnal admin choose custom FCM options
        # (e.g. content_available).
        self.base_request_body = self.get_config("fcm_options", dict, {})
        if not isinstance(self.base_request_body, dict):
            raise PushkinSetupException(
                "Config field fcm_options, if set, must be a dictionary of options"
            )

    @classmethod
    async def create(
        cls, name: str, sygnal: "Sygnal", config: Dict[str, Any]
    ) -> "GcmPushkin":
        """
        Override this if your pushkin needs to call async code in order to
        be constructed. Otherwise, it defaults to just invoking the Python-standard
        __init__ constructor.

        Returns:
            an instance of this Pushkin
        """
        return cls(name, sygnal, config)

    async def _perform_http_request(
        self, body: Dict[str, Any], headers: Dict[AnyStr, List[AnyStr]]
    ) -> Tuple[IResponse, str]:
        """
        Perform an HTTP request to the FCM server with the body and headers
        specified.
        Args:
            body: Body. Will be JSON-encoded.
            headers: HTTP Headers.

        Returns:

        """
        body_producer = FileBodyProducer(BytesIO(json.dumps(body).encode()))

        # we use the semaphore to actually limit the number of concurrent
        # requests, since the HTTPConnectionPool will actually just lead to more
        # requests being created but not pooled – it does not perform limiting.
        with QUEUE_TIME_HISTOGRAM.time():
            with PENDING_REQUESTS_GAUGE.track_inprogress():
                await self.connection_semaphore.acquire()

        url = GCM_URL
        if self.api_version is APIVersion.V1:
            url = str.encode(GCM_URL_V1.format(ProjectID=self.project_id))

        try:
            with SEND_TIME_HISTOGRAM.time():
                with ACTIVE_REQUESTS_GAUGE.track_inprogress():
                    response = await self.http_agent.request(
                        b"POST",
                        url,
                        headers=Headers(headers),
                        bodyProducer=body_producer,
                    )
                    response_text = (await readBody(response)).decode()
        except Exception as exception:
            raise TemporaryNotificationDispatchException(
                "GCM request failure"
            ) from exception
        finally:
            self.connection_semaphore.release()
        return response, response_text

    async def _request_dispatch(
        self,
        n: Notification,
        log: NotificationLoggerAdapter,
        body: Dict[str, Any],
        headers: Dict[AnyStr, List[AnyStr]],
        pushkeys: List[str],
        span: Span,
    ) -> Tuple[List[str], List[str]]:
        poke_start_time = time.time()

        response, response_text = await self._perform_http_request(body, headers)

        RESPONSE_STATUS_CODES_COUNTER.labels(
            pushkin=self.name, code=response.code
        ).inc()

        log.debug("GCM request took %f seconds", time.time() - poke_start_time)

        span.set_tag(tags.HTTP_STATUS_CODE, response.code)

        if self.api_version is APIVersion.Legacy:
            return self._handle_legacy_response(
                n,
                log,
                response,
                response_text,
                pushkeys,
                span,
            )
        elif self.api_version is APIVersion.V1:
            return self._handle_v1_response(
                log,
                response,
                response_text,
                pushkeys,
                span,
            )
        else:
            log.warn(
                "Processing response for unknown API version: %s", self.api_version
            )
            return [], []

    def _handle_legacy_response(
        self,
        n: Notification,
        log: NotificationLoggerAdapter,
        response: IResponse,
        response_text: str,
        pushkeys: List[str],
        span: Span,
    ) -> Tuple[List[str], List[str]]:
        failed = []
        if 500 <= response.code < 600:
            log.debug("%d from server, waiting to try again", response.code)

            retry_after = None

            for header_value in response.headers.getRawHeaders(
                b"retry-after", default=[]
            ):
                retry_after = int(header_value)
                span.log_kv({"event": "gcm_retry_after", "retry_after": retry_after})

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
        elif response.code == 404:
            # assume they're all failed
            log.info("Reg IDs %r get 404 response; assuming unregistered", pushkeys)
            return pushkeys, []
        elif 200 <= response.code < 300:
            try:
                resp_object = json_decoder.decode(response_text)
            except ValueError:
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
                span.log_kv(
                    {
                        logs.EVENT: "gcm_response_mismatch",
                        "num_devices": len(n.devices),
                        "num_results": len(resp_object["results"]),
                    }
                )

            # determine which pushkeys to retry or forget about
            new_pushkeys = []
            for i, result in enumerate(resp_object["results"]):
                if "error" in result:
                    log.warning(
                        "Error for pushkey %s: %s", pushkeys[i], result["error"]
                    )
                    span.set_tag("gcm_error", result["error"])
                    if result["error"] in BAD_PUSHKEY_FAILURE_CODES:
                        log.info(
                            "Reg ID %r has permanently failed with code %r: "
                            "rejecting upstream",
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
        else:
            raise NotificationDispatchException(
                f"Unknown GCM response code {response.code}"
            )

    def _handle_v1_response(
        self,
        log: NotificationLoggerAdapter,
        response: IResponse,
        response_text: str,
        pushkeys: List[str],
        span: Span,
    ) -> Tuple[List[str], List[str]]:
        if 500 <= response.code < 600:
            log.debug("%d from server, waiting to try again", response.code)

            retry_after = None

            for header_value in response.headers.getRawHeaders(
                b"retry-after", default=[]
            ):
                retry_after = int(header_value)
                span.log_kv({"event": "gcm_retry_after", "retry_after": retry_after})

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
        elif response.code == 403:
            log.error("403 from server! Sender ID mismatch! Error: %r", response_text)
            # permanent failure: give up
            raise NotificationDispatchException("Sender ID mismatch")
        elif response.code == 429:
            log.debug("%d from server, waiting to try again", response.code)

            # Minimum 1 minute delay required
            retry_after = None

            for header_value in response.headers.getRawHeaders(
                b"retry-after", default=[]
            ):
                retry_after = int(header_value)

            span.log_kv({"event": "gcm_retry_after", "retry_after": retry_after})
            raise NotificationQuotaDispatchException(
                "Message rate quota exceeded.", custom_retry_delay=retry_after
            )
        elif response.code == 404:
            log.info("Reg IDs %r get 404 response; assuming unregistered", pushkeys)
            return pushkeys, []
        elif 200 <= response.code < 300:
            return [], []
        else:
            raise NotificationDispatchException(
                f"Unknown GCM response code {response.code}"
            )

    async def _get_auth_header(self) -> str:
        """Retrieve the auth header that can be used to authorize requests.

        :return: Needed content of the `Authorization` header
        """
        if self.api_version is APIVersion.Legacy:
            return "key=%s" % (self.api_key,)
        else:
            assert self.credentials is not None
            await self._refresh_credentials()
            return "Bearer %s" % self.credentials.token

    async def _refresh_credentials(self) -> None:
        assert self.credentials is not None
        if not self.credentials.valid:
            await Deferred.fromFuture(
                asyncio.ensure_future(
                    self.credentials.refresh(self.google_auth_request)
                )
            )

    async def _dispatch_notification_unlimited(
        self, n: Notification, device: Device, context: NotificationContext
    ) -> List[str]:
        log = NotificationLoggerAdapter(logger, {"request_id": context.request_id})

        pushkeys: list[str] = []
        if self.api_version is APIVersion.Legacy:
            # `_dispatch_notification_unlimited` gets called once for each device in the
            # `Notification` with a matching app ID. We do something a little dirty and
            # perform all of our dispatches the first time we get called for a
            # `Notification` and do nothing for the rest of the times we get called.
            pushkeys = [
                device.pushkey
                for device in n.devices
                if self.handles_appid(device.app_id)
            ]
            # `pushkeys` ought to never be empty here. At the very least it should contain
            # `device`'s pushkey.

            if pushkeys[0] != device.pushkey:
                # We've already been asked to dispatch for this `Notification` and have
                # previously sent out the notification to all devices.
                return []
        elif self.api_version is APIVersion.V1:
            pushkeys = [device.pushkey]

        # The pushkey is kind of secret because you can use it to send push
        # to someone.
        # span_tags = {"pushkeys": pushkeys}
        span_tags = {"gcm_num_devices": len(pushkeys)}

        with self.sygnal.tracer.start_span(
            "gcm_dispatch", tags=span_tags, child_of=context.opentracing_span
        ) as span_parent:
            # TODO: Implement collapse_key to queue only one message per room.
            failed: List[str] = []

            data = GcmPushkin._build_data(n, device, self.api_version)

            # Reject pushkey(s) if default_payload is misconfigured
            if data is None:
                log.warning(
                    "Rejecting pushkey(s) due to misconfigured default_payload, "
                    "please ensure that default_payload is a dict."
                )
                return pushkeys

            headers = {
                "User-Agent": ["sygnal"],
                "Content-Type": ["application/json"],
            }

            headers["Authorization"] = [await self._get_auth_header()]

            body = self.base_request_body.copy()
            body["data"] = data
            if self.api_version is APIVersion.Legacy:
                body["priority"] = "normal" if n.prio == "low" else "high"
            elif self.api_version is APIVersion.V1:
                priority = {"priority": "normal" if n.prio == "low" else "high"}
                if "android" in body:
                    body["android"].update(priority)
                else:
                    body["android"] = priority

            if self.api_version is APIVersion.V1:
                body["token"] = device.pushkey
                new_body = body
                body = {}
                body["message"] = new_body

            for retry_number in range(0, MAX_TRIES):
                # This has to happen inside the retry loop since `pushkeys` can be modified in the
                # event of a failure that warrants a retry.
                if self.api_version is APIVersion.Legacy:
                    if len(pushkeys) == 1:
                        body["to"] = pushkeys[0]
                    else:
                        body["registration_ids"] = pushkeys

                log.info(
                    "Sending (attempt %i) => %r room:%s, event:%s",
                    retry_number,
                    pushkeys,
                    n.room_id,
                    n.event_id,
                )

                try:
                    span_tags = {"retry_num": retry_number}

                    with self.sygnal.tracer.start_span(
                        "gcm_dispatch_try", tags=span_tags, child_of=span_parent
                    ) as span:
                        new_failed, new_pushkeys = await self._request_dispatch(
                            n, log, body, headers, pushkeys, span
                        )
                    pushkeys = new_pushkeys
                    failed += new_failed

                    if len(pushkeys) == 0:
                        break
                except TemporaryNotificationDispatchException as exc:
                    retry_delay = RETRY_DELAY_BASE * (2**retry_number)
                    if exc.custom_retry_delay is not None:
                        retry_delay = exc.custom_retry_delay

                    log.warning(
                        "Temporary failure, will retry in %d seconds",
                        retry_delay,
                        exc_info=True,
                    )

                    span_parent.log_kv(
                        {"event": "temporary_fail", "retrying_in": retry_delay}
                    )

                    await twisted_sleep(
                        retry_delay, twisted_reactor=self.sygnal.reactor
                    )
                except NotificationQuotaDispatchException as exc:
                    retry_delay = RETRY_DELAY_BASE_QUOTA_EXCEEDED * (2**retry_number)
                    if exc.custom_retry_delay is not None:
                        retry_delay = exc.custom_retry_delay

                    log.warning(
                        "Quota exceeded, will retry in %d seconds",
                        retry_delay,
                        exc_info=True,
                    )

                    span_parent.log_kv(
                        {"event": "temporary_fail", "retrying_in": retry_delay}
                    )

                    await twisted_sleep(
                        retry_delay, twisted_reactor=self.sygnal.reactor
                    )

            if len(pushkeys) > 0:
                log.info("Gave up retrying reg IDs: %r", pushkeys)
            # Count the number of failed devices.
            span_parent.set_tag("gcm_num_failed", len(failed))
            return failed

    @staticmethod
    def _build_data(
        n: Notification,
        device: Device,
        api_version: APIVersion,
    ) -> Optional[Dict[str, Any]]:
        """
        Build the payload data to be sent.
        Args:
            n: Notification to build the payload for.
            device: Device information to which the constructed payload
            will be sent.

        Returns:
            JSON-compatible dict or None if the default_payload is misconfigured
        """
        data = {}
        overflow_fields = 0

        if device.data:
            default_payload = device.data.get("default_payload", {})
            if isinstance(default_payload, dict):
                data.update(default_payload)
            else:
                logger.warning(
                    "default_payload was misconfigured, this value must be a dict."
                )
                return None

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
                if data[attr] is not None and isinstance(data[attr], str):
                    # The only `attr` that shouldn't be of type `str` is `content`,
                    # which is handled explicitly later on.
                    data[attr], truncated = truncate_str(
                        data[attr], MAX_BYTES_PER_FIELD
                    )
                    if truncated:
                        overflow_fields += 1

        if api_version is APIVersion.V1:
            if isinstance(data.get("content"), dict):
                for attr, value in data["content"].items():
                    if not isinstance(value, str):
                        continue
                    value, truncated = truncate_str(value, MAX_BYTES_PER_FIELD)
                    if truncated:
                        overflow_fields += 1
                    data["content_" + attr] = value
                del data["content"]

        data["prio"] = "high"
        if n.prio == "low":
            data["prio"] = "normal"

        if getattr(n, "counts", None):
            if api_version is APIVersion.Legacy:
                data["unread"] = n.counts.unread
                data["missed_calls"] = n.counts.missed_calls
            elif api_version is APIVersion.V1:
                data["unread"] = str(n.counts.unread)
                data["missed_calls"] = str(n.counts.missed_calls)

        if overflow_fields > MAX_NOTIFICATION_OVERFLOW_FIELDS:
            logger.warning(
                "Payload contains too many overflowing fields. Notification likely to be rejected by Firebase."
            )

        return data


def truncate_str(input: str, max_bytes: int) -> Tuple[str, bool]:
    """
    Truncate the given string. If the truncation would occur in the middle of a unicode
    character, that character will be removed entirely instead.
    Appends a `…` character to the resulting string when truncation occurs.

    Args:
        `input`: the string to be truncated
        `max_bytes`: maximum length, in bytes, that the payload should occupy when truncated

    Returns:
        Tuple of (truncated string, whether truncation took place)
    """

    str_bytes = input.encode("utf-8")
    if len(str_bytes) <= max_bytes:
        return (input, False)

    try:
        return (str_bytes[: max_bytes - 3].decode("utf-8") + "…", True)
    except UnicodeDecodeError as err:
        return (str_bytes[: err.start].decode("utf-8") + "…", True)
