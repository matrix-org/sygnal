
### Sygnal 0.2.4 (2019-08-29)

- Fix typo leading to poor handling of 5xx GCM response codes.
- Handle 404 GCM response codes.

### Sygnal 0.2.3 (2019-08-14)

- Actually fix GCM connection limiting, and exception handling of exceptions
  that occur whilst reading the response body.
- Reduce logging for successful requests.
- Improve TLS performance to reduce CPU usage.
- Add a Prometheus metric that tracks the time taken to handle a `/notify` request.
- Add a `/health` endpoint for checking whether Sygnal is up.

### Sygnal 0.2.2 (2019-08-12)

- Fix GCM connection limiting.
- Clean up exception handling code.

### Sygnal 0.2.1 (2019-08-08)

- Declare sentry-sdk as a dependency.
- Obey GCM maximum connections count.
- Document `max_connections` config option in GCM.
- Separate Twisted's logging and the access logging.

### Sygnal 0.2.0 (2019-08-02)

This is a rewrite of Sygnal 0.0.1.
Before upgrading, please note that **Python 3.7 or higher is required**.

- Use new version of Apple Push Notification service (HTTP/2 protocol)
- Depend on Python 3.7+
- Depend on Twisted for async
- Add support for OpenTracing with Jaeger Tracing
