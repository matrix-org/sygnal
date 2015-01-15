Introduction
============

sygnal is a reference Push Gateway for Matrix.


Running
=======
sygnal is a plain WSGI app, although when used with gunicorn can
use gunicorn's extra hook to perform a clean shutdown which tries
as hard a spossible to ensure no messages are lost.

To run with gunicorn:

gunicorn -c gunicorn_config.py sygnal:app

There are two config files:
 * sygnal.cfg (The app-specific config file)
 * gunicorn_config.py (gunicorn's config file)

Gunicorn maintains its own logging in addition to the app's,
so the access_log and error_log contain gunicorn's accesses
and gunicorn specific errors. The log file in sygnal.cfg
contains app level logging.

Clean shutdown
==============
The code for APNS uses a grace period where it waits for errors
to come down the socket before declaring it safe for the app to
shut down (due to the design of APNS). Sygnal can be restarted
as such:
 * Send SIGTERM to the main gunicorn arbiter process
 * At this point, gunicorn will close the listening socket
   but continue shutting down (if necessary). Notification pokes
   will be refused (which is okay - home servers retry these)
 * A new instance of sygnal can now be started up while the old
   copy continues shutting down.

Sending a SIGHUP to gunicorn to instruct it to reload should
also work.

Log Rotation
============
Gunicorn appends to files but does not use a rotating logger.
Sygnal's app logging does the same. Gunicorn will re-open
all log files (including the app's) when sent SIGUSR1.
The recommended configuration is therefore to use logrotate.
