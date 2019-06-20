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
import asyncio
import json
import logging

from twisted.internet.defer import Deferred
from twisted.web import server
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET

from .exceptions import InvalidNotificationException
from .notifications import Notification

# TODO ?
logger = logging.getLogger(__name__)


# TODO sort this out.
class ClientError(Exception):
    pass


class V1NotifyHandler(Resource):
    def __init__(self, sygnal):
        super().__init__()
        self.sygnal = sygnal

    isLeaf = True

    def render_POST(self, request):
        async def wrap_handle_errors(coro):
            """
            Prevents exceptions, originating from one pushkin,
            from disrupting dispatch to other pushkins and feedback/returning the API response.
            :param coro: The coroutine or other awaitable to wrap
            :return: A wrapped awaitable which does not propagate the exception, returning
            [] on failure. (That is, the pushkeys will not be marked as failed.)
            """
            # TODO check this is what we want; maybe we should give 500 in event of failure?
            try:
                return await coro
            except Exception:
                logger.exception("Exception whilst dispatching notification to pushkin")
                return []

        try:
            body = json.loads(request.content.read())
        except Exception:
            raise ClientError("Expected JSON request body")

        if 'notification' not in body or not isinstance(body['notification'], dict):
            msg = "Invalid notification: expecting object in 'notification' key"
            logger.warning(msg)
            request.setResponseCode(400)
            return msg.encode()

        try:
            notif = Notification(body['notification'])
        except InvalidNotificationException as e:
            logger.exception("Invalid notification")
            request.setResponseCode(400)
            # return e.message.encode()
            return str(e).encode()

        # TODO NOTIFS_RECEIVED_COUNTER.inc()

        if len(notif.devices) == 0:
            msg = "No devices in notification"
            logger.warning(msg)
            request.setResponseCode(400)
            return msg.encode()

        rej = []
        futures = []

        pushkins = self.sygnal.pushkins

        for d in notif.devices:
            # TODO NOTIFS_RECEIVED_DEVICE_PUSH_COUNTER.inc()

            appid = d.app_id
            if appid not in pushkins:
                logger.warning("Got notification for unknown app ID %s", appid)
                rej.append(d.pushkey)
                continue

            pushkin = pushkins[appid]
            logger.debug(
                "Sending push to pushkin %s for app ID %s",
                pushkin.name, appid,
            )

            # TODO NOTIFS_BY_PUSHKIN.labels(pushkin.name).inc()

            futures.append(
                asyncio.ensure_future(wrap_handle_errors(
                    pushkin.dispatchNotification(notif, d)
                ))
            )

        def callback(rejected_lists):
            # combine all rejected pushkeys into one list
            rejected = sum(rejected_lists, rej)

            request.write(json.dumps({
                "rejected": rejected
            }).encode())

            request.finish()

        aggregate = Deferred.fromFuture(asyncio.gather(*futures))
        aggregate.addCallback(callback)

        # we have to try and send the notifications first, so we can find out which ones to reject
        return NOT_DONE_YET


class PushGatewayApiServer(object):
    def __init__(self, sygnal):
        root = Resource()
        matrix = Resource()
        push = Resource()
        v1 = Resource()

        # Note that using plain strings here will lead to silent failure
        root.putChild(b'_matrix', matrix)
        matrix.putChild(b'push', push)
        push.putChild(b'v1', v1)
        v1.putChild(b'notify', V1NotifyHandler(sygnal))

        self.site = server.Site(root)
