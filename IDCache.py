# Name: IDCache.py
# Purpose: provides caching of IDs for the pdfviewer.cgi script, so we can still return
#   PDF files when the database is offline for some reason
# Notes:
#   1. Cache files live in directory specified by CACHE_DIR global variable.
#   2. One cache file is used to map from _Refs_key to MGI ID / J# pair
#   3. Ten more cache files for mapping from all IDs to _Refs_keys, aiding efficiency.
#       (Use hash of ID mod 10 to evenly distribute IDs across the files.)
#   4. Searching involves shelling out to use grep twice:
#        once to identify the key, once to get the MGI ID and J#

import glob
import os
import subprocess
import Configuration

###--- globals ---###

config = Configuration.Configuration('Configuration')

CACHE_DIR = config['CACHE_DIR']
LOOKUP_FILENAME = 'pdfviewer.idCache.lookup'
SEARCH_FILENAME_PREFIX = 'pdfviewer.idCache.search'
NUM_BUCKETS = 10

###--- classes ---###

class ProfilerAware:
    def setStampFn (self, stampFn):
        self._stamp = stampFn
        return
        
    def stamp (self, msg):
        if self._stamp != None:
            self._stamp(msg)
        return 
    
class CacheSearcher (ProfilerAware):
    # Is: a searches for the ID caches
    # Has: knowledge of how to search the disk caches of IDs
    # Does: takes any reference ID and returns a tuple with (MGI ID, J#)
    
    def __init__ (self, profilerStamp = None):
        self.setStampFn(profilerStamp)
        return
    
    def lookup (self, refID):
        lookupPath = os.path.join(CACHE_DIR, LOOKUP_FILENAME)
        if not os.path.exists(lookupPath):
            raise Exception("Missing ID Cache File: %s" % lookupPath)

        lowerID = refID.strip().lower()
        index = hash(lowerID) % NUM_BUCKETS
        searchPath = os.path.join(CACHE_DIR, SEARCH_FILENAME_PREFIX + str(index))
        if not os.path.exists(searchPath):
            raise Exception("Missing ID Cache File: %s" % searchPath)
        
        exitcode, stdout = subprocess.getstatusoutput("grep '^%s\t' %s" % (lowerID, searchPath))
        if not stdout:
            raise Exception("Cannot find ID %s in cache file %s" % (refID, searchPath))

        refsKey = stdout.strip().split('\n')[0].split('\t')[1]
        self.stamp('Found refs key %s for ID %s' % (refsKey, refID))
        
        exitcode, stdout = subprocess.getstatusoutput("grep '^%s\t' %s" % (refsKey, lookupPath))
        if not stdout:
            raise Exception("Cannot find key %s in lookup file %s" % (refsKey, lookupPath))

        mgiID, jnumID = stdout.strip().split('\n')[0].split('\t')[1:3]
        return mgiID, jnumID

class CacheBuilder (ProfilerAware):
    # Is: a builder for the ID caches
    # Has: a function of accessing the database (pass in a reference to the pg_db.sql function, once
    #    the pg_db module has been initialized
    # Does: removes any existing cache files, queries the database, builds new cache files
    
    def __init__ (self, sqlFunction, profilerStamp = None):
        self.sql = sqlFunction
        self.setStampFn(profilerStamp)
        return
    
    def cacheIDs (self):
        self._removeOldCaches()
        self._buildNewCaches()
        return
    
    def _removeOldCaches (self):
        removed = 0
        failed = 0
        for cacheFile in glob.glob(os.path.join(CACHE_DIR, 'idCache.*')):
            try:
                os.remove(cacheFile)
                removed = removed + 1
            except:
                failed = failed + 1

        self.stamp('Removed %d files, failed on %d' % (removed, failed))
        return
    
    def _buildNewCaches (self):
        self._buildLookupFile()
        self._buildSearchFiles()
        return
    
    def _buildLookupFile (self):
        # builds the file that maps from _Refs_key to MGI ID and J#
       
        filepath = os.path.join(CACHE_DIR, LOOKUP_FILENAME)
        fp = open(filepath, 'w')
        
        cmd = '''select _Refs_key, coalesce(jnumid, '') as jnumid, mgiid from bib_citation_cache'''
        rows = self.sql(cmd, 'auto')
        for row in rows:
            fp.write('%s\t%s\t%s\n' % (row['_Refs_key'], row['mgiid'], row['jnumid']))
            
        fp.close()
        exitcode, stdout = subprocess.getstatusoutput('chmod o+r %s' % filepath)
        exitcode, stdout = subprocess.getstatusoutput('chmod g+w %s' % filepath)
        self.stamp('Put %d refs in lookup bucket' % len(rows))
        return
    
    def _buildSearchFiles (self):
        # builds the files that map from each (lowercase) ID to _Refs_key
        
        buckets = []                    # list of file pointers, one per bucket
        for i in range(0, NUM_BUCKETS):
            buckets.append(open(os.path.join(CACHE_DIR, SEARCH_FILENAME_PREFIX + str(i)), 'w'))
            
        cmd = '''select _Object_key as _Refs_key, accID
            from acc_accession a
            where a._MGIType_key = 1'''
        rows = self.sql(cmd, 'auto')
        
        for row in rows:
            lowerID = row['accID'].lower()
            bucketIndex = hash(lowerID) % NUM_BUCKETS
            buckets[bucketIndex].write('%s\t%s\n' % (lowerID, row['_Refs_key']))
            
        for bucket in buckets:
            bucket.close()

        for i in range(0, NUM_BUCKETS):
            filepath = os.path.join(CACHE_DIR, SEARCH_FILENAME_PREFIX + str(i))
            exitcode, stdout = subprocess.getstatusoutput('chmod o+r %s' % filepath)
            exitcode, stdout = subprocess.getstatusoutput('chmod g+w %s' % filepath)

        self.stamp('Put %d IDs in %d buckets' % (len(rows), len(buckets)))
        return
    
