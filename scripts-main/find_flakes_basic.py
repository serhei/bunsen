#!/usr/bin/env python3
# Find 'nondeterministic' testcases which produce
# different outcomes on the same commit & configuration.
# 1st version, simplified for a 'live coding' demo.
#
# run with ./bunsen.py +find_flakes | sort -n -r
cmdline_args = [
    ('source_repo', None, '<path>',
     "scan commits from source repo <path>"),
    ('branch', 'master', '<name>',
     "scan commits in branch <name>"),
    ('project', None, '<project>',
     "restrict analysis to <project>"),
]

import sys
import bunsen
from git import Repo

import tqdm

def add_list(t, k, v):
    if k not in t: t[k] = []
    t[k].append(v)

def add_set(t, k, v):
    if k not in t: t[k] = set()
    t[k].add(v)

from list_commits import get_source_commit

def get_config(testrun):
    # XXX simplified
    return (testrun.arch, testrun.osver)

from diff_runs import fail_outcomes
def is_pass(tc):
    # XXX simplified
    return tc.outcome not in fail_outcomes

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv,
                          args=cmdline_args,
                          required_args=['source_repo'],
                          optional_args=['project'])

    repo = Repo(opts.source_repo)

    # maps (commit, config) -> list(Testrun):
    all_testruns = {}
    for testrun in b.testruns(opts.project):
        commit, config = \
            get_source_commit(testrun), \
            get_config(testrun)
        if commit is None: continue
        add_list(all_testruns, (commit, config), \
                 testrun)

    # maps (tc_info, config) -> set(commit)
    # where tc_info is (name, subtest, outcome)
    all_testcases = {}
    n_all = 0
    n_sets = 0
    for commit, config in tqdm.tqdm(all_testruns, \
        desc='Scanning configurations', unit='configs'):
        n_all += 1
        if len(all_testruns[commit, config]) <= 1:
            continue # no possibility of flakes
        n_sets += 1
        # maps tc_info -> list(testrun)
        commit_testcases = {}
        for testrun in all_testruns[commit, config]:
            testrun = b.full_testrun(testrun)
            for tc in testrun.testcases:
                if is_pass(tc): continue
                name, subtest, outcome = \
                    tc.name, tc.subtest, tc.outcome
                tc_info = tc.name, tc.subtest, tc.outcome
                add_list(commit_testcases, tc_info, \
                         testrun)
        n_testruns = len(all_testruns[commit, config])
        for tc_info in commit_testcases:
            if len(commit_testcases[tc_info]) < n_testruns:
                # XXX tc_info doesn't appear in all runs
                add_set(all_testcases, tc_info, \
                        commit)

    #print("9000 -> {} total sets".format(n_all))
    #print("9001 -> {} total sets with >1 testrun analyzed".format(n_sets))
    for tc_info in all_testcases:
        print(len(all_testcases[tc_info]),
              "commits have nondeterministic",
              tc_info)
