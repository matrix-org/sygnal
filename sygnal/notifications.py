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
import abc
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type, TypeVar, overload

from matrix_common.regex import glob_to_regex
from opentracing import Span
from prometheus_client import Counter

from sygnal.exceptions import (
    InvalidNotificationException,
    NotificationDispatchException,
    PushkinSetupException,
)

if TYPE_CHECKING:
    from sygnal.sygnal import Sygnal

T = TypeVar("T")


@overload
def get_key(raw: Dict[str, Any], key: str, type_: Type[T], default: T) -> T: ...


@overload
def get_key(
    raw: Dict[str, Any], key: str, type_: Type[T], default: None = None
) -> Optional[T]: ...


def get_key(
    raw: Dict[str, Any], key: str, type_: Type[T], default: Optional[T] = None
) -> Optional[T]:
    if key not in raw:
        return default
    if not isinstance(raw[key], type_):
        raise InvalidNotificationException(f"{key} is of invalid type")
    return raw[key]


class Tweaks:
    def __init__(self, raw: Dict[str, Any]):
        self.sound: Optional[str] = get_key(raw, "sound", str)


class Device:
    def __init__(self, raw: Dict[str, Any]):
        if "app_id" not in raw or not isinstance(raw["app_id"], str):
            raise InvalidNotificationException(
                "Device with missing or non-string app_id"
            )
        self.app_id: str = raw["app_id"]
        if "pushkey" not in raw or not isinstance(raw["pushkey"], str):
            raise InvalidNotificationException(
                "Device with missing or non-string pushkey"
            )
        self.pushkey: str = raw["pushkey"]

        self.pushkey_ts: int = get_key(raw, "pushkey_ts", int, 0)
        self.data: Optional[Dict[str, Any]] = get_key(raw, "data", dict)
        self.tweaks = Tweaks(get_key(raw, "tweaks", dict, {}))


class Counts:
    def __init__(self, raw: Dict[str, Any]):
        self.unread: Optional[int] = get_key(raw, "unread", int)
        self.missed_calls: Optional[int] = get_key(raw, "missed_calls", int)


class Notification:
    def __init__(self, notif: dict):
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


class Pushkin(abc.ABC):
    def __init__(self, name: str, sygnal: "Sygnal", config: Dict[str, Any]):
        self.name = name
        self.appid_pattern = glob_to_regex(name, ignore_case=False)
        self.cfg = config
        self.sygnal = sygnal

    @overload
    def get_config(self, key: str, type_: Type[T], default: T) -> T: ...

    @overload
    def get_config(
        self, key: str, type_: Type[T], default: None = None
    ) -> Optional[T]: ...

    def get_config(
        self, key: str, type_: Type[T], default: Optional[T] = None
    ) -> Optional[T]:
        if key not in self.cfg:
            return default
        if not isinstance(self.cfg[key], type_):
            raise PushkinSetupException(
                f"{key} is of incorrect type, please check that the entry for {key} is "
                f"formatted correctly in the config file. "
            )
        return self.cfg[key]

    def handles_appid(self, appid: str) -> bool:
        """Checks whether the pushkin is responsible for the given app ID"""
        return self.name == appid or self.appid_pattern.match(appid) is not None

    @abc.abstractmethod
    async def dispatch_notification(
        self, n: Notification, device: Device, context: "NotificationContext"
    ) -> List[str]:
        """
        Args:
            n: The notification to dispatch via this pushkin
            device: The device to dispatch the notification for.
            context: the request context

        Returns:
            A list of rejected pushkeys, to be reported back to the homeserver
        """
        ...

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
        super().__init__(name, sygnal, config)
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
    def __init__(self, request_id: str, opentracing_span: Span, start_time: float):
        """
        Args:
            request_id: An ID for the request, or None to have it
                generated automatically.
            opentracing_span: The span for the API request triggering
                the notification.
            start_time: Start timer value, `time.perf_counter()`
        """
        self.request_id = request_id
        self.opentracing_span = opentracing_span
        self.start_time = start_time
