# -*- coding: utf-8 -*-
# Copyright 2021 The Matrix.org Foundation C.I.C.
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
import re

from twisted.trial import unittest

from sygnal.utils import glob_to_regex


class GlobToRegexTestCase(unittest.TestCase):
    def test_literal(self):
        """Tests `glob_to_regex` with only literal characters"""
        pattern = glob_to_regex("org.matrix", ignore_case=False)
        self.assertEqual(pattern.pattern, r"\Aorg\.matrix\Z")

    def test_wildcards(self):
        """Tests `glob_to_regex` with wildcards"""
        pattern = glob_to_regex("org.matrix.*", ignore_case=False)
        self.assertEqual(pattern.pattern, r"\Aorg\.matrix\..*\Z")

        pattern = glob_to_regex("org.matrix.???", ignore_case=False)
        self.assertEqual(pattern.pattern, r"\Aorg\.matrix\....\Z")

    def test_ignore_case(self):
        """Tests `glob_to_regex` case sensitivity"""
        pattern = glob_to_regex("org.matrix", ignore_case=False)
        self.assertEqual(pattern.flags & re.IGNORECASE, 0)

        pattern = glob_to_regex("org.matrix", ignore_case=True)
        self.assertEqual(pattern.flags & re.IGNORECASE, re.IGNORECASE)
