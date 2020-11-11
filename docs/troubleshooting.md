# Troubleshooting Sygnal deployments

Push notifications can be rather hard to get right, and there are plenty of
places to trip up and have nothing but silence to show for your efforts.

This document offers some suggestions of what to check and lists some common
pitfalls you may encounter, based on experience.

There are also appendices with information which may be useful for manual
debugging.

Your first steps are to ensure that you have logging so you can see what is going
on; a level of `INFO` or even `DEBUG` will be useful.


## Narrowing in on the problem

### Check the pusher is registered in your homeserver

Typically, applications will register a pusher on startup.

If you have access to your homeserver, you can check that it is making it there.

Start your application and then run a query against the database.

#### On Synapse

Use `sqlite3 /path/to/homeserver.db` or `psql synapse` as required for your
deployment.

```sql
SELECT app_id, data FROM pushers
  WHERE user_name = '@my.user:example.org' AND kind='http';
```

You should see something like:

```
      app_id       |                        data
-------------------+--------------------------------------------------------
 org.example.chat  | {"format":"event_id_only",
                   |  "url":"https://example.org/_matrix/push/v1/notify"}
```


#### On other homeserver implementations

No details available, but contributions welcome.


### Check the push gateway (Sygnal) is reachable from the homeserver

Following on from the example above, the homeserver's database contains the
push gateway URL of `https://example.org/_matrix/push/v1/notify`.

It may be worth manually checking that the push gateway is reachable from the
homeserver; e.g. with curl:

```
$ curl https://example.org/_matrix/push/v1/notify
<html>
  <head><title>405 - Method Not Allowed</title></head>
  <body>
    <h1>Method Not Allowed</h1>
    <p>Your browser approached me (at /_matrix/push/v1/notify) with the method "GET".  I only allow the methods HEAD, POST here.</p>
  </body>
</html>
```

If you get a response, such as an error like **405 Method Not Allowed**, as above,
this would suggest that the push gateway is at least reachable.

If you get a **404 No Such Resource** error on the `/_matrix/push/v1/notify` endpoint,
then chances are that your reverse proxy is not configured to pass through the
full URL.

If you don't get an HTTP response, then it is probably worth investigation.
Check that:

* Sygnal is running
* Sygnal's configuration makes it listen on the desired port
* Any reverse proxies are correctly set up and running
* The firewall permits inbound traffic on the port in question


## Troubleshooting Firebase notifications

### iOS-specific troubles with apps using Firebase

#### App doesn't receive notifications when inactive

Sygnal currently only sends 'data messages' (also called 'silent notifications',
but this name could be misleading).

Whereas data messages will wake up apps on Android with no additional changes,
iOS needs to be told that a notification is meant to wake up an inactive app.
This is done with FCM's `content_available` flag, which you can set in your
`fcm_options` dictionary for the Firebase pushkin.
(See [`sygnal.yaml.sample`](../sygnal.yaml.sample).)


## Troubleshooting APNs notifications

### Base64 decoding error in the logs

#### Common cause 1: Hex rather than base64 encoding

Sygnal's APNs support expects your pushkeys to be base64 encoded rather than
hexadecimally encoded.

*(Why? The previous APNs API which Sygnal supported was binary and didn't define
a text-safe encoding, so it was chosen to use base64 in Sygnal. Now the new API
exists and specifies hexadecimal encoding, but Sygnal retains backwards
compatibility and will do the base64-to-hex conversion.)*


#### Common cause 2: Firebase token given

If you are using Firebase for your iOS app, you will get Firebase tokens
(looking a bit like `blahblahblah:APA91blahblahblah`… note the presence of a
colon which is not valid base64).

In this case, you need to **configure Sygnal to use a FCM (gcm) pushkin rather
than an APNs one, as Firebase talks to APNs on your behalf**.
Instead of configuring Sygnal with your APNs secrets, you need to configure
Firebase with your APNs secrets, and Sygnal with your Firebase secrets.


### App doesn't receive notifications when inactive

If you want your application to be woken up to be able to process APNs messages
received when your application is in the background, you need to set the
`content-available` flag in your pusher's default payload — see
[the notes for iOS applications](applications.md#ios-applications-beware).


### '400 BadDeviceToken' error

If you get a bad device token error and you have doubled-checked the
token is correct, it is possible that you have used a token from the wrong 'environment',
such as a development token when Sygnal is configured to use the production
environment.

Sygnal connects to the production APNs instance by default. This will return
`400 BadDeviceToken` if you send it a token intended for the sandbox APNs
server.

Either use production tokens, or switch to the sandbox APNs server by setting:

```
com.example.myapp.ios:
  type: apns
  ...
  platform: sandbox
```

in your Sygnal config file.


# Appendices

## Sending a notification to Sygnal manually with `curl`

Note: this depends on the heredoc syntax of the `bash` shell.

```bash
curl -i -H "Content-Type: application/json" --request POST -d '@-' http://syg1:8008/_matrix/push/v1/notify <<EOF
{
  "notification": {
    "event_id": "\$3957tyerfgewrf384",
    "room_id": "!slw48wfj34rtnrf:example.org",
    "type": "m.room.message",
    "sender": "@exampleuser:example.org",
    "sender_display_name": "Major Tom",
    "room_name": "Mission Control",
    "room_alias": "#exampleroom:example.org",
    "prio": "high",
    "content": {
      "msgtype": "m.text",
      "body": "I'm floating in a most peculiar way."
    },
    "counts": {
      "unread": 2,
      "missed_calls": 1
    },
    "devices": [
      {
        "app_id": "<APP ID HERE>",
        "pushkey": "<PUSHKEY HERE>",
        "pushkey_ts": 12345678,
        "data": {},
        "tweaks": {
          "sound": "bing"
        }
      }
    ]
  }
}
EOF
```


## Example of an FCM request

HTTP data sent to `https://fcm.googleapis.com/fcm/send`:

```
POST /fcm/send HTTP/1.1
User-Agent: sygnal
Content-Type: application/json
Authorization: key=<FCM TOKEN HERE>
Host: fcm.googleapis.com

{"data": {"event_id": "$3957tyerfgewrf384", "type": "m.room.message", "sender": "@exampleuser:example.org", "room_name": "Mission Control", "room_alias": "#exampleroom:example.org", "membership": null, "sender_display_name": "Major Tom", "content": {"msgtype": "m.text", "body": "I'm floating in a most peculiar way."}, "room_id": "!slw48wfj34rtnrf:example.org", "prio": "high", "unread": 2, "missed_calls": 1}, "priority": "high", "to": "<PUSHKEY HERE>"}
```

You can send using curl using:

```bash
curl -i -H "Content-Type: application/json" -H "Authorization: key=<FCM TOKEN HERE>" --request POST -d '@-' https://fcm.googleapis.com/fcm/send <<EOF
{"data": {"event_id": "$3957tyerfgewrf384", "type": "m.room.message", "sender": "@exampleuser:example.org", "room_name": "Mission Control", "room_alias": "#exampleroom:example.org", "membership": null, "sender_display_name": "Major Tom", "content": {"msgtype": "m.text", "body": "I'm floating in a most peculiar way."}, "room_id": "!slw48wfj34rtnrf:example.org", "prio": "high", "unread": 2, "missed_calls": 1}, "priority": "high", "to": "<PUSHKEY HERE>"}
EOF
```
