Introduction
============

Sygnal is a reference Push Gateway for `Matrix <https://matrix.org/>`_.

See https://matrix.org/docs/spec/client_server/r0.5.0#id134
for a high level overview of how notifications work in Matrix.

https://matrix.org/docs/spec/push_gateway/r0.1.0
describes the protocol that Matrix Home Servers use to send notifications to Push Gateways such as Sygnal.

Setup
=====
Sygnal is configured through a YAML configuration file.
By default, this configuration file is assumed to be named ``sygnal.yaml`` and to be in the working directory.
To change this, set the ``SYGNAL_CONF`` environment variable to the path to your configuration file.
A sample configuration file is provided in this repository;
see ``sygnal.yaml.sample``.

The `apps:` section is where you set up different apps that are to be handled.
Each app should be given its own subsection, with the key of that subsection being the app's ``app_id``.
Keys in this section take the form of the ``app_id``, as specified when setting up a Matrix pusher
(see https://matrix.org/docs/spec/client_server/r0.5.0#post-matrix-client-r0-pushers-set).

See the sample configuration for examples.

App Types
---------

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

gcm
  This sends messages via Google/Firebase Cloud Messaging (GCM/FCM) and hence can be used
  to deliver notifications to Android apps. It expects the 'api_key' parameter
  to contain the 'Server key', which can be acquired from Firebase Console at:
  ``https://console.firebase.google.com/project/<PROJECT NAME>/settings/cloudmessaging/``

firebase
  This sends push notifications to iOS and Android using Firebase.

Other configuration options
---------------------------
event_handlers:
  The firebase and apns pushkin are also configurable with event handlers,
  making it possible to specify different handlers for certain events.

  Currently three types of handlers are available:

  voip:
    | For apns this handler uses the voIP type notification and includes VoIP
    | specific information to the device. On firebase the data pushes are used to send the
    | same information

  event:
    | Includes only the most basic information (event_id and room_id) and
    | uses no alert (visible notification)

  message:
    | Includes all information of the event and is sent as a visible notification
    | with data attached to it. (Data can be truncated to stay within the data limits
    | of apns and firebase)

.. sourcecode:: js

      event_handlers:
        'm.call.invite': voip
        'm.room.message': message
        'm.room.member': event

|

message_types:
  Firebase currently also provides an option for 'm.room.message' to replace the content
  of the visible notification with a replacement per 'msg_type'.

.. sourcecode:: js

  message_types:
    'm.image': This is an image message
    'm.audio': This is a audio message
    'm.video': This is a video message

Running
=======

``python -m sygnal.sygnal``

Python 3.7 or higher is required.

Deployment
==========
Sygnal can be deployed using docker. The docker file can be found at 'docker/Dockerfile'
to customize configuration.


Building the image:
    | Specify the {organization}/{repository}:{version-tag} you want to push
    | the image to as the '-t' option.

.. sourcecode:: bash

    docker build . -f docker/Dockerfile -t {organization}/{repository}:{version-tag}

Pushing the image to docker hub:
    | Before pushing you need to log into an account which has write access to the
    | repository.

.. sourcecode:: bash

    docker login --username {username} --password {password}
    docker build . -f docker/Dockerfile -t {organization}/{repository}:{version-tag}

Deployment:
    | A simple docker-compose file can be used to deploy sygnal to a server.

.. sourcecode:: yaml

    version: '3.7'

    services:
      sygnal:
        image: {organization}/{repository}:{version-tag}
        restart: unless-stopped
        environment:
          - SYGNAL_CONF=/data/sygnal.yaml
        volumes:
          - ./data:/data
        ports:
          - 5000:5000


Log Rotation
============
Sygnal's logging appends to files but does not use a rotating logger.
The recommended configuration is therefore to use ``logrotate``.
The log file will be automatically reopened if the log file changes, for example
due to ``logrotate``.
