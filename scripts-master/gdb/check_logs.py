#!/usr/bin/env python3
# WIP one-off -- Check which GDB test logs are present in a Bunsen git repo.
usage = "+check_logs [raw_logs=]<path>"
default_args = {'raw_logs':None, # raw buildbot log repository
               }

# This assumes the format of the public GDB buildbot data:
# - https://gdb-buildbot.osci.io/results/
# - https://gdb-build.sergiodj.net/results/
#
# BUNSEN_COMMIT files are ignored. I'm prototyping a different way of
# monitoring what's already been committed.
#
# TODO: Then roll this into a dry-run mode of commit_logs.py.

import sys
from bunsen import Bunsen, Testrun

import os

from list_commits import get_source_commit
from parse_dejagnu import parse_README
from commit_logs import traverse_logs

def check_logs(b, log_src):
    '''
    Check which logs from local path log_src have been committed.
    '''
    # TODO: Share hexsha_lens scheme with list_commits.py.
    all_osver_shas = set()
    hexsha_lens = set()
    tags = b.tags
    total_in_repo = 0
    for tag in tags:
        for testrun in b.testruns(tag):
            total_in_repo += 1
            hexsha = get_source_commit(testrun)
            if hexsha is None:
                print("WARNING: could not find a source commit for testrun:\n{}" \
                      .format(testrun.to_json(summary=True)), file=sys.stderr)
                continue
            hexsha_lens.add(len(hexsha))
            all_osver_shas.add(testrun.osver+'+'+hexsha)

    total = 0
    n_found, n_unknown = 0, 0
    for osver, test_sha, testdir in traverse_logs(log_src):
        total += 1

        # Doublecheck that source_commit matches test_sha:
        gdb_README = os.path.join(testdir, 'README.txt')
        testrun = parse_README(Testrun(), gdb_README)
        if testrun is None:
            print("? {}".format(testdir))
            n_unknown += 1
            continue
        source_sha = get_source_commit(testrun)
        if source_sha != test_sha:
            print("WARNING: source commit {} does not match test_sha in {}" \
                  .format(source_sha, testdir))

        found = False
        for k in hexsha_lens:
            if osver+'+'+test_sha[:k] in all_osver_shas:
                found = True
                break
        if not found:
            print("+ {}".format(testdir))
            #print("+ {} -> {}+{}".format(testdir, osver, test_sha))
            n_found += 1
    if n_found + n_unknown > 0:
        print("===")
    print("{} runs not in repo, {} with unknown README format of total {} runs" \
          .format(n_found, n_unknown, total))
    print("found total {} runs in repo with {} unique osver+sha pairs" \
          .format(total_in_repo, len(all_osver_shas)))

b = Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, usage=usage, required_args=['raw_logs'],
                          defaults=default_args)
    check_logs(b, opts.raw_logs)
