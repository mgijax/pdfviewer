#!/usr/local/bin/python

# splitter.cgi
# CGI for generating a web page that supports curators looking at the
#   how the extractedTextSplitter.py works for different references
# Curators enter reference ID or upload a pdf file and see the results
#   of the extracted text section splitting
# HISTORY
# 4/3/2019 - jak:  initial version

# ----------------------------
import sys
import string
import cgi
import os
import os.path
import time

sys.path.insert(0, '/usr/local/mgi/live/lib/python')
import Configuration

config = Configuration.Configuration('Configuration')
pathParams = config.lookup('PYTHONPATH')
if pathParams != None:
    pathItems = pathParams.strip().split(':')
    sys.path = pathItems + sys.path

import pg_db as db
import runCommand
import Pdfpath
import IDCache
import extractedTextSplitter

mgiconfigPath = '/usr/local/mgi/live/mgiconfig'
if 'MGICONFIG' in os.environ:
    mgiconfigPath = os.environ['MGICONFIG']
sys.path.insert(0, mgiconfigPath)
try:
    import masterConfig
    hasMasterConfig = True
except:
    hasMasterConfig = False

# Config values
DEBUG                 = (config.lookup('DEBUG') == 'True')
UPLOAD_DIR            = config.get('UPLOAD_DIR')
PDF_STORAGE_BASE_PATH = config.get('PDF_STORAGE_BASE_PATH')

BASE_PDFVIEWER_URL = './pdfviewer.cgi'

# ----------------------------

def debug(msg):
    if DEBUG: print '<br>' + msg

# ----------------------------
class ReferenceInfo (object):
    """
    Is a references
    Has the relevant info for us
    """
    def __init__(self, pubmedID, mgiID, jnumID, citation, title,
		    referenceType, isReview, isDiscard, extractedText):
	self.pubmedID      = pubmedID
	self.mgiID         = mgiID
	self.jnumID        = jnumID
	self.citation      = citation
	self.title         = title
	self.referenceType = referenceType
	self.isReview      = str(isReview)
	self.isDiscard     = str(isDiscard)
	self.extractedText = extractedText	# text extracted from the PDF

	self.pdfLink       = ''			# html for URL link to the PDF
# ----------------------------

def setDB():
    """
    set db connection
    """
    if hasMasterConfig:
	db.set_sqlServer(masterConfig.MGD_DBSERVER)
	db.set_sqlDatabase(masterConfig.MGD_DBNAME)
	db.set_sqlUser(masterConfig.MGD_DBUSER)
	db.set_sqlPasswordFromFile(masterConfig.MGD_DBPASSWORDFILE)
    else:
	db.set_sqlLogin('mgd_public', 'mgdpub', 'mgi-adhoc', 'mgd')
    debug('hitting %s %s' % (db.get_sqlServer(), db.get_sqlDatabase()) )

# ----------------------------

def canReadFromDatabase():
    """
    Test database connectivity
    Return True if okay, False if not
    """
    try:
	db.sql('select lastdump_date from mgi_dbinfo', 'auto')
    except:
	debug("no db connection")
	return False

    debug("have db connection")
    return True
# ----------------------------

def writeHeader():

    print """Content-type: text/html

	<HTML><HEAD><TITLE>Extracted Text Section Splitting</TITLE>
	<STYLE>
	table, th, td { border: 1px solid black; }
	.header { border: thin solid black; vertical-align: top; font-weight: bold }
	.value { border: thin solid black; vertical-align: top; }.highlight { background-color: yellow; }
	.right { text-align: right; }
	</STYLE>
	</HEAD>
	<BODY>
	<H3>Extracted Text Split Viewer</H3>
	"""
# ----------------------------

def getReferenceInfo (refID):
    """
    Given reference ID (jnum, mgi id, pubmed, ...),
    Return a ReferenceInfo object for the reference (if found)
    or an error message
    """
    setDB()
    if not canReadFromDatabase():	# get MGI/Jnum IDs from cached ID files
	try:
	    searcher = IDCache.CacheSearcher()
	    mgiID, jnumID =  searcher.lookup(refID)
	except:
	    return str(sys.exc_info()[1])
	noDB = "not available"
	refInfo = ReferenceInfo('', mgiID, jnumID, noDB, noDB, '', '', '',
								'no text yet')
    else:				# get info from db
	refInfo, error = getReferenceInfoFromDB(refID)
	if refInfo == None: return error

    # get path to PDF file
    pdfPath, error = getPdfFilePath(refInfo.mgiID)
    if error: return error

    # extract text
    extractedText, error = extractTextFromPDF(pdfPath)
    if error: return error

    refInfo.extractedText = extractedText

    # create PDF link using the pdfviewer cgi
    refInfo.pdfLink = '<a href="%s?id=%s" target="_blank">PDF</a>' % \
					(BASE_PDFVIEWER_URL, refInfo.mgiID)
    return refInfo
# ----------------------------

def getReferenceInfoFromDB(refID):
    """
    given a reference ID (Jnum, MGI id, pubmed, etc), get refInfo from db.
    Return pair (refInfo, error)
	refInfo =  ReferenceInfo object or None
	if refInfo == None, error = error message
    """
    error   = None
    refInfo = None
    query = '''select c.jnumID, c.mgiID, c.pubmedID, c.citation as citation,
		b.title, c.referenceType, c.isreviewarticle as isReview,
		c.isDiscard
	    from bib_citation_cache c, acc_accession a, bib_refs b
	    where a._MGIType_key = 1
		and a._Object_key = c._Refs_key
		and b._refs_key   = c._Refs_key
		and lower(a.accID) = '%s' ''' % refID.lower()
    results = db.sql(query, 'auto')

    if len(results) == 0:
	error = "Cannot find a reference with ID '%s'" % str(refID)
    else:
	r = results[0]
	refInfo = ReferenceInfo(r['pubmedID'],
				r['mgiID'],
				r['jnumID'],
				r['citation'],
				r['title'],
				r['referenceType'],
				r['isReview'],
				r['isDiscard'],
				'no text yet',
				)
    return refInfo, error
# ----------------------------

def getPdfFilePath(mgiID):
    """
    Return (path, error).
    path = full pathname of PDF file. If None, error is error message
    """
    filePath = None
    error    = None
    prefix, numeric = mgiID.split(':')

    try:
	filePath = os.path.join(Pdfpath.getPdfpath(PDF_STORAGE_BASE_PATH,mgiID),
							    numeric + '.pdf')
    except:
	return None, str(sys.exc_info()[1])

    if not os.path.exists(filePath):
	error = "Cannot find file: %s" % filePath
	filePath = None

    return filePath, None
# ----------------------------

def extractTextFromPDF(pdfPathName):
    """
    Extract text from the PDF file.
    Return (text, error)
	If text == None, error has a message.
	If text != None, it is the extracted text
    """
    text  = None
    error = None

    cmd = 'pdftotext -enc ASCII7 -q -nopgbrk %s -' % (pdfPathName)
    stdout, stderr, retcode = runCommand.runCommand(cmd) 
    if retcode != 0:
	error = "pdftotext error: %d<p>%s<p>%s<p>%s" % \
						(retcode, cmd, stderr, stdout)
    else:
	text = stdout

    return text, error
# ----------------------------

def getUploadedPDF(uploadDesc):
    """
    Get extracted text from uploaded PDF.
    Return Reference object if no errors,
	else return error message
    JIM: make this more error/exception tolerent
    """
    # Set pathName where to write the tmp PDF file so we can run pdftotext
    fileName   = os.path.basename(uploadDesc.filename)
    uniquefier = str(time.time()) + '__'
    uFileName  = uniquefier + fileName
    pathName   = os.path.join(UPLOAD_DIR, uFileName)

    try:
	# Read it into contents
	contents = uploadDesc.file.read()
	debug('contents length %d' % len(contents))

	# Write contents to pathName
	debug('writing %s' % pathName)
	fp = open(pathName, 'wb')
	fp.write(contents)
	fp.close()
	os.chmod(pathName, 0666)  # all rw so anyone can rm if things crap out

	# Extract text, get rid of tmp PDF file
	extractedText, error = extractTextFromPDF(pathName)

	os.remove(pathName)
	debug('removed %s' % pathName)
    except:
	error = str(sys.exc_info()[1])

    if error: return error

    # Build/return refInfo
    noDB = "N/A"
    refInfo = ReferenceInfo('',				# pubmedID
			    '',				# mgiID
			    '',				# jnumID
			    "Uploaded: %s" % fileName,	# filename as "citation"
			    '',				# title
			    '',				# ref type
			    noDB,			# isreview
			    noDB,			# isdiscard
			    extractedText,
			    )
    refInfo.pdfLink = 'N/A'

    return refInfo
# ----------------------------

def buildReferenceDetails(refInfo):
    """
    Return html (text string) for the reference detail display
    """
    splitter = extractedTextSplitter.ExtTextSplitter()

    bodyS, refsS, mfigS, starS, suppS = splitter.findSections( \
							refInfo.extractedText)
    reason = refsS.reason
    refStart = refsS.sPos
    lenText = len(refInfo.extractedText)

    textareaWidth = 150
    textareaHeight = 4
    pdfLink = refInfo.pdfLink
    body = [
    '''
    <TABLE>
    <TR>
	<TH>Link</TH>
	<TH>IDs</TH>
	<TH>Title</TH>
	<TH>Citation</TH>
	<TH>Other</TH>
    </TR>
    <TR>
    ''',
	'<TD> %s </TD>' % pdfLink,
	'<TD style="font-size: x-small"> %s<br>%s<br>%s </TD>' % \
			    (refInfo.jnumID, refInfo.mgiID, refInfo.pubmedID),
	'<TD style="font-size: small"> %s </TD>' % refInfo.title,
	'<TD style="font-size: small"> %s </TD>' % refInfo.citation,
    '''
	<TD style="font-size: xx-small">
	    Doc length:&nbsp;%d<br>
	    %s<br>
	    isReview:&nbsp;%s<br>
	    isDiscard:&nbsp;%s<br>
	</TD>
    '''	% (lenText, refInfo.referenceType, refInfo.isReview, refInfo.isDiscard),
    '''
    <TR>
    </TABLE>
    ''',
    '''
    <p>
    <b>Body</b> <small>%d to %d, %d chars</small>
    <BR>
    ''' % (bodyS.sPos, bodyS.ePos, bodyS.ePos - bodyS.sPos),
	'<textarea rows="%d" cols="%d">' % (textareaHeight, textareaWidth),
	 bodyS.text,
	'</textarea>',
    '''
    <p>
    <b>Ref Section</b> <small>%d to %d, %d chars, Reason: "%s"</small>
    <BR>
    ''' % (refsS.sPos, refsS.ePos, refsS.ePos - refsS.sPos, refsS.reason),
	'<textarea rows="%d" cols="%d">' % (textareaHeight, textareaWidth),
	refsS.text,
	'</textarea>',
    '''
    <p>
    <b>Manuscript Figs  Section</b> <small>%d to %d, %d chars, Reason: "%s"</small>
    <BR>
    ''' % (mfigS.sPos, mfigS.ePos, mfigS.ePos - mfigS.sPos, mfigS.reason),
	'<textarea rows="%d" cols="%d">' % (textareaHeight, textareaWidth),
	mfigS.text,
	'</textarea>',
    '''
    <p>
    <b>Star Methods Section</b> <small>%d to %d, %d chars, Reason: "%s"</small>
    <BR>
    ''' % (starS.sPos, starS.ePos, starS.ePos - starS.sPos, starS.reason),
	'<textarea rows="%d" cols="%d">' % (textareaHeight, textareaWidth),
	starS.text,
	'</textarea>',
    '''
    <p>
    <b>Supplemental Data Section</b> <small>%d to %d, %d chars, Reason: "%s"</small>
    <BR>
    ''' % (suppS.sPos, suppS.ePos, suppS.ePos - suppS.sPos, suppS.reason),
	'<textarea rows="%d" cols="%d">' % (textareaHeight, textareaWidth),
	suppS.text,
	'</textarea>',
    '''
    <p>
    <b>Whole extracted text</b> <small>%d chars</small>
    <BR>
    ''' % (lenText),
	'<textarea rows="%d" cols="%d">' % (textareaHeight, textareaWidth),
	refInfo.extractedText,
	'</textarea>',
    ]
    return '\n'.join(body)
# ----------------------------

def getParameters():
    """
    Return dict {formfield_name : value}
	values are
	    string if the formfield is a simple param
	    cgi.FieldStorage object if the formfield is an uploaded file
    Print parameter summary/report if DEBUG
    """
    global DEBUG
    form = cgi.FieldStorage()

    debug("<p>Parameters")
    params = {}
    for k in form.keys():
	#params[k] = form.getvalue(k)
	params[k] = form[k]
	if form[k].filename:		# have an uploaded file 
	    params[k] = form[k]
	    debug( "%s: upload file '%s'" % (k, params[k].filename) )
	    debug( "Class: %s" % params[k].__class__)
	    debug( repr(params[k].__dict__) + '<br>' )
	else:				# assume we have a string
	    params[k] = form.getvalue(k)
	    debug( "%s: '%s'" % (k, params[k]) )
	if k == 'debug':	# not sure we can make this work w/ POST
	    DEBUG = (params[k] == 'true')
    debug("End Parameters<p>")
    return params
# ----------------------------

def writePage():
    """
    Main
    """
    writeHeader()
    debug('through initialization')

    params = getParameters()

    form = ['''
	    <DIV CLASS="search">
	    <FORM ACTION="splitter.cgi" METHOD="POST" enctype="multipart/form-data">
	    <b>Ref ID </b>
	    <INPUT NAME="refID" TYPE="text" SIZE="25" autofocus>
	    &nbsp;&nbsp; or &nbsp;&nbsp <b> Upload PDF </b>
	    <INPUT type="file" id="pdffile" name="pdffile" accept=".pdf" >
	    <INPUT TYPE="submit" VALUE="View Split">
	    </FORM>
	    </DIV>
	    ''']
	    #'<INPUT NAME="isHidden" TYPE="hidden" VALUE="cannot see me">',
    refInfo = ''

    if params.has_key('refID') and params['refID'] != '': # get ref/PDF via ID
	refInfo = getReferenceInfo(params['refID'])

    elif params.has_key('pdffile')  \
		and type(params['pdffile']) != type(''):  # upload PDF
	refInfo = getUploadedPDF(params['pdffile'])

    if type(refInfo) == type(''):		# have error msg
	refDisplay = refInfo
    else:					# have a reference
	refDisplay = buildReferenceDetails(refInfo)

    body = '\n'.join(form) + refDisplay
    tail = '</BODY></HTML>'

    print body + tail
    return
# ----------------------------
# MAIN
# ----------------------------
writePage()
