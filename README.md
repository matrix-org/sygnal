Sygnal is now actively maintained at [element-hq/sygnal](https://github.com/element-hq/sygnal)
=================================================================================================

Sygnal is a reference Push Gateway for [Matrix](https://matrix.org/)
developed from 2019 through 2025 as part of the Matrix.org Foundation.
The Matrix.org Foundation is not able to resource maintenance of Sygnal
and it continues to be developed by Element.

See [The future of Synapse and Dendrite blog post](https://matrix.org/blog/2023/11/06/future-of-synapse-dendrite/) for more information.


Introduction
============

See https://spec.matrix.org/latest/push-gateway-api/#overview for a high
level overview of how notifications work in Matrix.

The [Matrix Specification](https://spec.matrix.org/latest/push-gateway-api/)
describes the protocol that Matrix Home Servers use to send notifications to Push
Gateways such as Sygnal.


Setup
=====

Sygnal is configured through a YAML configuration file. By default, this
configuration file is assumed to be named `sygnal.yaml` and to be in the
working directory. To change this, set the `SYGNAL_CONF` environment
variable to the path to your configuration file. A sample configuration
file is provided in this repository; see `sygnal.yaml.sample`.

The `apps:` section is where you set up different apps that
are to be handled. Each app should be given its own subsection, with the
key of that subsection being the app's `app_id`. Keys in this section
take the form of the `app_id`, as specified when setting up a Matrix
pusher (see
https://spec.matrix.org/latest/client-server-api/#post_matrixclientv3pushersset).

See the sample configuration for examples.

You can find a docker image for sygnal [on DockerHub](https://hub.docker.com/r/matrixdotorg/sygnal).


App Types
---------

There are two supported App Types:


### apns

This sends push notifications to iOS apps via the Apple Push
Notification Service (APNS).

The expected configuration depends on which kind of authentication you
wish to use.

For certificate-based authentication, it expects:

- the `certfile` parameter to be a path relative to sygnal's
  working directory of a PEM file containing the APNS
  certificate and unencrypted private key.

For token-based authentication, it expects:

- the `keyfile` parameter to be a path relative to Sygnal's
  working directory of a p8 file
- the `key_id` parameter
- the `team_id` parameter
- the `topic` parameter, which is most commonly the 'Bundle Identifier' for your
  iOS application

For either type, it can accept:

- the `platform` parameter which determines whether the production or sandbox
  APNS environment is used.
  Valid values are 'production' or 'sandbox'. If not provided, 'production' is used.
- the `push_type` parameter which determines what value for the `apns-push-type` header is sent to
  APNs. If not provided, the header is not sent.
- the `convert_device_token_to_hex` parameter which determines if the
  token provided from the client is b64 decoded and converted to
  hex. Some client libraries already provide the token in hex, and
  this should be set to `False` if so.

### gcm

This sends messages via Google/Firebase Cloud Messaging (GCM/FCM)
and hence can be used to deliver notifications to Android apps.

The expected configuration depends on which version of the firebase api you
wish to use.

For legacy API, it expects:

- the `api_key` parameter to contain the `Server key`,
  which can be acquired from Firebase Console at:
  `https://console.firebase.google.com/project/<PROJECT NAME>/settings/cloudmessaging/`
    
For API v1, it expects:

- the `api_version` parameter to contain `v1`
- the `project_id` parameter to contain the `Project ID`,
  which can be acquired from Firebase Console at:
  `https://console.cloud.google.com/project/<PROJECT NAME>/settings/general/`
- the `service_account_file` parameter to contain the path to the service account file,
  which can be acquired from Firebase Console at:
  `https://console.firebase.google.com/project/<PROJECT NAME>/settings/serviceaccounts/adminsdk`

Using an HTTP Proxy for outbound traffic
----------------------------------------

Sygnal will, by default, automatically detect an `HTTPS_PROXY`
environment variable on start-up.

If one is present, it will be used for outbound traffic to APNs and
GCM/FCM.

Currently only HTTP proxies with the CONNECT method are supported. (Both
APNs and FCM use HTTPS traffic which is tunnelled in a CONNECT tunnel.)

If you wish, you can instead configure a HTTP CONNECT proxy in
`sygnal.yaml`.


Pusher `data` configuration
===========================

The following parameters can be specified in the `data`
dictionary which is given when configuring the pusher via
[POST /_matrix/client/v3/pushers/set](https://spec.matrix.org/latest/client-server-api/#post_matrixclientv3pushersset):

- `default_payload`: a dictionary which defines the basic payload to
  be sent to the notification service. Sygnal will merge information
  specific to the push event into this dictionary. If unset, the empty
  dictionary is used.

  This can be useful for clients to specify default push payload
  content. For instance, iOS clients will have freedom to use
  silent/mutable notifications and be able to set some default
  alert/sound/badge fields.


Running
=======

### Python

With default configuration file name of `sygnal.yaml`:

```sh
python -m sygnal.sygnal
```

With custom configuration file name:

```sh
SYGNAL_CONF=/path/to/custom_sygnal.conf python -m sygnal.sygnal
```

Python 3.8 or higher is required.


### Container

The example below uses Podman but should work the same by substituting `podman` with `docker`. First create a volume to store your configuration and any necessary key files:

```
podman volume create sygnal
cp /path/to/sygnal.conf /path/to/volumes/sygnal/_data
cp /path/to/keyfile.p8 /path/to/volumes/sygnal/_data
```

We're going to mount the volume as `/sygnal` so make sure your configuration references any key files in this directory. Now you can pull the image and run the container:

```
podman image pull docker.io/matrixdotorg/sygnal
podman run -d --name sygnal -p 5000:5000 -v sygnal:/sygnal -e SYGNAL_CONF=/sygnal/sygnal.yaml sygnal:latest
```


Log Rotation
============

Sygnal's logging appends to files but does not use a rotating logger.
The recommended configuration is therefore to use `logrotate`. The log
file will be automatically reopened if the log file changes, for example
due to `logrotate`.


More Documentation
==================

More documentation for Sygnal is available in the `docs` directory:

-   [Notes for Application Developers](docs/applications.md)
-   [Troubleshooting](docs/troubleshooting.md)
