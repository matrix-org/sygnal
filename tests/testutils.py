# -*- coding: utf-8 -*-
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
from io import BytesIO
from threading import Condition

from twisted.internet.defer import ensureDeferred
from twisted.test.proto_helpers import MemoryReactorClock
from twisted.trial import unittest
from twisted.web.http_headers import Headers
from twisted.web.server import NOT_DONE_YET
from twisted.web.test.requesthelper import DummyRequest as UnaugmentedDummyRequest

from sygnal.http import PushGatewayApiServer
from sygnal.sygnal import Sygnal, merge_left_with_defaults, CONFIG_DEFAULTS

REQ_PATH = b"/_matrix/push/v1/notify"


class TestCase(unittest.TestCase):
    def config_setup(self, config):
        config["db"]["dbfile"] = ":memory:"

    def setUp(self):
        reactor = ExtendedMemoryReactorClock()

        config = {"apps": {}, "db": {}, "log": {"setup": {"version": 1}}}
        config = merge_left_with_defaults(CONFIG_DEFAULTS, config)

        self.config_setup(config)

        self.sygnal = Sygnal(config, reactor)
        self.sygnal.database.start()
        self.v1api = PushGatewayApiServer(self.sygnal)

        start_deferred = ensureDeferred(
            self.sygnal._make_pushkins_then_start(0, [], None)
        )

        while not start_deferred.called:
            # we need to advance until the pushkins have started up
            self.sygnal.reactor.advance(1)
            self.sygnal.reactor.wait_for_work(lambda: start_deferred.called)

    def tearDown(self):
        super().tearDown()
        self.sygnal.database.close()

    def _make_dummy_notification(self, devices):
        return {
            "notification": {
                "event_id": "$3957tyerfgewrf384",
                "room_id": "!slw48wfj34rtnrf:example.com",
                "type": "m.room.message",
                "sender": "@exampleuser:matrix.org",
                "sender_display_name": "Major Tom",
                "room_name": "Mission Control",
                "room_alias": "#exampleroom:matrix.org",
                "prio": "high",
                "content": {
                    "msgtype": "m.text",
                    "body": "I'm floating in a most peculiar way.",
                },
                "counts": {"unread": 2, "missed_calls": 1},
                "devices": devices,
            }
        }

    def _make_request(self, payload, headers=None):
        """
        Make a dummy request to the notify endpoint with the specified
        Args:
            payload: payload to be JSON encoded
            headers (dict, optional): A L{dict} mapping header names as L{bytes}
            to L{list}s of header values as L{bytes}

        Returns (DummyRequest):
            A dummy request corresponding to the request arguments supplied.

        """
        pathparts = REQ_PATH.split(b"/")
        if pathparts[0] == b"":
            pathparts = pathparts[1:]
        dreq = DummyRequest(pathparts)
        dreq.requestHeaders = Headers(headers or {})
        dreq.responseCode = 200  # default to 200

        if isinstance(payload, dict):
            payload = json.dumps(payload)

        dreq.content = BytesIO(payload.encode())
        dreq.method = "POST"

        return dreq

    def _collect_request(self, request):
        """
        Collects (waits until done and then returns the result of) the request.
        Args:
            request (Request): a request to collect

        Returns (dict or int):
            If successful (200 response received), the response is JSON decoded
            and the resultant dict is returned.
            If the response code is not 200, returns the response code.
        """
        resource = self.v1api.site.getResourceFor(request)
        rendered = resource.render(request)

        if request.responseCode != 200:
            return request.responseCode

        if isinstance(rendered, str):
            return json.loads(rendered)
        elif rendered == NOT_DONE_YET:

            while not request.finished:
                # we need to advance until the request has been finished
                self.sygnal.reactor.advance(1)
                self.sygnal.reactor.wait_for_work(lambda: request.finished)

            assert request.finished > 0

            if request.responseCode != 200:
                return request.responseCode

            written_bytes = b"".join(request.written)
            return json.loads(written_bytes)
        else:
            raise RuntimeError(f"Can't collect: {rendered}")

    def _request(self, *args, **kwargs):
        """
        Makes and collects a request.
        See L{_make_request} and L{_collect_request}.
        """
        request = self._make_request(*args, **kwargs)

        return self._collect_request(request)


class ExtendedMemoryReactorClock(MemoryReactorClock):
    def __init__(self):
        super().__init__()
        self.work_notifier = Condition()

    def callFromThread(self, function, *args):
        self.callLater(0, function, *args)

    def callLater(self, when, what, *a, **kw):
        self.work_notifier.acquire()
        try:
            return_value = super().callLater(when, what, *a, **kw)
            self.work_notifier.notify_all()
        finally:
            self.work_notifier.release()

        return return_value

    def wait_for_work(self, early_stop=lambda: False):
        """
        Blocks until there is work as long as the early stop condition
        is not satisfied.

        Args:
            early_stop: Extra function called that determines whether to stop
                blocking.
                Should returns true iff the early stop condition is satisfied,
                in which case no blocking will be done.
                It is intended to be used to detect when the task you are
                waiting for is complete, e.g. a Deferred has fired or a
                Request has been finished.
        """
        self.work_notifier.acquire()

        try:
            while len(self.getDelayedCalls()) == 0 and not early_stop():
                self.work_notifier.wait()
        finally:
            self.work_notifier.release()


class DummyRequest(UnaugmentedDummyRequest):
    """
    Tracks the response code in the 'code' field, like a normal Request.
    """

    def __init__(self, postpath, session=None, client=None):
        super().__init__(postpath, session, client)
        self.code = 200

    def setResponseCode(self, code, message=None):
        super().setResponseCode(code, message)
        self.code = code


class DummyResponse(object):
    def __init__(self, code):
        self.code = code


def make_async_magic_mock(ret_val):
    async def dummy(*_args, **_kwargs):
        return ret_val

    return dummy
