#!/usr/local/bin/python

# Name: gen_cache.py
# Purpose: generate a cache of reference IDs, so pdfviewer.cgi can return PDFs
#	by ID even when the database is unavailable

import os
import cgi
import types
import sys
sys.path.insert(0, '/usr/local/mgi/live/lib/python')

import pg_db
import Pdfpath
import IDCache
import Profiler

mgiconfigPath = '/usr/local/mgi/live/mgiconfig'
if 'MGICONFIG' in os.environ:
	mgiconfigPath = os.environ['MGICONFIG']
sys.path.insert(0, mgiconfigPath)

try:
	import masterConfig
	hasMasterConfig = True
except:
	hasMasterConfig = False

###--- Globals ---###

profiler = Profiler.Profiler()

if hasMasterConfig:
	pg_db.set_sqlServer(masterConfig.MGD_DBSERVER)
	pg_db.set_sqlDatabase(masterConfig.MGD_DBNAME)
	pg_db.set_sqlUser(masterConfig.MGD_DBUSER)
	pg_db.set_sqlPasswordFromFile(masterConfig.MGD_DBPASSWORDFILE)
else:
	pg_db.set_sqlLogin('mgd_public', 'mgdpub', 'mgi-adhoc', 'mgd')

builder = IDCache.CacheBuilder(pg_db.sql, profiler.stamp)
builder.cacheIDs()
profiler.stamp('finished')
profiler.write()
