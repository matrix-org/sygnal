Sygnal 0.9.3 (2021-04-22)
=========================

Features
--------

- Prevent the push key from being rejected for temporary errors and oversized payloads, add TTL logging, and support `events_only` push data flag. ([\#212](https://github.com/matrix-org/sygnal/issues/212))
- WebPush: add support for Urgency and Topic header ([\#213](https://github.com/matrix-org/sygnal/issues/213))


Bugfixes
--------

- Fix a long-standing bug where invalid JSON would be accepted over the HTTP interfaces. ([\#216](https://github.com/matrix-org/sygnal/issues/216))
- Limit the size of requests received from HTTP clients. ([\#220](https://github.com/matrix-org/sygnal/issues/220))


Updates to the Docker image
---------------------------

- Remove manually added GeoTrust Root CA certificate from docker image as Apple is no longer using it. ([\#208](https://github.com/matrix-org/sygnal/issues/208))


Improved Documentation
----------------------

- Make `CONTIBUTING.md` more explicit about how to get tests passing. ([\#188](https://github.com/matrix-org/sygnal/issues/188))
- Update `CONTRIBUTING.md` to specify how to run code style and type checks with Tox, and add formatting to code block samples. ([\#193](https://github.com/matrix-org/sygnal/issues/193))
- Document how to work around pip installation timeout errors. Contributed by Omar Mohamed. ([\#215](https://github.com/matrix-org/sygnal/issues/215))


Internal Changes
----------------

- Update Tox to run in the installed version of Python (instead of specifying Python 3.7) and to consider specific paths and folders while running checks, instead of the whole repository (potentially including unwanted files and folders, e.g. the virtual environment). ([\#193](https://github.com/matrix-org/sygnal/issues/193))
- Make development dependencies available as extras. Contributed by Hillery Shay. ([\#194](https://github.com/matrix-org/sygnal/issues/194))
- Update `setup.py` to specify that a minimum version of Python greater or equal to 3.7 is required. Contributed by Tawanda Moyo. ([\#207](https://github.com/matrix-org/sygnal/issues/207))
- Port CI checks to Github Actions. ([\#210](https://github.com/matrix-org/sygnal/issues/210), [\#219](https://github.com/matrix-org/sygnal/issues/219))
- Upgrade development dependencies. Contributed by Omar Mohamed ([\#214](https://github.com/matrix-org/sygnal/issues/214))
- Set up `coverage.py` to run in tox environment, and add html reports ([\#217](https://github.com/matrix-org/sygnal/issues/217))


Sygnal v0.9.2 (2021-03-29)
==========================

Features
--------

- Add `allowed_endpoints` option as an understood config option for WebPush pushkins. ([\#184](https://github.com/matrix-org/sygnal/issues/184))
- Add `ttl` option as an understood config option for WebPush pushkins to make delivery more reliable ([\#185](https://github.com/matrix-org/sygnal/issues/185))


Sygnal 0.9.1 (2021-03-23)
=========================

Features
--------

- Add `allowed_endpoints` configuration option for limiting the endpoints that WebPush pushkins will contact. ([\#182](https://github.com/matrix-org/sygnal/issues/182))


Bugfixes
--------

- Fix bug where the requests from different WebPush devices could bleed into each other. ([\#180](https://github.com/matrix-org/sygnal/issues/180))
- Fix bug when using a HTTP proxy where connections would sometimes fail to establish. ([\#181](https://github.com/matrix-org/sygnal/issues/181))


Sygnal 0.9.0 (2021-03-19)
=========================

Features
--------

- Add experimental support for WebPush pushkins. ([\#177](https://github.com/matrix-org/sygnal/issues/177))


Bugfixes
--------

- Fix erroneous warning log line when setting the `max_connections` option in a GCM app config. ([\#157](https://github.com/matrix-org/sygnal/issues/157))
- Fix bug where the `sygnal_inflight_request_limit_drop` metric would not appear in prometheus until requests were actually dropped. ([\#172](https://github.com/matrix-org/sygnal/issues/172))
- Fix bug where Sygnal would not recover after losing connection to the database. ([\#179](https://github.com/matrix-org/sygnal/issues/179))


Improved Documentation
----------------------

- Add preliminary documentation ([Troubleshooting](docs/troubleshooting.md) and [Application Developers' Notes](docs/applications.md)). ([\#150](https://github.com/matrix-org/sygnal/issues/150), [\#154](https://github.com/matrix-org/sygnal/issues/154), [\#158](https://github.com/matrix-org/sygnal/issues/158))
- Add a note to the releasing doc asking people to inform EMS and customers during the release process. ([\#155](https://github.com/matrix-org/sygnal/issues/155))


Internal Changes
----------------

- Remove a source of noisy (but otherwise harmless) exceptions in tests. ([\#149](https://github.com/matrix-org/sygnal/issues/149))
- Add tests for HTTP Proxy support. ([\#151](https://github.com/matrix-org/sygnal/issues/151), [\#152](https://github.com/matrix-org/sygnal/issues/152))
- Remove extraneous log line. ([\#174](https://github.com/matrix-org/sygnal/issues/174))
- Fix type hints due to Twisted upgrade. ([\#178](https://github.com/matrix-org/sygnal/issues/178))


Sygnal 0.8.2 (2020-08-06)
=========================

Features
--------

- Add the ability to configure custom FCM options, which is necessary for using iOS with Firebase. ([\#145](https://github.com/matrix-org/sygnal/issues/145))
- Add a Prometheus metric (`sygnal_inflight_request_limit_drop`) that shows the number of notifications dropped due to exceeding the in-flight concurrent request limit. ([\#146](https://github.com/matrix-org/sygnal/issues/146))


Sygnal 0.8.1 (2020-07-28)
=========================

Updates to the Docker image
---------------------------

- Include GeoTrust Global CA's certificate in the Docker image as it is needed for APNs (and was removed by Debian). ([\#141](https://github.com/matrix-org/sygnal/issues/141))


Sygnal 0.7.2 (2020-07-28)
=========================

Updates to the Docker image
---------------------------

- Include GeoTrust Global CA's certificate in the Docker image as it is needed for APNs (and was removed by Debian). ([\#141](https://github.com/matrix-org/sygnal/issues/141))


Sygnal 0.8.0 (2020-07-27)
=========================

Features
--------

- Add support for HTTP CONNECT proxies on outbound FCM and APNs traffic, with optional support for HTTP Proxy Basic Authentication. ([\#130](https://github.com/matrix-org/sygnal/issues/130))
- Add support for per-pushkin in-flight request limiting. ([\#132](https://github.com/matrix-org/sygnal/issues/132))


Internal Changes
----------------

- Fixed MyPy errors so it can be enabled in CI and gradually be increased in coverage. ([\#131](https://github.com/matrix-org/sygnal/issues/131))
- Attempt the same number of retries for both GCM and APNS. ([\#133](https://github.com/matrix-org/sygnal/issues/133))
- Use tox for tests and linting. ([\#134](https://github.com/matrix-org/sygnal/issues/134))
- Include libpq5 in the docker image. ([\#135](https://github.com/matrix-org/sygnal/issues/135))


Sygnal 0.7.1 (2020-07-27)
=========================

Security advisory
-----------------

This version of Sygnal updates the minimum version of the `aioapns` dependency
to version `1.10` which addresses a TLS hostname validation bug in `aioapns`.

Sygnal was vulnerable to a man-in-the-middle attack on APNs data if someone
could spoof your DNS or otherwise redirect your APNs traffic.

This issue affects any Sygnal deployments that make use of APNs certificate
authentication (i.e. those with `certfile: something.pem` in the configuration).

Administrators are encouraged to upgrade.


Bugfixes
--------

- Update minimum version of `aioapns` dependency to 1.10, which has security fixes. ([\#139](https://github.com/matrix-org/sygnal/issues/139))


Sygnal 0.7.0 (2020-06-24)
=========================

Features
--------

- Use `default_payload` from the device data for both APNS and GCM payloads. ([\#127](https://github.com/matrix-org/sygnal/issues/127))


Improved Documentation
----------------------

- Note information about Docker files in release instructions. ([\#126](https://github.com/matrix-org/sygnal/issues/126))


Internal Changes
----------------

- Improve logging if a pushkin cannot be created. ([\#125](https://github.com/matrix-org/sygnal/issues/125))


Sygnal 0.6.0 (2020-05-12)
=========================

Features
--------

- Report the APNS certificate expiry as a prometheus metric. ([\#106](https://github.com/matrix-org/sygnal/issues/106), [\#112](https://github.com/matrix-org/sygnal/issues/112))
- Change APNS payload to be mutable and include the `event_id` in payload. ([\#114](https://github.com/matrix-org/sygnal/issues/114))


Bugfixes
--------

- Sygnal will no longer warn about the `database` config field being not understood. ([\#100](https://github.com/matrix-org/sygnal/issues/100))
- Log errors during start-up and fix the sample logging config. ([\#122](https://github.com/matrix-org/sygnal/issues/122))


Improved Documentation
----------------------

- Document platform value for APNS apps ([\#110](https://github.com/matrix-org/sygnal/issues/110))


Internal Changes
----------------

- Add Dockerfile. ([\#63](https://github.com/matrix-org/sygnal/issues/63))


Sygnal 0.5.0 (2020-04-24)
=========================

Features
----------------

- Reuse Configurations With asterisk App IDs and Token Based APNS Auth ([\#108](https://github.com/matrix-org/sygnal/pull/108))


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
