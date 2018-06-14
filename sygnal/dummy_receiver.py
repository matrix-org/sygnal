#!/usr/bin/env python
import pika
import ast

import grequests
import gevent

import logging
import time

# The error codes that mean a registration ID will never
# succeed and we should reject it upstream.
# We include NotRegistered here too for good measure, even
# though gcm-client 'helpfully' extracts these into a separate
# list.
BAD_PUSHKEY_FAILURE_CODES = [
    'MissingRegistration',
    'InvalidRegistration',
    'NotRegistered',
    'InvalidPackageName',
    'MismatchSenderId',
]

# Failure codes that mean the message in question will never
# succeed, so don't retry, but the registration ID is fine
# so we should not reject it upstream.
BAD_MESSAGE_FAILURE_CODES = [
    'MessageTooBig',
    'InvalidDataKey',
    'InvalidTtl',
]

GCM_URL = "https://fcm.googleapis.com/fcm/send"
RETRY_DELAY_BASE = 10

logger = logging.getLogger(__name__)

connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
channel = connection.channel()

channel.queue_declare(queue='hello')

def parse_message(msg_body):
    msg_contents = ast.literal_eval(msg_body)
    return msg_contents[:3], msg_contents[4:]

def callback(ch, method, properties, msg_body):
    print " [x] Received %r" % msg_body
    parse_message(msg_body)
    headers, body = parse_message(msg_body)
    print "  headers=%r" % headers
    print "  body=%r" % body

    poke_start_time = time.time()

    req = grequests.post(
        GCM_URL, json=body, headers=headers, timeout=10,
    )
    req.send()

    logger.debug("GCM request took %f seconds", time.time() - poke_start_time)

    if req.response is None:
        success = False
        logger.debug("Request failed, waiting to try again", req.exception)
    elif req.response.status_code / 100 == 5:
        success = False
        logger.debug("%d from server, waiting to try again", req.response.status_code)
    elif req.response.status_code == 400:
        logger.error(
            "%d from server, we have sent something invalid! Error: %r",
            req.response.status_code,
            req.response.text,
        )
        # permanent failure: give up
        raise Exception("Invalid request")
    elif req.response.status_code == 401:
        logger.error(
            "401 from server! Our API key is invalid? Error: %r",
            req.response.text,
        )
        # permanent failure: give up
        raise Exception("Not authorized to push")
    elif req.response.status_code / 100 == 2:
        resp_object = req.response.json()
        if 'results' not in resp_object:
            logger.error(
                "%d from server but response contained no 'results' key: %r",
                req.response.status_code, req.response.text,
            )
        if len(resp_object['results']) < len(pushkeys):
            logger.error(
                "Sent %d notifications but only got %d responses!",
                len(n.devices), len(resp_object['results'])
            )

        new_pushkeys = []
        for i, result in enumerate(resp_object['results']):
            if 'registration_id' in result:
                self.canonical_reg_id_store.set_canonical_id(
                    pushkeys[i], result['registration_id']
                )
            if 'error' in result:
                logger.warn("Error for pushkey %s: %s", pushkeys[i], result['error'])
                if result['error'] in BAD_PUSHKEY_FAILURE_CODES:
                    logger.info(
                        "Reg ID %r has permanently failed with code %r: rejecting upstream",
                            pushkeys[i], result['error']
                    )
                    failed.append(pushkeys[i])
                elif result['error'] in BAD_MESSAGE_FAILURE_CODES:
                    logger.info(
                        "Message for reg ID %r has permanently failed with code %r",
                            pushkeys[i], result['error']
                    )
                else:
                    logger.info(
                        "Reg ID %r has temporarily failed with code %r",
                            pushkeys[i], result['error']
                    )
                    new_pushkeys.append(pushkeys[i])
        if len(new_pushkeys) == 0:
            return failed
        pushkeys = new_pushkeys

    retry_delay = RETRY_DELAY_BASE #* (2 ** retry_number)
    if req.response and 'retry-after' in req.response.headers:
        try:
            retry_delay = int(req.response.headers['retry-after'])
        except:
            pass
    logger.info("Retrying in %d seconds", retry_delay)
    gevent.sleep(seconds=retry_delay)

channel.basic_consume(callback,
                      queue='hello',
                      no_ack=True)

print ' [*] Waiting for messages. To exit press CTRL+C'
channel.start_consuming()
