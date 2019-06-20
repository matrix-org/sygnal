# -*- coding: utf-8 -*-

# Copyright 2014 matrix.org
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


class Database:
    def __init__(self, dbfile, twisted_reactor):
        self.twisted_reactor = twisted_reactor
        self.dbfile = dbfile
        # SQLite is blocking, so run queries in a separate thread
        self.dbexecutor = ThreadPoolExecutor(max_workers=1)
        self.dbexecutor.submit(self._db_setup)

    def _db_setup(self):
        self.db = sqlite3.connect(self.dbfile)

    def query(self, query, args=(), fetch=None):
        deferred = Deferred()

        def runquery():
            result = None
            try:
                c = self.db.cursor()
                c.execute(query, args)
                if fetch == 1 or fetch == 'one':
                    result = c.fetchone()
                elif fetch == 'all':
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
