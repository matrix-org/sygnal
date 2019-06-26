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
import copy
import importlib
import logging
import os
import sys
from logging.handlers import WatchedFileHandler

import yaml
from twisted.internet import reactor
from twisted.internet.defer import gatherResults, ensureDeferred

from sygnal.http import PushGatewayApiServer
from .database import Database

logger = logging.getLogger(__name__)

CONFIG_DEFAULTS = {
    "http": {"port": 5000, "bind_addresses": ["127.0.0.1"]},
    "log": {"level": "info", "file": ""},
    "db": {"dbfile": "sygnal.db"},
}


class Sygnal(object):
    def __init__(self, config, custom_reactor=reactor):
        self.config = config
        self.reactor = custom_reactor
        self.pushkins = {}

    def _setup(self):
        cfg = self.config

        if "loglevel" in cfg["log"]:
            logging.getLogger().setLevel(getattr(logging, cfg["log"]["level"].upper()))
        if "file" in cfg["log"]:
            logfile = cfg["log"]["file"]
        else:
            logfile = ""
        if logfile != "":
            handler = WatchedFileHandler(logfile)
            formatter = logging.Formatter(
                "%(asctime)s [%(process)d] %(levelname)-5s "
                "%%(request_id)s %(name)s %(message)s"
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

        self.database = Database(cfg["db"]["dbfile"], self.reactor)

        for app_id, app_cfg in cfg["apps"].items():
            try:
                self.pushkins[app_id] = self._make_pushkin(app_id, app_cfg)
            except Exception:
                logger.exception(
                    "Failed to load and create pushkin for kind %s", app_cfg["type"]
                )
                raise

        if len(self.pushkins) == 0:
            logger.error("No app IDs are configured. Edit sygnal.yaml to define some.")
            sys.exit(1)

        logger.info("Configured with app IDs: %r", self.pushkins.keys())
        logger.info("Setup completed")

    def _make_pushkin(self, app_name, app_config):
        app_type = app_config["type"]
        if "." in app_type:
            kind_split = app_type.rsplit(".", 1)
            to_import = kind_split[0]
            to_construct = kind_split[1]
        else:
            to_import = f"sygnal.{app_type}pushkin"
            to_construct = f"{app_type.capitalize()}Pushkin"

        logger.info("Importing pushkin module: %s", to_import)
        pushkin_module = importlib.import_module(to_import)
        logger.info("Creating pushkin: %s", to_construct)
        clarse = getattr(pushkin_module, to_construct)
        return clarse(app_name, self, app_config)

    def run(self):
        self._setup()
        port = int(self.config["http"]["port"])
        bind_addresses = self.config["http"]["bind_addresses"]
        pushgateway_api = PushGatewayApiServer(self)
        logger.info("Listening on port %d", port)

        start_deferred = gatherResults(
            [ensureDeferred(pushkin.start(self)) for pushkin in self.pushkins.values()],
            consumeErrors=True,
        )

        def on_started(_):
            for interface in bind_addresses:
                logger.info("Starting listening on %s port %d", interface, port)
                self.reactor.listenTCP(port, pushgateway_api.site, interface=interface)

        start_deferred.addCallback(on_started)

        logger.info("Starting pushkins")
        self.reactor.run()

    def shutdown(self):
        pass  # TODO


def parse_config():
    config_path = os.getenv("SYGNAL_CONF", "sygnal.yaml")
    with open(config_path) as file_handle:
        return yaml.safe_load(file_handle)


def check_config(config):
    UNDERSTOOD_CONFIG_FIELDS = {"apps", "http", "log"}

    def check_section(section_name, known_keys):
        nonunderstood = set(config[section_name].keys()).difference(known_keys)
        if len(nonunderstood) > 0:
            logger.warning(
                f"The following configuration fields in '{section_name}' are not understood: %s",
                nonunderstood,
            )

    nonunderstood = set(config.keys()).difference(UNDERSTOOD_CONFIG_FIELDS)
    if len(nonunderstood) > 0:
        logger.warning(
            "The following configuration fields are not understood: %s", nonunderstood
        )

    check_section("http", {"port", "bind_addresses"})
    check_section("log", {"file", "level"})
    check_section("db", {"dbfile"})


def merge_left_with_defaults(defaults, loaded_config):
    result = defaults.copy()

    # copy defaults or override them
    for k, v in result.items():
        if isinstance(v, dict):
            if k in loaded_config:
                result[k] = merge_left_with_defaults(v, loaded_config[k])
            else:
                result[k] = copy.deepcopy(v)
        elif k in loaded_config:
            result[k] = loaded_config[k]

    # copy things with no defaults
    for k, v in loaded_config.items():
        if k not in result:
            result[k] = v

    return result


if __name__ == "__main__":
    config = parse_config()
    config = merge_left_with_defaults(CONFIG_DEFAULTS, config)
    check_config(config)
    sygnal = Sygnal(config)
    sygnal.run()
