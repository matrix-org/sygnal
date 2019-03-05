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

import sqlite3
import logging
import threading
from six.moves import queue
import sys

logger = logging.getLogger(__name__)

class Db:
    def __init__(self, dbfile):
        self.dbfile = dbfile
        self.db_queue = queue.Queue()
        # Sqlite is blocking and does so in the c library so we can't
        # use gevent's monkey patching to make it play nice. We just
        # run all sqlite in a separate thread.
        self.dbthread = threading.Thread(target=self.db_loop)
        self.dbthread.setDaemon(True)
        self.dbthread.start()

    def db_loop(self):
        self.db = sqlite3.connect(self.dbfile)
        while True:
            job = self.db_queue.get()
            job()

    def query(self, query, args=(), fetch=None):
        res = {}
        ev = threading.Event()
        def runquery():
            try:
                c = self.db.cursor()
                c.execute(query, args)
                if fetch == 1 or fetch == 'one':
                    res['rows'] = c.fetchone()
                elif fetch == 'all':
                    res['rows'] = c.fetchall()
                elif fetch == None:
                    self.db.commit()
                    res['rowcount'] = c.rowcount
            except:
                logger.exception("Caught exception running db query %s", query)
                res['ex'] = sys.exc_info()[1]
            ev.set()
        self.db_queue.put(runquery)
        ev.wait()
        if 'ex' in res:
            raise res['ex']
        elif 'rows' in res:
            return res['rows']
        elif 'rowcount' in res:
            return res['rowcount']
