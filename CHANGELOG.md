Sygnal 0.4.1 (2020-04-02)
=========================

Bugfixes
--------

- Fix an issue where using PostgreSQL would cause Sygnal to crash ([\#95](https://github.com/matrix-org/sygnal/issues/95))


Sygnal 0.4.0 (2020-03-31)
=========================

**NOTE**: The config schema has changed. The `db` config section has been replaced
by `database`. Example configuration can be found in `sygnal.yaml.sample`. `db` will
continue to work, but the section is deprecated and may be removed in a future release.

Features
--------

- Add support for PostgreSQL ([\#91](https://github.com/matrix-org/sygnal/issues/91))


Internal Changes
----------------

- Replace occurances of 'assertEquals' with 'assertEqual' to reduce deprecation noise while running tests. ([\#93](https://github.com/matrix-org/sygnal/issues/93))


Sygnal 0.3.0 (2020-03-24)
=========================

Features
--------

- Add prometheus metric for the number of requests in flight. ([\#87](https://github.com/matrix-org/sygnal/issues/87))
- Add prometheus metrics to track pushkin things. ([\#88](https://github.com/matrix-org/sygnal/issues/88))


Bugfixes
--------

- Fix warnings about `finish()` after disconnect. ([\#84](https://github.com/matrix-org/sygnal/issues/84))
- Fix a bug which meant that requests were logged with an invalid timestamp. ([\#86](https://github.com/matrix-org/sygnal/issues/86))


Internal Changes
----------------

- Change how we stub out HTTP requests in the tests. ([\#85](https://github.com/matrix-org/sygnal/issues/85))


Sygnal 0.2.4 (2019-08-29)
===

- Fix typo leading to poor handling of 5xx GCM response codes.
- Handle 404 GCM response codes.

Sygnal 0.2.3 (2019-08-14)
===

- Actually fix GCM connection limiting, and exception handling of exceptions
  that occur whilst reading the response body.
- Reduce logging for successful requests.
- Improve TLS performance to reduce CPU usage.
- Add a Prometheus metric that tracks the time taken to handle a `/notify` request.
- Add a `/health` endpoint for checking whether Sygnal is up.

Sygnal 0.2.2 (2019-08-12)
===

- Fix GCM connection limiting.
- Clean up exception handling code.

Sygnal 0.2.1 (2019-08-08)
===

- Declare sentry-sdk as a dependency.
- Obey GCM maximum connections count.
- Document `max_connections` config option in GCM.
- Separate Twisted's logging and the access logging.

Sygnal 0.2.0 (2019-08-02)
===

This is a rewrite of Sygnal 0.0.1.
Before upgrading, please note that **Python 3.7 or higher is required**.

- Use new version of Apple Push Notification service (HTTP/2 protocol)
- Depend on Python 3.7+
- Depend on Twisted for async
- Add support for OpenTracing with Jaeger Tracing
