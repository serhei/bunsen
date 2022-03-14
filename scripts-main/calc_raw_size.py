#!/usr/bin/env python3

import sys
import bunsen
from git import Repo

# TODOXXX make into a method of Bunsen class
from bunsen import Testlog
def b_testlogs(b, testrun):
    testlogs = []
    try:
        commit = b.git_repo.commit(testrun.bunsen_commit_id)
    except:
        return []
    for blog in commit.tree:
        # XXX testlog = b.testlog(blob.path, testrun.bunsen_commit_id)
        testlog = Testlog(b, path=blob.path, commit_id=testrun.bunsen_commit_id, blob=blob)
        testlogs.append(testlog)
    return testlogs

# TODOXXX make into a method of Testlog class
def testlog_size(testlog):
    ds = testlog._data_stream
    return ds.read().size()

#b = Bunsen.from_cmdline()
b = bunsen.Bunsen()
if __name__=='__main__':
    total = 0
    num = 0
    for project in b.projects:
        out.section()
        out.message(project=tag)
        for testrun in b.testruns(tag, key_function=key_function):
            for testlog in b_testlogs(b, testrun):
                sz = testlog_size(testlog)
                print(f"{testlog.path} -> {sz}")
                total += sz
            num += 1
    print("TOTAL {total} bytes in {num} testruns")
