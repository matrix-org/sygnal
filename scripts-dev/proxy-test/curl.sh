#!/bin/sh

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <notification_file>"
    exit 1
fi

exec curl --fail -i -H "Content-Type: application/json" --request POST -d @$1 http://localhost:5000/_matrix/push/v1/notify
