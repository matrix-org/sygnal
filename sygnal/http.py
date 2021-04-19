# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
# Copyright 2019 New Vector Ltd
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
import re
import sys
import time
import traceback
from uuid import uuid4

from opentracing import Format, logs, tags
from prometheus_client import Counter, Gauge, Histogram
from twisted.internet.defer import ensureDeferred
from twisted.web import server
from twisted.web.http import (
    combinedLogFormatter,
    datetimeToLogString,
    proxiedLogFormatter,
)
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET

from sygnal.notifications import NotificationContext
from sygnal.utils import NotificationLoggerAdapter, json_decoder

from .exceptions import InvalidNotificationException, NotificationDispatchException
from .notifications import Notification

logger = logging.getLogger(__name__)

NOTIFS_RECEIVED_COUNTER = Counter(
    "sygnal_notifications_received", "Number of notification pokes received"
)

NOTIFS_RECEIVED_DEVICE_PUSH_COUNTER = Counter(
    "sygnal_notifications_devices_received", "Number of devices been asked to push"
)

NOTIFS_BY_PUSHKIN = Counter(
    "sygnal_per_pushkin_type",
    "Number of pushes sent via each type of pushkin",
    labelnames=["pushkin"],
)

PUSHGATEWAY_HTTP_RESPONSES_COUNTER = Counter(
    "sygnal_pushgateway_status_codes",
    "HTTP Response Codes given on the Push Gateway API",
    labelnames=["code"],
)

NOTIFY_HANDLE_HISTOGRAM = Histogram(
    "sygnal_notify_time",
    "Time taken to handle /notify push gateway request",
    labelnames=["code"],
)

REQUESTS_IN_FLIGHT_GUAGE = Gauge(
    "sygnal_requests_in_flight",
    "Number of HTTP requests in flight",
    labelnames=["resource"],
)


class V1NotifyHandler(Resource):
    def __init__(self, sygnal):
        super().__init__()
        self.sygnal = sygnal

    isLeaf = True

    def _make_request_id(self):
        """
        Generates a request ID, intended to be unique, for a request so it can
        be followed through logging.
        Returns: a request ID for the request.
        """
        return str(uuid4())

    def render_POST(self, request):
        response = self._handle_request(request)
        if response != NOT_DONE_YET:
            PUSHGATEWAY_HTTP_RESPONSES_COUNTER.labels(code=request.code).inc()
        return response

    def _handle_request(self, request):
        """
        Actually handle the request.
        Args:
            request (Request): The request, corresponding to a POST request.

        Returns:
            Either a str instance or NOT_DONE_YET.

        """
        request_id = self._make_request_id()
        header_dict = {
            k.decode(): v[0].decode()
            for k, v in request.requestHeaders.getAllRawHeaders()
        }

        # extract OpenTracing scope from the HTTP headers
        span_ctx = self.sygnal.tracer.extract(Format.HTTP_HEADERS, header_dict)
        span_tags = {
            tags.SPAN_KIND: tags.SPAN_KIND_RPC_SERVER,
            "request_id": request_id,
        }

        root_span = self.sygnal.tracer.start_span(
            "pushgateway_v1_notify", child_of=span_ctx, tags=span_tags
        )

        # if this is True, we will not close the root_span at the end of this
        # function.
        root_span_accounted_for = False

        try:
            context = NotificationContext(request_id, root_span, time.perf_counter())

            log = NotificationLoggerAdapter(logger, {"request_id": request_id})

            try:
                body = json_decoder.decode(request.content.read().decode("utf-8"))
            except Exception as exc:
                msg = "Expected JSON request body"
                log.warning(msg, exc_info=exc)
                root_span.log_kv({logs.EVENT: "error", "error.object": exc})
                request.setResponseCode(400)
                return msg.encode()

            if "notification" not in body or not isinstance(body["notification"], dict):
                msg = "Invalid notification: expecting object in 'notification' key"
                log.warning(msg)
                root_span.log_kv({logs.EVENT: "error", "message": msg})
                request.setResponseCode(400)
                return msg.encode()

            try:
                notif = Notification(body["notification"])
            except InvalidNotificationException as e:
                log.exception("Invalid notification")
                request.setResponseCode(400)
                root_span.log_kv({logs.EVENT: "error", "error.object": e})
                return str(e).encode()

            if notif.event_id is not None:
                root_span.set_tag("event_id", notif.event_id)

            # track whether the notification was passed with content
            root_span.set_tag("has_content", notif.content is not None)

            NOTIFS_RECEIVED_COUNTER.inc()

            if len(notif.devices) == 0:
                msg = "No devices in notification"
                log.warning(msg)
                request.setResponseCode(400)
                return msg.encode()

            root_span_accounted_for = True

            async def cb():
                with REQUESTS_IN_FLIGHT_GUAGE.labels(
                    self.__class__.__name__
                ).track_inprogress():
                    await self._handle_dispatch(root_span, request, log, notif, context)

            ensureDeferred(cb())

            # we have to try and send the notifications first,
            # so we can find out which ones to reject
            return NOT_DONE_YET
        except Exception as exc_val:
            root_span.set_tag(tags.ERROR, True)

            # [2] corresponds to the traceback
            trace = traceback.format_tb(sys.exc_info()[2])
            root_span.log_kv(
                {
                    logs.EVENT: tags.ERROR,
                    logs.MESSAGE: str(exc_val),
                    logs.ERROR_OBJECT: exc_val,
                    logs.ERROR_KIND: type(exc_val),
                    logs.STACK: trace,
                }
            )
            raise
        finally:
            if not root_span_accounted_for:
                root_span.finish()

    def find_pushkins(self, appid):
        """Finds matching pushkins in self.sygnal.pushkins according to the appid.


        Args:
            appid (str): app identifier to search in self.sygnal.pushkins.

        Returns:
            list of `Pushkin`: If it finds a specific pushkin with
                the exact app id, immediately returns it.
                Otherwise returns possible pushkins.
        """
        # if found a specific appid, just return it as a list
        if appid in self.sygnal.pushkins:
            return [self.sygnal.pushkins[appid]]

        result = []
        for key, value in self.sygnal.pushkins.items():
            # The ".+" symbol is used in place of "*" symbol
            regex = key.replace("*", ".+")
            if re.search(regex, appid):
                result.append(value)
        return result

    async def _handle_dispatch(self, root_span, request, log, notif, context):
        """
        Actually handle the dispatch of notifications to devices, sequentially
        for simplicity.

        root_span: the OpenTracing span
        request: the Twisted Web Request
        log: the logger to use
        notif (Notification): the notification to dispatch
        context (NotificationContext): the context of the notification
        """
        try:
            rejected = []

            for d in notif.devices:
                NOTIFS_RECEIVED_DEVICE_PUSH_COUNTER.inc()

                appid = d.app_id
                found_pushkins = self.find_pushkins(appid)
                if len(found_pushkins) == 0:
                    log.warning("Got notification for unknown app ID %s", appid)
                    rejected.append(d.pushkey)
                    continue

                if len(found_pushkins) > 1:
                    log.warning("Got notification for an ambigious app ID %s", appid)
                    rejected.append(d.pushkey)
                    continue

                pushkin = found_pushkins[0]
                log.debug(
                    "Sending push to pushkin %s for app ID %s", pushkin.name, appid
                )

                NOTIFS_BY_PUSHKIN.labels(pushkin.name).inc()

                result = await pushkin.dispatch_notification(notif, d, context)
                if not isinstance(result, list):
                    raise TypeError("Pushkin should return list.")

                rejected += result

            request.write(json.dumps({"rejected": rejected}).encode())

            if rejected:
                log.info(
                    "Successfully delivered notifications with %d rejected pushkeys",
                    len(rejected),
                )
        except NotificationDispatchException:
            request.setResponseCode(502)
            log.warning("Failed to dispatch notification.", exc_info=True)
        except Exception:
            request.setResponseCode(500)
            log.error("Exception whilst dispatching notification.", exc_info=True)
        finally:
            if not request._disconnected:
                request.finish()

            PUSHGATEWAY_HTTP_RESPONSES_COUNTER.labels(code=request.code).inc()
            root_span.set_tag(tags.HTTP_STATUS_CODE, request.code)

            req_time = time.perf_counter() - context.start_time
            if req_time > 0:
                # can be negative as perf_counter() may not be monotonic
                NOTIFY_HANDLE_HISTOGRAM.labels(code=request.code).observe(req_time)
            if not 200 <= request.code < 300:
                root_span.set_tag(tags.ERROR, True)
            root_span.finish()


class HealthHandler(Resource):
    def render_GET(self, request):
        """
        `/health` is used for automatic checking of whether the service is up.
        It should just return a blank 200 OK response.
        """
        return b""


class SizeLimitingRequest(server.Request):
    # Arbitrarily limited to 512 KiB.
    MAX_REQUEST_SIZE = 512 * 1024

    def handleContentChunk(self, data):
        # we should have a content by now
        assert self.content, "handleContentChunk() called before gotLength()"
        if self.content.tell() + len(data) > self.MAX_REQUEST_SIZE:
            logger.info(
                "Aborting connection from %s because the request exceeds maximum size",
                self.client.host,
            )
            self.transport.abortConnection()
            return

        return super().handleContentChunk(data)


class SygnalLoggedSite(server.Site):
    """
    A subclass of Site to perform access logging in a way that makes sense for
    Sygnal.
    """

    def __init__(self, *args, log_formatter, **kwargs):
        super().__init__(*args, **kwargs)
        self.log_formatter = log_formatter
        self.logger = logging.getLogger("sygnal.access")

    def log(self, request):
        """Log this request. Called by request.finish."""
        # this also works around a bug in twisted.web.http.HTTPFactory which uses a
        # monotonic time as an epoch time.
        log_date_time = datetimeToLogString()
        line = self.log_formatter(log_date_time, request)
        self.logger.info("Handled request: %s", line)


class PushGatewayApiServer(object):
    def __init__(self, sygnal):
        """
        Initialises the /_matrix/push/* (Push Gateway API) server.
        Args:
            sygnal (Sygnal): the Sygnal object
        """
        root = Resource()
        matrix = Resource()
        push = Resource()
        v1 = Resource()

        # Note that using plain strings here will lead to silent failure
        root.putChild(b"_matrix", matrix)
        matrix.putChild(b"push", push)
        push.putChild(b"v1", v1)
        v1.putChild(b"notify", V1NotifyHandler(sygnal))

        # add health
        root.putChild(b"health", HealthHandler())

        use_x_forwarded_for = sygnal.config["log"]["access"]["x_forwarded_for"]

        log_formatter = (
            proxiedLogFormatter if use_x_forwarded_for else combinedLogFormatter
        )

        self.site = SygnalLoggedSite(
            root,
            reactor=sygnal.reactor,
            log_formatter=log_formatter,
            requestFactory=SizeLimitingRequest,
        )
