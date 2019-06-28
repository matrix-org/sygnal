Introduction
============

Sygnal is a reference Push Gateway for Matrix (http://matrix.org/).

See
https://matrix.org/docs/spec/client_server/r0.5.0#id510 for a high level overview of how notifications work in Matrix.

https://matrix.org/docs/spec/push_gateway/r0.1.0
describes the protocol that Matrix Home Servers use to send notifications to
Push Gateways such as Sygnal.

Setup
=====
sygnal is a plain WSGI app, although these instructions use gunicorn which
will create a complete, standalone webserver.  When used with gunicorn,
sygnal can use gunicorn's extra hook to perform a clean shutdown which tries as
hard as possible to ensure no messages are lost.

There are two config files: # TODO
 * sygnal.conf (The app-specific config file)
 * gunicorn_config.py (gunicorn's config file)

sygnal.conf contains configuration for sygnal itself. This includes the location
and level of sygnal's log file. The [apps] section is where you set up different
apps that are to be handled. Keys in this section take the form of the app_id
and the name of the configuration key, joined by a single dot ('.'). The app_id
is as specified when setting up a Matrix pusher (see
http://matrix.org/docs/spec/client_server/r0.2.0.html#post-matrix-client-r0-pushers-set). So for example, the `type` for
the App ID of `com.example.myapp.ios.prod` would be specified as follows::

  com.example.myapp.ios.prod.type = foobar

By default sygnal.conf is assumed to be in the working directory, but the path
can be overriden by setting the `sygnal.conf` environment variable.

The gunicorn sample config contains everything necessary to run sygnal from
gunicorn. The shutdown hook handles clean shutdown. You can customise other
aspects of this file as you wish to change, for example, the log location or the
bind port.

Note that sygnal uses gevent. You should therefore not change the worker class
or the number of workers (which should be 1: in gevent, a single worker uses
multiple greenlets to handle all the requests).

App Types
---------
There are two supported App Types:

apns
  This sends push notifications to iOS apps via the Apple Push Notification
  Service (APNS). It expects either:
  
  - the 'certfile' parameter to be a path relative to
  sygnal's working directory of a PEM file containing the APNS certificate and
  unencrypted private key.
  - OR:
	- the 'keyfile' parameter to be a path relative to Sygnal's working directory of a p8 file
	- # TODO

gcm
  This sends messages via Google Cloud Messaging (GCM) and hence can be used
  to deliver notifications to Android apps. It expects the 'apiKey' parameter
  to contain the secret GCM key.

Running
=======

`python -m sygnal.sygnal`

Python 3.6 or higher is required. You may therefore need to use e.g. `python3.6` on your system. # TODO suggest venv?

Clean shutdown
==============
# TODO this is obsolete
The code for APNS uses a grace period where it waits for errors to come down the
socket before declaring it safe for the app to shut down (due to the design of
APNS). Terminating using SIGTERM performs a clean shutdown::

    kill -TERM `cat sygnal.pid`

Restarting sygnal using SIGHUP will handle this gracefully::

    kill -HUP `cat sygnal.pid`

Log Rotation
============
# obsolete-ish
Gunicorn appends to files but does not use a rotating logger.
Sygnal's app logging does the same. Gunicorn will re-open all log files
(including the app's) when sent SIGUSR1.  The recommended configuration is
therefore to use logrotate.
