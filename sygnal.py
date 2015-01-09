#!/usr/bin/env python

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


import flask
import gevent.pywsgi
import ConfigParser
from flask import Flask, request

from sygnal import Notification, InvalidNotificationException

import json
import sys
import logging

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.debug = False
app.config.from_object(__name__)

CONFIG_SECTIONS = ['http', 'log', 'apps']
CONFIG_DEFAULTS = {
    'port': '5000',
    'loglevel': 'info'
}

pushkins = {}


class ClientError(Exception):
    pass

def parse_config():
    cfg = ConfigParser.SafeConfigParser(CONFIG_DEFAULTS)
    for sect in CONFIG_SECTIONS:
        try:
            cfg.add_section(sect)
        except ConfigParser.DuplicateSectionError:
            pass
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

@app.route('/notify', methods=['POST'])
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
            pushkin.dispatchNotification(notif)
            return flask.jsonify({})
        except:
            logger.exception("Failed to send push")
            flask.abort(500, "Failed to send push")


if __name__ == '__main__':
    cfg = parse_config()
    
    logging.basicConfig(level=getattr(logging, cfg.get('log', 'loglevel').upper()))

    for key,val in cfg.items('apps'):
        parts = key.rsplit('.', 1)
        if len(parts) < 2:
            continue
        if parts[1] == 'type':
            try:
                pushkins[parts[0]] = make_pushkin(val, parts[0])
            except:
                logger.exception("Failed to load module for kind %s", val)
                print "Unrecognised type: %s" % (val,)

    if len(pushkins) == 0:
        print "No app IDs are configured. Edit sygnal.conf to define some."
        sys.exit(1)

    for p in pushkins:
        pushkins[p].cfg = cfg
        pushkins[p].setup(cfg)
        logger.info("Configured with app IDs: %r", pushkins.keys())

    logger.info("Setup completed, listening on port %s", cfg.get('http', 'port'))

    http_server = gevent.pywsgi.WSGIServer(('0.0.0.0', cfg.getint('http', 'port')), app)
    http_server.serve_forever()

