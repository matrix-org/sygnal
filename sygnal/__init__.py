# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
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


from .exceptions import InvalidNotificationException
import flask
from flask import Flask, request

import sygnal.db

import ConfigParser
import json
import sys
import logging

logger = logging.getLogger(__name__)

app = Flask('sygnal')
app.debug = False
app.config.from_object(__name__)

CONFIG_SECTIONS = ['http', 'log', 'apps', 'db']
CONFIG_DEFAULTS = {
    'port': '5000',
    'loglevel': 'info',
    'logfile': '',
    'dbfile': 'sygnal.db'
}

pushkins = {}


class Tweaks:
    def __init__(self, raw):
        self.sound = None

        if 'sound' in raw:
            self.sound = raw['sound']


class Device:
    def __init__(self, raw):
        self.app_id = None
        self.pushkey = None
        self.pushkey_ts = 0
        self.data = None
        self.tweaks = None

        if 'app_id' not in raw:
            raise InvalidNotificationException("Device with no app_id")
        if 'pushkey' not in raw:
            raise InvalidNotificationException("Device with no pushkey")
        if 'pushkey_ts' in raw:
            self.pushkey_ts = raw['pushkey_ts']
        if 'tweaks' in raw:
            self.tweaks = Tweaks(raw['tweaks'])
        else:
            self.tweaks = Tweaks({})
        self.app_id = raw['app_id']
        self.pushkey = raw['pushkey']
        if 'data' in raw:
            self.data = raw['data']


class Counts:
    def __init__(self, raw):
        self.unread = None
        self.missed_calls = None

        if 'unread' in raw:
            self.unread = raw['unread']
        if 'mised_calls' in raw:
            self.mised_calls = raw['mised_calls']


class Notification:
    def __init__(self, notif):
        attrs = [ 'id', 'type', 'sender' ]
        for a in attrs:
            if a not in notif:
                raise InvalidNotificationException("Expected '%s' key" % (a,))
            self.__dict__[a] = notif[a]

        optional_attrs = ['room_name', 'room_alias', 'prio', 'membership', 'sender_display_name']
        for a in optional_attrs:
            if a in notif:
                self.__dict__[a] = notif[a]
            else:
                self.__dict__[a] = None

        if 'devices' not in notif or not isinstance(notif['devices'], list):
               raise InvalidNotificationException("Expected list in 'devices' key")

        if 'counts' in notif:
            self.counts = Counts(notif['counts'])
        else:
            self.counts = Counts({})

        self.devices = [Device(d) for d in notif['devices']]
        

class Pushkin(object):
    def __init__(self, name):
        self.name = name

    def setup(self):
        pass

    def getConfig(self, key):
        if not self.cfg.has_option('apps', '%s.%s' % (self.name, key)):
            return None
        return self.cfg.get('apps', '%s.%s' % (self.name, key))
        
    def dispatchNotification(self, n):
        pass

    def shutdown(self):
        pass


class SygnalContext:
    pass


class ClientError(Exception):
    pass


def parse_config():
    cfg = ConfigParser.SafeConfigParser(CONFIG_DEFAULTS)
    for sect in CONFIG_SECTIONS:
        try:
            cfg.add_section(sect)
        except ConfigParser.DuplicateSectionError:
            pass
    # it would be nice to be able to customise this the only
    # way gunicorn lets us pass parameters to our app is by
    # adding arguments to the module which is kind of grim
    cfg.read("sygnal.conf")
    return cfg

def make_pushkin(kind, name):
    if '.' in kind:
        toimport = kind
    else:
        toimport = "sygnal.%spushkin" % kind
    toplevelmodule = __import__(toimport)
    pushkinmodule = getattr(toplevelmodule, "%spushkin" % kind)
    clarse = getattr(pushkinmodule, "%sPushkin" % kind.capitalize())
    return clarse(name)

@app.errorhandler(ClientError)
def handle_client_error(e):
    resp = flask.jsonify({ 'error': { 'msg': str(e) }  })
    resp.status_code = 400
    return resp

@app.route('/')
def root():
    return ""

@app.route('/_matrix/push/v1/notify', methods=['POST'])
def notify():
    try:
        body = json.loads(request.data)
    except:
        raise ClientError("Expecting json request body")

    if 'notification' not in body or not isinstance(body['notification'], dict):
        msg = "Invalid notification: expecting object in 'notification' key"
        logger.warn(msg)
        flask.abort(400, msg)

    try:
        notif = Notification(body['notification'])
    except InvalidNotificationException as e:
        logger.exception("Invalid notification")
        flask.abort(400, e.message)

    if len(notif.devices) == 0:
        flask.abort(400, "No devices in notification")

    for d in notif.devices:
        appid = d.app_id.lower()
        if appid not in pushkins:
            logger.warn("Got notification for unknown app ID %s", appid)
            flask.abort(400, "Got notification for unknown app ID %s" % (appid,))

        pushkin = pushkins[appid]
        try:
            rej = pushkin.dispatchNotification(notif)
            return flask.jsonify({
                "rejected": rej
            })
        except:
            logger.exception("Failed to send push")
            flask.abort(500, "Failed to send push")


@app.before_first_request
def setup():
    cfg = parse_config()

    logging.getLogger().setLevel(getattr(logging, cfg.get('log', 'loglevel').upper()))
    logfile = cfg.get('log', 'logfile')
    if logfile != '':
        handler = logging.FileHandler(logfile)
        formatter = logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s')
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)
    else:
        logging.basicConfig()

    ctx = SygnalContext()
    ctx.database = sygnal.db.Db(cfg.get('db', 'dbfile'))

    for key,val in cfg.items('apps'):
        parts = key.rsplit('.', 1)
        if len(parts) < 2:
            continue
        if parts[1] == 'type':
            try:
                pushkins[parts[0]] = make_pushkin(val, parts[0])
            except:
                logger.exception("Failed to load module for kind %s", val)

    if len(pushkins) == 0:
        logger.error("No app IDs are configured. Edit sygnal.conf to define some.")
        sys.exit(1)

    for p in pushkins:
        pushkins[p].cfg = cfg
        pushkins[p].setup(ctx)
        logger.info("Configured with app IDs: %r", pushkins.keys())

    logger.error("Setup completed")

def shutdown():
    logger.info("Starting shutdown...")
    i = 0
    for p in pushkins.values():
        logger.info("Shutting down (%d/%d)..." % (i+1, len(pushkins)))
        p.shutdown()
        i += 1
    logger.info("Shutdown complete...")
