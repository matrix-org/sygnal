Introduction
============

sygnal is a reference Push Gateway for Matrix (http://matrix.org/).

See
https://github.com/matrix-org/matrix-doc/blob/master/drafts/push_overview.rst
for a high level overview of how notifications work in Matrix.

https://github.com/matrix-org/matrix-doc/blob/master/drafts/push_pgwapi.rst
described the protocol that Matrix Home Servers use to send notifications to
Push Gateways such as sygnal.

Setup
=====
sygnal is a plain WSGI app, although when used with gunicorn can
use gunicorn's extra hook to perform a clean shutdown which tries as hard a
spossible to ensure no messages are lost.

There are two config files:
 * sygnal.cfg (The app-specific config file)
 * gunicorn_config.py (gunicorn's config file)

sygnal.cfg contains configuration for sygnal itself. This includes the location
and level of sygnal's log file. The [apps] section is where you set up different
apps that are to be handled. Each app has a 'type' and its own configuration. It
also has an App ID: this is the app_id as specified when setting up a Matrix
pusher (see
https://github.com/matrix-org/matrix-doc/blob/master/drafts/push_csapi.rst). The
name of an app configuration key is the app_id and the name of the
configuration key, joined by a single dot ('.'). App Types are listed below.

The gunicorn sample config contains everything necessary to run sygnal from
gunicorn. The shutdown hook handles clean shutdown. You can customise other
aspects of this file as you wish to change, for example, the log location or the
bind port.

Note that sygnal uses gevent. You should therefore not change the worker class
or the number of workers (which should be 1: in gevent, a single worker uses
multiple greenlets to handle all the requests).

App Types
---------
At present, the only supported App Type is 'apns'. 

apns
  This sends push notifications to iOS apps via the Apple Push Notification
  Service (APNS). It expects the 'certfile' parameter to be a path relative to
  sygnal's working directory of a PEM file containing the APNS certificate and
  unencrypted private key.

Running
=======
To run with gunicorn:

gunicorn -c gunicorn_config.py sygnal:app

You can customise the gunicorn_config.py to determine whether this daemonizes or runs in the foreground.

Gunicorn maintains its own logging in addition to the app's, so the access_log
and error_log contain gunicorn's accesses and gunicorn specific errors. The log
file in sygnal.cfg contains app level logging.

Clean shutdown
==============
The code for APNS uses a grace period where it waits for errors to come down the
socket before declaring it safe for the app to shut down (due to the design of
APNS). Terminating using SIGTERM performs a clean shutdown::

    kill -TERM `cat sygnal.pid`

Restarting sygnal using SIGHUP will handle this gracefully::

    kill -HUP `cat sygnal.pid`

Log Rotation
============
Gunicorn appends to files but does not use a rotating logger.
Sygnal's app logging does the same. Gunicorn will re-open all log files
(including the app's) when sent SIGUSR1.  The recommended configuration is
therefore to use logrotate.
