# Notes for application developers

This document aims to illustrate some of the quirks, peculiarities and other
notable aspects of Sygnal for developers of applications that need to receive
push.

Sygnal has been somewhat flavoured by the development of the Element iOS
and Element Android clients, but nevertheless is intended to be useful for
other iOS and Android applications that have 'typical' requirements, without
need to resort to customising the application types (Pushkins).

(It is possible to extend Sygnal with other application types, but this is
out of scope for this document.)

Once you have read this document, you may also find [the troubleshooting document](./troubleshooting.md)
useful if you are running into trouble whilst deploying Sygnal.


## Definitions

* **APNs**: Apple Push Notification service, a notification service for iOS
  applications.
* **FCM**: Firebase Cloud Messaging (previously Google Cloud Messaging), a
  notification service primarily for Android applications but also usable for
  iOS and Chrome applications.
* **push key**: Also known as registration token (FCM) or device token (APNs),
  this ID identifies a device to which notifications can be sent.
* **Pushkin**: A module which adds support to Sygnal for an application type.
  Sygnal comes with an APNs pushkin and an FCM pushkin out of the box, but you
  may also use custom ones.


## Outlines of flows

An understanding of the flows of push notifications may be useful.
This section will provide an outline.


### Registration flow

0) The client needs to be configured with the address of its Sygnal instance,
   and any configuration that FCM or APNs requires for client apps.

1) The client needs to use an FCM or APNs library to acquire a push key, which
   identifies the client to the notification service.

2) The client registers a pusher on the user's homeserver, giving the address of
   its Sygnal instance, its push key, and perhaps some other parameters.
   To register a pusher, it uses [POST /_matrix/client/r0/pushers/set](https://matrix.org/docs/spec/client_server/latest#post-matrix-client-r0-pushers-set).


#### Worth noting

* In general, there is no contract between the operators of the homeserver(s)
  and the operators of the Sygnal push gateways.

  - Sygnal (push gateway) instances are deployed by the owner of the application.

  - Unless the application restricts it, the user is usually free to choose any
    homeserver which may be operated by anyone.

* It is not feasible to allow end-users to configure their own Sygnal instance,
  because the Sygnal instance needs the appropriate FCM or APNs secrets that
  belong to the application.


### Notification flow

1) The user's homeserver receives an event (from federation, another local user,
   or even an application service — the source is unimportant).

2) The user's homeserver applies push rules to the event on behalf of the user,
   in order to determine whether to send a push notification or not (as well as
   a few other tweaks).

  - For example, the push rules can decide to exclude certain rooms, or to
    notify for mentions on keywords (so the user could, for example, be notified
    when anyone mentions 'lunch').

3) If the homeserver decides to send a notification, we continue.

4) The homeserver calls [POST /_matrix/push/v1/notify](https://matrix.org/docs/spec/push_gateway/latest#post-matrix-push-v1-notify)
   on the Sygnal instance that the user's client registered.

5) The Sygnal receives this request and rewrites it into a request for the
   appropriate notification service (APNs or FCM), which it then sends.

6) Sygnal responds to the homeserver about the push's success and whether or not
   the push key was rejected.


#### Worth noting

* When Sygnal only sends data messages (also known as 'silent notifications') to
  target devices — the application needs to wake up and trigger its own
  notification, perhaps after downloading and decrypting the event.

  - Data-only messages are always sent to FCM.

  - Data-only messages are sent to APNs when the `event_id_only` format is in
    use. In other cases, the app may still need to perform additional processing,
    for example if encrypted events would need to be decrypted.

* When encrypted events are present, the homeserver is unable to conclusively
  run push rules — in this case, the client will need to run them locally to
  decide whether or not a notification should be displayed, a sound needs to be
  played and/or the message needs to be highlighted.

* **Consider user privacy**: if you use the `event_id_only` format, then data
  sent to the notification service (FCM or APNs) is minimal. If you do not, then
  unencrypted messages will have their content sent to the notification service,
  which some may prefer to avoid.


## Platform-specific notes

### Apple Push Notification service

By default, the client will receive a message with this structure:

```json
{
  "room_id": "!slw48wfj34rtnrf:example.com",
  "event_id": "$qTOWWTEL48yPm3uT-gdNhFcoHxfKbZuqRVnnWWSkGBs",
  "aps": {
    "alert": {
      "loc-key": "MSG_FROM_USER_IN_ROOM_WITH_CONTENT",
      "loc-args": [
        "Major Tom",
        "Mission Control",
        "I'm floating in a most peculiar way."
      ]
    },
    "badge": 3
  }
}
```

Please note that fields may be truncated if they are large, so that they fit
within APNs' limit.
Please also note that some fields will be unavailable if you registered a pusher
with `event_id_only` format.


#### iOS applications beware!

When registering your iOS pusher, you have the ability to specify a default
payload that will be sent to APNs. This allows you to set custom flags for APNs.

Of particular interest are `content-available` and `mutable-content` (which you
can set to `1` to enable).

Consult [APNs documentation] for a more in-depth explanation, but:

* `content-available` wakes up your application for up to 30 seconds in which it
  can process the message.
* `mutable-content` allows your application to mutate (modify) the notification.

An example `data` dictionary to specify on `POST /_matrix/client/r0/pushers/set`:

```json
{
  "url": "https://push-gateway.location.here/_matrix/push/v1/notify",
  "format": "event_id_only",
  "default_payload": {
    "aps": {
      "mutable-content": 1,
      "content-available": 1,
      "alert": {"loc-key": "SINGLE_UNREAD", "loc-args": []}
    }
  }
}
```

[APNs documentation]: https://developer.apple.com/library/archive/documentation/NetworkingInternet/Conceptual/RemoteNotificationsPG/CreatingtheNotificationPayload.html


### Firebase Cloud Messaging

The client will receive a message with an FCM `data` payload with this structure:

```json
{
  "event_id": "$3957tyerfgewrf384",
  "type": "m.room.message",
  "sender": "@exampleuser:example.org",
  "room_name": "Mission Control",
  "room_alias": "#exampleroom:example.org",
  "sender_display_name": "Major Tom",
  "content": {
    "msgtype": "m.text",
    "body": "I'm floating in a most peculiar way."
  },
  "room_id": "!slw48wfj34rtnrf:example.org",
  "prio": "high",
  "unread": 2,
  "missed_calls": 1
}
```

Please note that fields may be truncated if they are large, so that they fit
within FCM's limit.
Please also note that some fields will be unavailable if you registered a pusher
with `event_id_only` format.

### Web Push

#### Setup & configuration

In the sygnal virtualenv, generate the server key pair by running
`vapid --gen --applicationServerKey`. This will generate a `private_key.pem`
(which you'll refer to in the config file with `vapid_private_key`) 
and `public_key.pem` file, and also string labeled `Application Server Key`.

You'll copy the Application Server Key to your web application to subscribe
to the push manager:

```js
serviceWorkerRegistration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: "...",
});
```

You also need to set an e-mail address in `vapid_contact_email` in the config file,
where the push gateway operator can reach you in case they need to notify you
about your usage of their API.

#### Push key and expected push data

In your web application, [the push manager subscribe method]
(https://developer.mozilla.org/en-US/docs/Web/API/PushManager/subscribe) will return
[a subscription](https://developer.mozilla.org/en-US/docs/Web/API/PushSubscription) 
with an `endpoint` and `keys` property, the latter containing a `p256dh` and `auth` 
property. The `p256dh` key is used as the push key, and the push data is expected 
`endpoint` and `auth`. You can also set `default_payload` in the push data;
any properties set in it will be present in the push messages you receive, 
so it can be used to pass identifiers specific to your client
(like which account the notification is for).

Also note that because you can only have one push subscription per service worker,
and hence per origin, you might create pushers for different accounts with the same 
p256dh push key. To prevent the server from removing other pushers with the same 
push key for your other users, you should set `append` to `true` when uploading 
your pusher.

#### Notification format

The notification as received by your web application will contain these keys 
if they were set by the homeserver, and otherwise omit them. These are the
same as specified in [the push gateway spec]
(https://matrix.org/docs/spec/push_gateway/r0.1.0#post-matrix-push-v1-notify),
but `count` with `unread` and `missed_calls` is flattened into the notification object.

```
room_id
room_name
room_alias
membership
event_id
sender
sender_display_name
user_is_target
type
content
unread
missed_calls
```