# -*- coding: utf-8 -*-
# Copyright 2015 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Copied and adapted from
# https://raw.githubusercontent.com/matrix-org/pushbaby/master/tests/test_truncate.py


import string
import unittest

from sygnal.apnstruncate import json_encode, truncate


def simplestring(length, offset=0):
    """
    Deterministically generates a string.
    Args:
        length: Length of the string
        offset: Offset of the string

    Returns:
        A string formed of lowercase ASCII characters.
    """
    return "".join(
        [
            string.ascii_lowercase[(i + offset) % len(string.ascii_lowercase)]
            for i in range(length)
        ]
    )


def sillystring(length, offset=0):
    """
    Deterministically generates a string
    Args:
        length: Length of the string
        offset: Offset of the string

    Returns:
        A string formed of weird and wonderful UTF-8 emoji characters.
    """
    chars = ["\U0001F430", "\U0001F431", "\U0001F432", "\U0001F433"]
    return "".join([chars[(i + offset) % len(chars)] for i in range(length)])


def payload_for_aps(aps):
    """
    Returns the APNS payload for an 'aps' dictionary.
    """
    return {"aps": aps}


class TruncateTestCase(unittest.TestCase):
    def test_dont_truncate(self):
        """
        Tests that truncation is not performed if unnecessary.
        """
        # This shouldn't need to be truncated
        txt = simplestring(20)
        aps = {"alert": txt}
        self.assertEqual(txt, truncate(payload_for_aps(aps), 256)["aps"]["alert"])

    def test_truncate_alert(self):
        """
        Tests that the 'alert' string field will be truncated when needed.
        """
        overhead = len(json_encode(payload_for_aps({"alert": ""})))
        txt = simplestring(10)
        aps = {"alert": txt}
        self.assertEqual(
            txt[:5], truncate(payload_for_aps(aps), overhead + 5)["aps"]["alert"]
        )

    def test_truncate_alert_body(self):
        """
        Tests that the 'alert' 'body' field will be truncated when needed.
        """
        overhead = len(json_encode(payload_for_aps({"alert": {"body": ""}})))
        txt = simplestring(10)
        aps = {"alert": {"body": txt}}
        self.assertEqual(
            txt[:5],
            truncate(payload_for_aps(aps), overhead + 5)["aps"]["alert"]["body"],
        )

    def test_truncate_loc_arg(self):
        """
        Tests that the 'alert' 'loc-args' field will be truncated when needed.
        (Tests with one loc arg)
        """
        overhead = len(json_encode(payload_for_aps({"alert": {"loc-args": [""]}})))
        txt = simplestring(10)
        aps = {"alert": {"loc-args": [txt]}}
        self.assertEqual(
            txt[:5],
            truncate(payload_for_aps(aps), overhead + 5)["aps"]["alert"]["loc-args"][0],
        )

    def test_truncate_loc_args(self):
        """
        Tests that the 'alert' 'loc-args' field will be truncated when needed.
        (Tests with two loc args)
        """
        overhead = len(json_encode(payload_for_aps({"alert": {"loc-args": ["", ""]}})))
        txt = simplestring(10)
        txt2 = simplestring(10, 3)
        aps = {"alert": {"loc-args": [txt, txt2]}}
        self.assertEqual(
            txt[:5],
            truncate(payload_for_aps(aps), overhead + 10)["aps"]["alert"]["loc-args"][
                0
            ],
        )
        self.assertEqual(
            txt2[:5],
            truncate(payload_for_aps(aps), overhead + 10)["aps"]["alert"]["loc-args"][
                1
            ],
        )

    def test_python_unicode_support(self):
        """
        Tests Python's unicode support :-
            a one character unicode string should have a length of one, even if it's one
            multibyte character.
            OS X, for example, is broken, and counts the number of surrogate pairs.
            I have no great desire to manually parse UTF-8 to work around this since
            it works fine on Linux.
        """
        if len(u"\U0001F430") != 1:
            msg = (
                "Unicode support is broken in your Python binary. "
                + "Truncating messages with multibyte unicode characters will fail."
            )
            self.fail(msg)

    def test_truncate_string_with_multibyte(self):
        """
        Tests that truncation works as expected on strings containing one
        multibyte character.
        """
        overhead = len(json_encode(payload_for_aps({"alert": ""})))
        txt = u"\U0001F430" + simplestring(30)
        aps = {"alert": txt}
        # NB. The number of characters of the string we get is dependent
        # on the json encoding used.
        self.assertEqual(
            txt[:17], truncate(payload_for_aps(aps), overhead + 20)["aps"]["alert"]
        )

    def test_truncate_multibyte(self):
        """
        Tests that truncation works as expected on strings containing only
        multibyte characters.
        """
        overhead = len(json_encode(payload_for_aps({"alert": ""})))
        txt = sillystring(30)
        aps = {"alert": txt}
        trunc = truncate(payload_for_aps(aps), overhead + 30)
        # The string is all 4 byte characters so the trunctaed UTF-8 string
        # should be a multiple of 4 bytes long
        self.assertEqual(len(trunc["aps"]["alert"].encode()) % 4, 0)
        # NB. The number of characters of the string we get is dependent
        # on the json encoding used.
        self.assertEqual(txt[:7], trunc["aps"]["alert"])
