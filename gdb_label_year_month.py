#!/usr/bin/env python3

# XXX This is not a Bunsen analysis, just a standalone script to label
# a GDB buildbot repository with year_month.txt files for use with the
# 'timeslice' option in +gdb/commit_logs. You probably don't need to
# use this unless you've decided to prepare your own GDB dataset and
# are having difficulty doing it all at once.

import sys
import os
import dateparser
import lzma

# XXX Duplicates +gdb/parse_dejagnu openfile_or_xz():
def openfile_or_xz(path):
    if os.path.isfile(path):
        return open(path, mode='rt')
    elif os.path.isfile(path+'.xz'):
        return lzma.open(path+'.xz', mode='rt')
    return open(path, mode='rt') # XXX trigger default error

# XXX Duplicates +gdb/commit_logs find_file_or_xz():
def find_file_or_xz(testdir, name):
    if os.path.isfile(os.path.join(testdir, name)):
        return os.path.join(testdir, name)
    if os.path.isfile(os.path.join(testdir, name+'.xz')):
        return os.path.join(testdir, name+'.xz')
    return None

# XXX Duplicates +gdb/commit_logs is_testdir():
def is_testdir(testdir):
    if not os.path.isfile(os.path.join(testdir,'README.txt')):
        return False
    if find_file_or_xz(testdir,'gdb.log') is None:
        return False
    if find_file_or_xz(testdir,'gdb.sum') is None:
        return False
    return True

# XXX Duplicates some of the traversal code in +gdb/commit_logs
# and the parsing code in +gdb/parse_dejagnu annotate_dejagnu_log():
if __name__=='__main__':
    log_src = sys.argv[1]
    for logdir in os.listdir(log_src):
        logdir = os.path.join(log_src, logdir)
        if not os.path.isdir(logdir): continue
        for bigdir in os.listdir(logdir):
            # TODO: also check if bigdir is a testdir
            bigdir = os.path.join(logdir, bigdir)
            if not os.path.isdir(bigdir): continue
            for testdir in os.listdir(bigdir):
                test_sha = testdir
                testdir = os.path.join(bigdir, testdir)
                if not os.path.isdir(testdir): continue
                if not is_testdir(testdir): continue
                if os.path.isfile(os.path.join(testdir, "year_month.txt")):
                    #pass # XXX to test how long it takes to scan everything from scratch
                    continue
                logfile = os.path.join(testdir, "gdb.log")
                f = openfile_or_xz(logfile)
                year_month = None
                if True: # this is slow but more thorough
                    try:
                        relevant_lines = f.readlines()
                    except UnicodeDecodeError: # yep, it happens
                        relevant_lines = []
                if False: # this is fast but ugly and doesn't work with lzma
                    relevant_lines = []
                    first = f.readline()        # Read the first line.
                    second = f.readline()
                    third = f.readline()
                    relevant_lines += [first,second,third]
                    f.seek(-2, os.SEEK_END)     # Jump to the second last byte.
                    while f.read(1) != b"\n":   # Until EOL is found...
                        f.seek(-2, os.SEEK_CUR) # ...jump back the read byte plus one more.
                    last = f.readline()         # Read last line.
                    relevant_lines += [last]
                for line in relevant_lines:
                        if (line.startswith("Test Run By") and " on " in line) \
                           or (" completed at " in line):
                            if line.startswith("Test Run By"):
                                t1 = line.rfind(" on ") + len(" on ")
                            else:
                                t1 = line.find(" completed at ") \
                                    + len(" completed at ")
                            datestamp = line[t1:].strip()
                            try:
                                datestamp = dateparser.parse(datestamp)
                                # XXX datetime is a bit too brittle in practice.
                                #datestamp = datetime.strptime(datestamp, datestamp_format)
                                year_month = datestamp.strftime("%Y-%m")
                            except ValueError:
                                print("WARNING: unknown datestamp in line --",
                                      line, file=sys.stderr)
                f.close()
                if year_month is not None:
                    print("FOUND {} for {}".format(year_month, testdir))
                    with open(os.path.join(testdir,"year_month.txt"), 'w') as f:
                        f.write(year_month)
                else:
                    print("NO YEAR_MONTH FOUND FOR {}".format(testdir))
                

