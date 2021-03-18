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
import typing
from typing import Any, Dict, List, Optional

from prometheus_client import Counter

from .exceptions import InvalidNotificationException, NotificationDispatchException

if typing.TYPE_CHECKING:
    from .sygnal import Sygnal


class Tweaks:
    def __init__(self, raw):
        self.sound = None

        if "sound" in raw:
            self.sound = raw["sound"]


class Device:
    def __init__(self, raw):
        self.app_id = None
        self.pushkey = None
        self.pushkey_ts = 0
        self.data = None
        self.tweaks = None

        if "app_id" not in raw:
            raise InvalidNotificationException("Device with no app_id")
        if "pushkey" not in raw:
            raise InvalidNotificationException("Device with no pushkey")
        if "pushkey_ts" in raw:
            self.pushkey_ts = raw["pushkey_ts"]
        if "tweaks" in raw:
            self.tweaks = Tweaks(raw["tweaks"])
        else:
            self.tweaks = Tweaks({})
        self.app_id = raw["app_id"]
        self.pushkey = raw["pushkey"]
        if "data" in raw:
            self.data = raw["data"]


class Counts:
    def __init__(self, raw):
        self.unread = None
        self.missed_calls = None

        if "unread" in raw:
            self.unread = raw["unread"]
        if "missed_calls" in raw:
            self.missed_calls = raw["missed_calls"]


class Notification:
    def __init__(self, notif):
        # optional attributes
        self.room_name: Optional[str] = notif.get("room_name")
        self.room_alias: Optional[str] = notif.get("room_alias")
        self.prio: Optional[str] = notif.get("prio")
        self.membership: Optional[str] = notif.get("membership")
        self.sender_display_name: Optional[str] = notif.get("sender_display_name")
        self.content: Optional[Dict[str, Any]] = notif.get("content")
        self.event_id: Optional[str] = notif.get("event_id")
        self.room_id: Optional[str] = notif.get("room_id")
        self.user_is_target: Optional[bool] = notif.get("user_is_target")
        self.type: Optional[str] = notif.get("type")
        self.sender: Optional[str] = notif.get("sender")

        if "devices" not in notif or not isinstance(notif["devices"], list):
            raise InvalidNotificationException("Expected list in 'devices' key")

        if "counts" in notif:
            self.counts = Counts(notif["counts"])
        else:
            self.counts = Counts({})

        self.devices = [Device(d) for d in notif["devices"]]


class Pushkin(object):
    def __init__(self, name: str, sygnal: "Sygnal", config: Dict[str, Any]):
        self.name = name
        self.cfg = config
        self.sygnal = sygnal

    def get_config(self, key: str, default=None):
        if key not in self.cfg:
            return default
        return self.cfg[key]

    async def dispatch_notification(
        self, n: Notification, device: Device, context: "NotificationContext"
    ) -> List[str]:
        """
        Args:
            n: The notification to dispatch via this pushkin
            device: The device to dispatch the notification for.
            context (NotificationContext): the request context

        Returns:
            A list of rejected pushkeys, to be reported back to the homeserver
        """
        pass

    @classmethod
    async def create(cls, name: str, sygnal: "Sygnal", config: Dict[str, Any]):
        """
        Override this if your pushkin needs to call async code in order to
        be constructed. Otherwise, it defaults to just invoking the Python-standard
        __init__ constructor.

        Returns:
            an instance of this Pushkin
        """
        return cls(name, sygnal, config)


class ConcurrencyLimitedPushkin(Pushkin):
    """
    A subclass of Pushkin that limits the number of in-flight requests at any
    one time, so as to prevent one Pushkin pulling the whole show down.
    """

    # Maximum in-flight, concurrent notification dispatches that we apply by default
    # We start turning away requests after this limit is reached.
    DEFAULT_CONCURRENCY_LIMIT = 512

    UNDERSTOOD_CONFIG_FIELDS = {"inflight_request_limit"}

    RATELIMITING_DROPPED_REQUESTS = Counter(
        "sygnal_inflight_request_limit_drop",
        "Number of notifications dropped because the number of inflight requests"
        " exceeded the configured inflight_request_limit.",
        labelnames=["pushkin"],
    )

    def __init__(self, name: str, sygnal: "Sygnal", config: Dict[str, Any]):
        super(ConcurrencyLimitedPushkin, self).__init__(name, sygnal, config)
        self._concurrent_limit = config.get(
            "inflight_request_limit",
            ConcurrencyLimitedPushkin.DEFAULT_CONCURRENCY_LIMIT,
        )
        self._concurrent_now = 0

        # Grab an instance of the dropped request counter given our pushkin name.
        # Note this ensures the counter appears in metrics even if it hasn't yet
        # been incremented.
        dropped_requests = ConcurrencyLimitedPushkin.RATELIMITING_DROPPED_REQUESTS
        self.dropped_requests_counter = dropped_requests.labels(pushkin=name)

    async def dispatch_notification(
        self, n: Notification, device: Device, context: "NotificationContext"
    ) -> List[str]:
        if self._concurrent_now >= self._concurrent_limit:
            self.dropped_requests_counter.inc()
            raise NotificationDispatchException(
                "Too many in-flight requests for this pushkin. "
                "(Something is wrong and Sygnal is struggling to keep up!)"
            )

        self._concurrent_now += 1
        try:
            return await self._dispatch_notification_unlimited(n, device, context)
        finally:
            self._concurrent_now -= 1

    async def _dispatch_notification_unlimited(
        self, n: Notification, device: Device, context: "NotificationContext"
    ) -> List[str]:
        # to be overridden by Pushkins!
        raise NotImplementedError


class NotificationContext(object):
    def __init__(self, request_id, opentracing_span, start_time):
        """
        Args:
            request_id (str): An ID for the request, or None to have it
                generated automatically.
            opentracing_span (Span): The span for the API request triggering
                the notification.
            start_time (float): Start timer value, `time.perf_counter()`
        """
        self.request_id = request_id
        self.opentracing_span = opentracing_span
        self.start_time = start_time
