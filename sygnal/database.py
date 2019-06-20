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

import asyncio
import logging
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, dbfile):
        self.dbfile = dbfile
        # SQLite is blocking, so run queries in a separate thread
        self.dbexecutor = ThreadPoolExecutor(max_workers=1)
        self.dbexecutor.submit(self._db_setup)

    def _db_setup(self):
        self.db = sqlite3.connect(self.dbfile)

    async def query(self, query, args=(), fetch=None):
        def runquery():
            result = {}
            try:
                c = self.db.cursor()
                c.execute(query, args)
                if fetch == 1 or fetch == 'one':
                    result['rows'] = c.fetchone()
                elif fetch == 'all':
                    result['rows'] = c.fetchall()
                elif fetch is None:
                    self.db.commit()
                    result['rowcount'] = c.rowcount
            except Exception:
                logger.exception("Caught exception running db query %s", query)
                result['ex'] = sys.exc_info()[1]
            return result

        res = await asyncio.get_event_loop().run_in_executor(self.dbexecutor, runquery)

        if 'ex' in res:
            raise res['ex']
        elif 'rows' in res:
            return res['rows']
        elif 'rowcount' in res:
            return res['rowcount']
