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
from os import environ
from time import time_ns
from io import BytesIO
from threading import Condition
from typing import BinaryIO, Optional, Union

import attr
from twisted.internet.defer import ensureDeferred
from twisted.test.proto_helpers import MemoryReactorClock
from twisted.trial import unittest
from twisted.web.http_headers import Headers
from twisted.web.server import Request

import psycopg2

from sygnal.http import PushGatewayApiServer
from sygnal.sygnal import CONFIG_DEFAULTS, Sygnal, merge_left_with_defaults

REQ_PATH = b"/_matrix/push/v1/notify"

USE_POSTGRES = environ.get("TEST_USE_POSTGRES", False)
# the dbname we will connect to in order to create the base database.
POSTGRES_DBNAME_FOR_INITIAL_CREATE = "postgres"
POSTGRES_USER = environ.get("TEST_POSTGRES_USER", None)
POSTGRES_PASSWORD = environ.get("TEST_POSTGRES_PASSWORD", None)
POSTGRES_HOST = environ.get("TEST_POSTGRES_HOST", None)


class TestCase(unittest.TestCase):
    def config_setup(self, config):
        self.dbname = "_sygnal_%s" % (time_ns())
        if USE_POSTGRES:
            config["db"] = {
                "user": POSTGRES_USER,
                "password": POSTGRES_PASSWORD,
                "database": self.dbname,
                "host": POSTGRES_HOST,
            }
        else:
            config["db"] = {"dbfile": ":memory:"}

    def _set_up_database(self, dbname):
        conn = psycopg2.connect(
            database=POSTGRES_DBNAME_FOR_INITIAL_CREATE,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host=POSTGRES_HOST,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("DROP DATABASE IF EXISTS %s;" % (dbname,))
        cur.execute("CREATE DATABASE %s;" % (dbname,))
        cur.close()
        conn.close()

    def _tear_down_database(self, dbname):
        conn = psycopg2.connect(
            database=POSTGRES_DBNAME_FOR_INITIAL_CREATE,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host=POSTGRES_HOST,
        )
        cur = conn.cursor()
        cur.execute("DROP DATABASE %s;" % (dbname,))
        cur.commit()
        cur.close()
        conn.close()

    def setUp(self):
        reactor = ExtendedMemoryReactorClock()

        config = {"apps": {}, "db": {}, "log": {"setup": {"version": 1}}}
        config = merge_left_with_defaults(CONFIG_DEFAULTS, config)

        self.config_setup(config)

        if USE_POSTGRES:
            self._set_up_database(self.dbname)

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
        if USE_POSTGRES:
            self._tear_down_database(self.dbname)

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

    def _request(self, payload) -> Union[dict, int]:
        """
        Make a dummy request to the notify endpoint with the specified payload

        Args:
            payload: payload to be JSON encoded

        Returns (dict or int):
            If successful (200 response received), the response is JSON decoded
            and the resultant dict is returned.
            If the response code is not 200, returns the response code.
        """
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        content = BytesIO(payload.encode())

        channel = FakeChannel(self.v1api.site, self.sygnal.reactor)
        channel.process_request(b"POST", REQ_PATH, content)

        while not channel.done:
            # we need to advance until the request has been finished
            self.sygnal.reactor.advance(1)
            self.sygnal.reactor.wait_for_work(lambda: channel.done)

        assert channel.done

        if channel.result.code != 200:
            return channel.result.code

        return json.loads(channel.response_body)


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


class DummyResponse(object):
    def __init__(self, code):
        self.code = code


def make_async_magic_mock(ret_val):
    async def dummy(*_args, **_kwargs):
        return ret_val

    return dummy


@attr.s
class HTTPResult:
    """Holds the result data for FakeChannel"""

    version = attr.ib(type=str)
    code = attr.ib(type=int)
    reason = attr.ib(type=str)
    headers = attr.ib(type=Headers)


@attr.s
class FakeChannel(object):
    """
    A fake Twisted Web Channel (the part that interfaces with the
    wire).
    """

    site = attr.ib()
    _reactor = attr.ib()
    _producer = None

    result = attr.ib(type=Optional[HTTPResult], default=None)
    response_body = b""
    done = attr.ib(type=bool, default=False)

    @property
    def code(self):
        if not self.result:
            raise Exception("No result yet.")
        return int(self.result.code)

    def writeHeaders(self, version, code, reason, headers):
        self.result = HTTPResult(version, int(code), reason, headers)

    def write(self, content):
        assert isinstance(content, bytes), "Should be bytes! " + repr(content)
        self.response_body += content

    def requestDone(self, _self):
        self.done = True

    def getPeer(self):
        return None

    def getHost(self):
        return None

    @property
    def transport(self):
        return None

    def process_request(self, method: bytes, request_path: bytes, content: BinaryIO):
        """pretend that a request has arrived, and process it"""

        # this is normally done by HTTPChannel, in its various lineReceived etc methods
        req = self.site.requestFactory(self)  # type: Request
        req.content = content
        req.requestReceived(method, request_path, b"1.1")
