#!/usr/local/bin/python

# Name: pdfviewer.cgi
# Purpose: provide access to PDF files, either via linking from the PWI or
#	by curators entering IDs directly
# Notes:
# 1. PDF files are stored using the numeric part of the reference's MGI ID
#	as the filename (with a .pdf extension).
# 2. PDF files are grouped into directories of 1000.
# 3. We could link directly to PDF files served up through Apache, but using
#	this script will allow us to set HTTP headers that give a more
#	meaningful filename when a curators saves the PDF file locally.
# 4. Database interactivity in this script is read-only.
# 5. Whenever possible we use the database to do ID lookups.  In cases where
#	the database is unavailable, we fall back on disk caching managed by the
#	IDCache module.

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

DEBUG = False			# debugging to stderr on (True) or off (False)?
profiler = Profiler.Profiler()


if hasMasterConfig:
	pg_db.set_sqlServer(masterConfig.MGD_DBSERVER)
	pg_db.set_sqlDatabase(masterConfig.MGD_DBNAME)
	pg_db.set_sqlUser(masterConfig.MGD_DBUSER)
	pg_db.set_sqlPasswordFromFile(masterConfig.MGD_DBPASSWORDFILE)
else:
	pg_db.set_sqlLogin('mgd_public', 'mgdpub', 'mgi-adhoc', 'mgd')

###--- Query Form ---###

FORM = '''
<HTML><HEAD><TITLE>pdfviewer</TITLE>
<SCRIPT>
function formSubmit() {
  var accids = document.getElementById('accids').value;
  if ((accids.indexOf(",") >= 0) || (accids.indexOf(" ") >= 0)) {
  	document.getElementById('pdfForm').target = "";
  } else {
  	document.getElementById('pdfForm').target = "_blank";
  }
}
</SCRIPT>
</HEAD>
<BODY>
<H3>pdfviewer</H3>
<FORM ID="pdfForm" ACTION="pdfviewer.cgi" METHOD="GET" onSubmit="formSubmit()">
Enter the ID for a reference to retrieve:
<INPUT TYPE="text" NAME="id" WIDTH="30" ID="accids">
&nbsp;&nbsp;<input type="submit" name="Go" value="Go" />
&nbsp;&nbsp;<span style="color: blue" title="Accepts:  MGI, J:, PubMed, DOI, GO REF">Help</span>
<p>
%s
</FORM>
</BODY>
</HTML>
'''

###--- Functions ---###

def parseParameters():
	# Purpose: identify any parameters from the user
	# Return: dictionary, maps parameter name to value
	# Notes: only one parameter recognized so far:
	#    id - any reference accession ID (PubMed, MGI, J:, DOI, GO REF)

	global REF_ID

	params = {}
	form = cgi.FieldStorage()
	if 'id' in form:
		params['id'] = ''
		for item in form.getlist('id'):
			params['id'] = '%s, %s' % (params['id'], item)
		params['id'] = params['id'][2:]
	profiler.stamp("parsed parameters")
	return params

def sendForm(accids = None, error = None):
	# Generate and send the query form out to the user

	ids = ''
	if accids:
		idList = [
			'Your %d requested PDFs are available here:<br/>' % \
				len(accids),
			'<ul>',
			]
		for accid in accids:
			idList.append('<li><a href="pdfviewer.cgi?id=%s" target="_blank">%s</a></li>' % (accid, accid))

		idList.append('</ul>')
		ids = '\n'.join(idList)

	elif error:
		ids = 'Error: %s' % error

	page = [
		'Content-type: text/html',
		'',
		FORM % ids
		]
	print '\n'.join(page)
	profiler.stamp("sent form")
	return

def sendPDF(refID):
	# 1. look up MGI ID for reference
	# 2. look up other data to construct new filename
	# 3. look up PDF
	# 4. send appropriate headers (type, filename)
	#    a. filename should be numeric MGI ID and J: number with an
	#	underscore between them (per 8/11/17 email), like:
	#	123456_J98765.pdf
	# 5. send PDF

	try:
		mgiID, jnum = getReferenceData(refID)
	except:
		profiler.stamp("failed to get reference info")
		sendForm(error = sys.exc_info()[1])
		return

	profiler.stamp("queried database")

	if (mgiID == None):
		if refID.startswith("MGI:"):
			mgiID = refID
		else:
			sendForm(error = "Unknown ID: %s" % refID)
			return

	prefix, numeric = mgiID.split(':')

	filepath = os.path.join(Pdfpath.getPdfpath('/data/littriage', mgiID),
		numeric + '.pdf')

	if not os.path.exists(filepath):
		sendForm(error = "Cannot find file: %s" % filepath)
		return

	profiler.stamp("found path: %s" % filepath)

	newFilename = mgiID.replace('MGI:', '')
	if jnum:
		newFilename = '%s_%s.pdf' % (newFilename, jnum.replace(':', ''))
	else:
		newFilename = '%s.pdf' % newFilename

	print 'Content-type: application/pdf'
	print 'Content-Disposition: inline; filename="%s"' % newFilename
	print
	
	infile = open(filepath, 'rb')

	profiler.stamp("opened input file")

	readSize = 256 * 1024 * 1024		# 256 Mb
	chunk = infile.read(readSize)
	while (chunk):
		sys.stdout.write(chunk)
		chunk = infile.read(readSize)

	infile.close() 

	profiler.stamp("read input file and sent PDF")
	return

def canReadFromDatabase():
	# Purpose: tests database connectivity
	# Returns: True if okay, False if not

	try:
		pg_db.sql('select lastdump_date from mgi_dbinfo', 'auto')
	except:
		return False

	profiler.stamp("tested db connection")
	return True

# 1. If we can search the database, do so for the given ID.
#    a. If no results, is faulty ID.
#    b. If has results, use them.
# 2. If cannot search the database, fall back on data from IDCache.

def getReferenceData (refID):
	# Purpose: The fastest way to get the necessary ID data for a reference
	#	is to query the database.  If we can't do that, fall back on cache
	#	files, if available.
	# Returns: (MGI ID, J: number) or (None, None) if 'refID' is unknown
	# Throws: Exception if we cannot query the database

	if not canReadFromDatabase():
		searcher = IDCache.CacheSearcher()
		return searcher.lookup(refID)

	cmd = '''select c.jnumID, c.mgiID
		from bib_citation_cache c, acc_accession a
		where a._MGIType_key = 1
			and a._Object_key = c._Refs_key
			and lower(a.accID) = '%s' ''' % refID.lower()

	results = pg_db.sql(cmd, 'auto')
	if len(results) > 0:
		return (results[0]['mgiID'], results[0]['jnumID'])

	return (None, None)

###--- Main Program ---###

if __name__ == '__main__':
	try:
		params = parseParameters()
		if 'id' in params:
			if (' ' in params['id']) or (',' in params['id']):
				accids = params['id'].replace(',', ' ').split()
				sendForm(accids)
			else:
				sendPDF(params['id'])
		else:
			sendForm()
			
		if DEBUG:
			profiler.write()
	except:
		sendForm(error = sys.exc_info()[1])
