Introduction
============

Sygnal is a reference Push Gateway for `Matrix <https://matrix.org/>`_.

See https://matrix.org/docs/spec/client_server/r0.5.0#id134
for a high level overview of how notifications work in Matrix.

https://matrix.org/docs/spec/push_gateway/r0.1.0
describes the protocol that Matrix Home Servers use to send notifications to Push Gateways such as Sygnal.


Contributing
============
Looking to contribute to Sygnal? See `CONTRIBUTING.md <CONTRIBUTING.md>`_


Setup
=====
Sygnal is configured through a YAML configuration file.
By default, this configuration file is assumed to be named ``sygnal.yaml`` and to be in the working directory.
To change this, set the ``SYGNAL_CONF`` environment variable to the path to your configuration file.
A sample configuration file is provided in this repository;
see ``sygnal.yaml.sample``.

Sygnal supports using either SQLite3 or PostgreSQL as a database backend. See the ``sygnal.yaml.sample``
for more information on how to configure.

The `apps:` section is where you set up different apps that are to be handled.
Each app should be given its own subsection, with the key of that subsection being the app's ``app_id``.
Keys in this section take the form of the ``app_id``, as specified when setting up a Matrix pusher
(see https://matrix.org/docs/spec/client_server/r0.5.0#post-matrix-client-r0-pushers-set).

See the sample configuration for examples.


App Types
---------
There are two supported App Types:

apns
  This sends push notifications to iOS apps via the Apple Push Notification
  Service (APNS).

  Expected configuration depends on which kind of authentication you wish to use:

  |

  For certificate-based authentication:
    It expects:

    * the ``certfile`` parameter to be a path relative to
      sygnal's working directory of a PEM file containing the APNS certificate and
      unencrypted private key.

  For token-based authentication:
    It expects:

    * the 'keyfile' parameter to be a path relative to Sygnal's working directory of a p8 file
    * the 'key_id' parameter
    * the 'team_id' parameter
    * the 'topic' parameter

  For either type:
    It can accept:
    
    * the 'platform' parameter which determines whether the production or sandbox APNS environment is used. Valid values are 'production' or 'sandbox'. If not provided, 'production' is used.

gcm
  This sends messages via Google/Firebase Cloud Messaging (GCM/FCM) and hence can be used
  to deliver notifications to Android apps. It expects the 'api_key' parameter
  to contain the 'Server key', which can be acquired from Firebase Console at:
  ``https://console.firebase.google.com/project/<PROJECT NAME>/settings/cloudmessaging/``


Using an HTTP Proxy for outbound traffic
----------------------------------------

Sygnal will, by default, automatically detect an ``HTTPS_PROXY``
environment variable on start-up.

If one is present, it will be used for outbound traffic to APNs and GCM/FCM.

Currently only HTTP proxies with the CONNECT method are supported.
(Both APNs and FCM use HTTPS traffic which is tunnelled in a CONNECT tunnel.)

If you wish, you can instead configure a HTTP CONNECT proxy in ``sygnal.yaml``.


Pusher ``data`` configuration
=============================

The following parameters can be specified in the `data` dictionary which is given when configuring the pusher
via `POST /_matrix/client/r0/pushers/set <https://matrix.org/docs/spec/client_server/latest#post-matrix-client-r0-pushers-set>`_ :

* ``default_payload``: a dictionary which defines the basic payload to be sent to the notification service.
  Sygnal will merge information specific to the push event into this dictionary. If unset, the empty dictionary is used.

  This can be useful for clients to specify default push payload content. For instance, iOS clients will have
  freedom to use silent/mutable notifications and be able to set some default alert/sound/badge fields.


Running
=======

With default configuration file name of ``sygnal.yaml``:
    ``python -m sygnal.sygnal``

With custom configuration file name:
    ``SYGNAL_CONF=/path/to/custom_sygnal.conf python -m sygnal.sygnal``

Python 3.7 or higher is required.


Log Rotation
============

Sygnal's logging appends to files but does not use a rotating logger.
The recommended configuration is therefore to use ``logrotate``.
The log file will be automatically reopened if the log file changes, for example
due to ``logrotate``.


More Documentation
==================

More documentation for Sygnal is available in the ``docs`` directory:

* `Notes for Application Developers <docs/applications.md>`_
* `Troubleshooting <docs/troubleshooting.md>`_
