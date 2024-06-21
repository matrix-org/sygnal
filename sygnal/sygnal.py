# -*- coding: utf-8 -*-
# Copyright 2014 OpenMarket Ltd
# Copyright 2018, 2019 New Vector Ltd
# Copyright 2019-2020 The Matrix.org Foundation C.I.C.
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
import logging.config
import os
import sys
from typing import Any, Dict, Generator, Set, cast

import opentracing
import prometheus_client
import yaml
from opentracing import Tracer
from opentracing.scope_managers.asyncio import AsyncioScopeManager
from twisted.internet import asyncioreactor, defer
from twisted.internet.defer import Deferred, ensureDeferred
from twisted.internet.interfaces import (
    IReactorCore,
    IReactorFDSet,
    IReactorPluggableNameResolver,
    IReactorTCP,
    IReactorTime,
)
from twisted.python import log as twisted_log
from twisted.python.failure import Failure
from zope.interface import Interface

from sygnal.http import PushGatewayApiServer
from sygnal.notifications import Pushkin

logger = logging.getLogger(__name__)

CONFIG_DEFAULTS: Dict[str, Any] = {
    "http": {"port": 5000, "bind_addresses": ["127.0.0.1"]},
    "log": {"setup": {}, "access": {"x_forwarded_for": False}},
    "metrics": {
        "prometheus": {"enabled": False, "address": "127.0.0.1", "port": 8000},
        "opentracing": {
            "enabled": False,
            "implementation": None,
            "jaeger": {},
            "service_name": "sygnal",
        },
        "sentry": {"enabled": False},
    },
    "proxy": None,
    "apps": {},
}


class SygnalReactor(
    IReactorFDSet,
    IReactorPluggableNameResolver,
    IReactorTCP,
    IReactorCore,
    IReactorTime,
    Interface,
):
    pass


class Sygnal:
    def __init__(
        self,
        config: Dict[str, Any],
        custom_reactor: SygnalReactor,
        tracer: Tracer = opentracing.tracer,
    ):
        """
        Object that holds state for the entirety of a Sygnal instance.
        Args:
            config: Configuration for this Sygnal
            custom_reactor: a Twisted Reactor to use.
            tracer (optional): an OpenTracing tracer. The default is the no-op tracer.
        """
        self.config = config
        self.reactor = custom_reactor
        self.pushkins: Dict[str, Pushkin] = {}
        self.tracer = tracer

        logging_dict_config = config["log"]["setup"]
        logging.config.dictConfig(logging_dict_config)

        logger.debug("Started logging")

        observer = twisted_log.PythonLoggingObserver()
        observer.start()

        proxy_url = config.get("proxy")
        if proxy_url is not None:
            logger.info("Using proxy configuration from Sygnal configuration file")
        else:
            proxy_url = os.getenv("HTTPS_PROXY")
            if proxy_url:
                logger.info(
                    "Using proxy configuration from HTTPS_PROXY environment variable."
                )
                config["proxy"] = proxy_url

        sentrycfg = config["metrics"]["sentry"]
        if sentrycfg["enabled"] is True:
            import sentry_sdk

            logger.info("Initialising Sentry")
            sentry_sdk.init(sentrycfg["dsn"])

        if config.get("db") is not None:
            logger.warning(
                "Config includes the legacy 'db' option and will be ignored"
                " as Sygnal no longer uses a database, this field can be removed"
            )

        if config.get("database") is not None:
            logger.warning(
                "Config includes the legacy 'database' option and will be ignored"
                " as Sygnal no longer uses a database, this field can be removed"
            )

        promcfg = config["metrics"]["prometheus"]
        if promcfg["enabled"] is True:
            prom_addr = promcfg["address"]
            prom_port = int(promcfg["port"])
            logger.info(
                "Starting Prometheus Server on %s port %d", prom_addr, prom_port
            )

            prometheus_client.start_http_server(port=prom_port, addr=prom_addr or "")

        tracecfg = config["metrics"]["opentracing"]
        if tracecfg["enabled"] is True:
            if tracecfg["implementation"] == "jaeger":
                try:
                    import jaeger_client

                    jaeger_cfg = jaeger_client.Config(
                        config=tracecfg["jaeger"],
                        service_name=tracecfg["service_name"],
                        scope_manager=AsyncioScopeManager(),
                    )

                    jaeger_tracer = jaeger_cfg.initialize_tracer()
                    assert jaeger_tracer is not None
                    self.tracer = jaeger_tracer

                    logger.info("Enabled OpenTracing support with Jaeger")
                except ModuleNotFoundError:
                    logger.critical(
                        "You have asked for OpenTracing with Jaeger but do not have"
                        " the Python package 'jaeger_client' installed."
                    )
                    raise
            else:
                raise RuntimeError(
                    "Unknown OpenTracing implementation: %s.", tracecfg["impl"]
                )

    async def _make_pushkin(self, app_name: str, app_config: Dict[str, Any]) -> Pushkin:
        """
        Load and instantiate a pushkin.
        Args:
            app_name: The pushkin's app_id
            app_config: The pushkin's configuration

        Returns:
            A pushkin of the desired type.
        """
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
        return await clarse.create(app_name, self, app_config)

    async def make_pushkins_then_start(self) -> None:
        for app_id, app_cfg in self.config["apps"].items():
            try:
                self.pushkins[app_id] = await self._make_pushkin(app_id, app_cfg)
            except Exception:
                logger.error(
                    "Failed to load and create pushkin for kind '%s'" % app_cfg["type"]
                )
                raise

        if len(self.pushkins) == 0:
            raise RuntimeError(
                "No app IDs are configured. Edit sygnal.yaml to define some."
            )

        logger.info("Configured with app IDs: %r", self.pushkins.keys())

        pushgateway_api = PushGatewayApiServer(self)
        port = int(self.config["http"]["port"])
        for interface in self.config["http"]["bind_addresses"]:
            logger.info("Starting listening on %s port %d", interface, port)
            self.reactor.listenTCP(port, pushgateway_api.site, 50, interface=interface)

    def run(self) -> None:
        """
        Attempt to run Sygnal and then exit the application.
        """

        @defer.inlineCallbacks
        def start() -> Generator[Deferred[Any], Any, Any]:
            try:
                yield ensureDeferred(self.make_pushkins_then_start())
            except Exception:
                # Print the exception and bail out.
                print("Error during startup:", file=sys.stderr)

                # this gives better tracebacks than traceback.print_exc()
                Failure().printTraceback(file=sys.stderr)

                if self.reactor.running:
                    self.reactor.stop()

        self.reactor.callWhenRunning(start)
        self.reactor.run()


def parse_config() -> Dict[str, Any]:
    """
    Find and load Sygnal's configuration file.
    Returns:
        A loaded configuration.
    """
    config_path = os.getenv("SYGNAL_CONF", "sygnal.yaml")
    print("Using configuration file: %s" % config_path, file=sys.stderr)
    try:
        with open(config_path) as file_handle:
            return yaml.safe_load(file_handle)
    except FileNotFoundError:
        logger.critical(
            "Could not find configuration file!\n" "Path: %s\n" "Absolute Path: %s",
            config_path,
            os.path.realpath(config_path),
        )
        raise


def check_config(config: Dict[str, Any]) -> None:
    """
    Lightly check the configuration and issue warnings as appropriate.
    Args:
        config: The loaded configuration.
    """
    UNDERSTOOD_CONFIG_FIELDS = CONFIG_DEFAULTS.keys()

    def check_section(
        section_name: str, known_keys: Set[str], cfgpart: Dict[str, Any] = config
    ) -> None:
        nonunderstood = set(cfgpart[section_name].keys()).difference(known_keys)
        if len(nonunderstood) > 0:
            logger.warning(
                f"The following configuration fields in '{section_name}' "
                f"are not understood: %s",
                nonunderstood,
            )

    nonunderstood = set(config.keys()).difference(UNDERSTOOD_CONFIG_FIELDS)
    if len(nonunderstood) > 0:
        logger.warning(
            "The following configuration sections are not understood: %s", nonunderstood
        )

    check_section("http", {"port", "bind_addresses"})
    check_section("log", {"setup", "access"})
    check_section(
        "access", {"file", "enabled", "x_forwarded_for"}, cfgpart=config["log"]
    )
    check_section("metrics", {"opentracing", "sentry", "prometheus"})
    check_section(
        "opentracing",
        {"enabled", "implementation", "jaeger", "service_name"},
        cfgpart=config["metrics"],
    )
    check_section(
        "prometheus", {"enabled", "address", "port"}, cfgpart=config["metrics"]
    )
    check_section("sentry", {"enabled", "dsn"}, cfgpart=config["metrics"])


def merge_left_with_defaults(
    defaults: Dict[str, Any], loaded_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge two configurations, with one of them overriding the other.
    Args:
        defaults: A configuration of defaults
        loaded_config: A configuration, as loaded from disk.

    Returns:
        A merged configuration, with loaded_config preferred over defaults.
    """
    result = defaults.copy()

    if loaded_config is None:
        return result

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


def main() -> None:
    # TODO we don't want to have to install the reactor, when we can get away with
    #   it
    asyncioreactor.install()

    # we remove the global reactor to make it evident when it has accidentally
    # been used:
    # ! twisted.internet.reactor = None
    # TODO can't do this ^ yet, since twisted.internet.task.{coiterate,cooperate}
    #   (indirectly) depend on the globally-installed reactor and there's no way
    #   to pass in a custom one.
    #   and twisted.web.client uses twisted.internet.task.cooperate

    config = parse_config()
    config = merge_left_with_defaults(CONFIG_DEFAULTS, config)
    check_config(config)
    custom_reactor = cast(SygnalReactor, asyncioreactor.AsyncioSelectorReactor())
    sygnal = Sygnal(config, custom_reactor)
    sygnal.run()


if __name__ == "__main__":
    main()
