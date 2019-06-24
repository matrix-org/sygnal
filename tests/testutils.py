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
from configparser import ConfigParser
from io import BytesIO
from threading import Condition

from twisted.internet.defer import gatherResults, ensureDeferred
from twisted.test.proto_helpers import MemoryReactorClock
from twisted.trial.unittest import TestCase
from twisted.web.http_headers import Headers
from twisted.web.server import NOT_DONE_YET
from twisted.web.test.requesthelper import DummyRequest

import sygnal.sygnal
from sygnal.http import PushGatewayApiServer
from sygnal.sygnal import Sygnal

REQ_PATH = b"/_matrix/push/v1/notify"


class TestCase(TestCase):
    def config_setup(self, config):
        config["db"]["dbfile"] = ":memory:"

    def setUp(self):
        reactor = ExtendedMemoryReactorClock()

        config = ConfigParser(sygnal.sygnal.CONFIG_DEFAULTS)
        for section in sygnal.sygnal.CONFIG_SECTIONS:
            config.add_section(section)

        self.config_setup(config)

        self.sygnal = Sygnal(config, reactor)
        self.sygnal._setup()
        self.v1api = PushGatewayApiServer(self.sygnal)

        start_deferred = gatherResults(
            [
                ensureDeferred(pushkin.start(self.sygnal))
                for pushkin in self.sygnal.pushkins.values()
            ],
            consumeErrors=True,
        )

        while not start_deferred.called:
            # we need to advance until the pushkins have started up
            self.sygnal.reactor.advance(1)
            self.sygnal.reactor.wait_for_work(lambda: start_deferred.called)

    def _make_dummy_notification(self, devices):
        return {
            "notification": {
                "id": "$3957tyerfgewrf384",
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
        request = self._make_request(*args, **kwargs)

        return self._collect_request(request)


class ExtendedMemoryReactorClock(MemoryReactorClock):
    def __init__(self):
        super().__init__()
        self.work_notifier = Condition()

    def callFromThread(self, function, *args):
        # TODO: check this is a safe implementation
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


class DummyResponse(object):
    def __init__(self, code):
        self.code = code
