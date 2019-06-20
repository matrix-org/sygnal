# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
# Copyright 2018, 2019 New Vector Ltd
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


import configparser
import importlib
import logging
import os
import sys
from logging.handlers import WatchedFileHandler

from twisted.internet import reactor
from twisted.internet.defer import gatherResults, ensureDeferred

from sygnal.http import PushGatewayApiServer
from .database import Database

logger = logging.getLogger(__name__)

CONFIG_SECTIONS = ['http', 'log', 'apps', 'db', 'metrics']
CONFIG_DEFAULTS = {
    'port': '5000',
    'loglevel': 'info',
    'logfile': '',
    'dbfile': 'sygnal.db'
}


class Sygnal(object):
    def __init__(self, config, custom_reactor=reactor):
        self.config = config
        self.reactor = custom_reactor
        self.pushkins = {}

    def _setup(self):
        cfg = self.config

        logging.getLogger().setLevel(getattr(logging, cfg.get('log', 'loglevel').upper()))
        logfile = cfg.get('log', 'logfile')
        if logfile != '':
            handler = WatchedFileHandler(logfile)
            # TODO not sure how to port this over to Twisted Web handler.addFilter(RequestIdFilter())
            formatter = logging.Formatter(
                '%(asctime)s [%(process)d] %(levelname)-5s '
                '%%(request_id)s %(name)s %(message)s'
            )
            handler.setFormatter(formatter)
            logging.getLogger().addHandler(handler)
        else:
            logging.basicConfig()

        # TODO if cfg.has_option("metrics", "sentry_dsn"):
        #     # Only import sentry if enabled
        #     import sentry_sdk
        #     from sentry_sdk.integrations.flask import FlaskIntegration
        #     sentry_sdk.init(
        #         dsn=cfg.get("metrics", "sentry_dsn"),
        #         integrations=[FlaskIntegration()],
        #     )

        # TODO if cfg.has_option("metrics", "prometheus_port"):
        #     prometheus_client.start_http_server(
        #         port=cfg.getint("metrics", "prometheus_port"),
        #         addr=cfg.get("metrics", "prometheus_addr"),
        #     )

        self.database = Database(cfg.get('db', 'dbfile'), self.reactor)

        for key, val in cfg.items('apps'):
            parts = key.rsplit('.', 1)
            if len(parts) < 2:
                continue
            if parts[1] == 'type':
                try:
                    self.pushkins[parts[0]] = self._make_pushkin(val, parts[0])
                except Exception:
                    logger.exception("Failed to load module for kind %s", val)
                    raise

        if len(self.pushkins) == 0:
            logger.error("No app IDs are configured. Edit sygnal.conf to define some.")
            sys.exit(1)

        logger.info("Configured with app IDs: %r", self.pushkins.keys())
        logger.info("Setup completed")

    def _make_pushkin(self, kind, name):
        if '.' in kind:
            toimport = kind
        else:
            toimport = f"sygnal.{kind}pushkin"

        logger.info("Creating pushkin: %s", toimport)
        pushkin_module = importlib.import_module(toimport)
        clarse = getattr(pushkin_module, f"{kind.capitalize()}Pushkin")
        return clarse(name, self, self.config)

    def run(self):
        self._setup()
        port = int(self.config.get('http', 'port'))
        pushgateway_api = PushGatewayApiServer(self)
        logger.info("Listening on port %d", port)

        start_deferred = gatherResults([ensureDeferred(pushkin.start(self)) for pushkin in self.pushkins.values()],
                                       consumeErrors=True)

        def on_started(_):
            logger.info("Starting listening")
            self.reactor.listenTCP(port, pushgateway_api.site)

        start_deferred.addCallback(on_started)

        logger.info("Starting pushkins")
        self.reactor.run()

    def shutdown(self):
        pass  # TODO


def parse_config():
    cfg = configparser.ConfigParser(CONFIG_DEFAULTS)
    # Make keys case-sensitive
    cfg.optionxform = str
    for sect in CONFIG_SECTIONS:
        try:
            cfg.add_section(sect)
        except configparser.DuplicateSectionError:
            pass
    cfg.read(os.getenv("SYGNAL_CONF", "sygnal.conf"))
    return cfg


if __name__ == '__main__':
    config = parse_config()
    sygnal = Sygnal(config)
    sygnal.run()
