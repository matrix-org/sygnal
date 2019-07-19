# -*- coding: utf-8 -*-
# Copyright 2014 matrix.org
# Copyright 2019 The Matrix.org Foundation C.I.C.
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

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from twisted.internet.defer import Deferred

logger = logging.getLogger(__name__)


class Database(object):
    def __init__(self, dbfile, twisted_reactor):
        self.twisted_reactor = twisted_reactor
        self.dbfile = dbfile
        # SQLite is blocking, so run queries in a separate thread
        self.dbexecutor = ThreadPoolExecutor(max_workers=1)
        self.dbexecutor.submit(self._db_setup)

    def _db_setup(self):
        logger.info("Opening SQLite database: %s", self.dbfile)
        self.db = sqlite3.connect(self.dbfile)

    def query(self, query, args=(), fetch=None):
        """
        Execute a query asynchronously.
        Args:
            query (str): The query string
            args (tuple): Arguments for ?-substitution in the query string
            fetch (str, optional): determines what part of the result is received

        Returns:
            a Deferred which will fire with the query result when the
            query is completed.

            fetch modes are:
            - 'one': return one row or None
            - 'all': return a list of rows
            - None: return a row count

        """
        deferred = Deferred()

        def runquery():
            result = None
            try:
                c = self.db.cursor()
                c.execute(query, args)
                if fetch == 1 or fetch == "one":
                    result = c.fetchone()
                elif fetch == "all":
                    result = c.fetchall()
                elif fetch is None:
                    self.db.commit()
                    result = c.rowcount
                self.twisted_reactor.callFromThread(deferred.callback, result)
            except Exception as exception:
                logger.exception("Caught exception running db query %s", query)
                self.twisted_reactor.callFromThread(deferred.errback, exception)

        self.dbexecutor.submit(runquery)

        return deferred
