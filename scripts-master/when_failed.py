#!/usr/bin/env python3
# Walk the history of the master branch of the Git repo
# <source_repo>. For every commit, compare testruns under <project>
# with testruns for the parent commit.  Print a summary of how test
# results changed for test cases whose name contains <key>.
usage = "when_fail.py <source_repo> <project> <key>"

# TODO: Suggested options:
# - increase/decrease verbosity
# - sort commits by most-recent/least-recent first
# - restrict to N most recent commits
# - list commits for a different branch
# - change/disable the is_similar check

import sys
import bunsen
from git import Repo

from list_commits import index_source_commits, iter_history

# TODO: Compute a detailed diff rather than this crude numerical tally.
class Totals:
    def __init__(self):
        self.by_name = {} # tc_name -> # found
        self.all_outcomes = {} # tc_name -> set of outcomes
        self.by_name_outcome = {} # tc_name x tc_outcome -> # found

    def add_name_outcome(self, tc_name, tc_outcome):
        if tc_name not in self.by_name:
            self.by_name[tc_name] = 0
        self.by_name[tc_name] += 1
        if tc_name not in self.all_outcomes:
            self.all_outcomes[tc_name] = set()
        self.all_outcomes[tc_name].add(tc_outcome)
        tc_name_outcome = tc_name + ' x ' + tc_outcome
        if tc_name_outcome not in self.by_name_outcome:
            self.by_name_outcome[tc_name_outcome] = 0
        self.by_name_outcome[tc_name_outcome] += 1

    def is_similar(self, other_totals, tc_name):
        # XXX: There are many possible definitions of 'similar';
        # this one is pretty strict.
        self_outcomes = self.all_outcomes[tc_name] \
            if tc_name in self.all_outcomes else None
        other_outcomes = other_totals.all_outcomes[tc_name] \
            if tc_name in other_totals.all_outcomes else None
        return self_outcomes == other_outcomes
        
    def summary(self, tc_name):
        if tc_name not in self.all_outcomes:
            return "<none>"
        s = ""
        initial = True
        for tc_outcome in self.all_outcomes[tc_name]:
            tc_name_outcome = tc_name + ' x ' + tc_outcome
            if not initial: s += "+"
            s += str(self.by_name_outcome[tc_name_outcome]) + tc_outcome
            initial = False
        return s

b = bunsen.Bunsen()
if __name__=='__main__':
    # TODO: source_repo_path, tag could take default values from b.config
    source_repo_path, tag, key = b.cmdline_args(sys.argv, 3, usage=usage)
    tags = b.tags if tag is None else [tag]
    repo = Repo(source_repo_path)

    testruns_map, hexsha_lens = index_source_commits(b, tags)
    commit, testruns, totals = None, None, None
    printed = False
    for prev_commit, prev_testruns in \
        iter_history(b, repo, testruns_map, hexsha_lens):
        # Build prev_commit, prev_testruns -> prev_totals
        prev_totals = Totals()
        for testrun in prev_testruns:
            testrun = b.full_testrun(testrun)
            for tc in testrun.testcases:
                tc_name, tc_outcome = tc['name'], tc['outcome']
                if key in tc_name:
                    prev_totals.add_name_outcome(tc_name, tc_outcome)

        prev_printed = False

        # TODO: Should be controlled by verbosity setting.
        print(prev_commit.hexsha[:7], prev_commit.summary); prev_printed = True

        # XXX skip on first iteration which has no next commit
        if totals is not None:
            found_difference = False
            for tc_name in totals.all_outcomes:
                if totals.is_similar(prev_totals, tc_name): continue

                s1 = totals.summary(tc_name)
                s2 = prev_totals.summary(tc_name)
                if s1 == s2: continue

                found_difference = True
                if not printed:
                    print(commit.hexsha[:7], commit.summary)
                    printed = True
                print("-", tc_name, s1, "<-", s2)
            if found_difference:
                # TODO: Should be controlled by verbosity setting.
                for testrun in testruns:
                    print("* {} {} {} pass {} fail" \
                          .format(testrun.year_month, testrun.bunsen_commit_id,
                                  testrun.pass_count, testrun.fail_count))
                    print(testrun.to_json())
                print()

            if found_difference and not prev_printed:
                print(prev_commit.hexsha[:7], prev_commit.summary)
                prev_printed = True

        commit, testruns, totals = prev_commit, prev_testruns, prev_totals
        printed = prev_printed

    # EXAMPLE CODE -- Printing all commits + testcases.
    #
    # testruns_map, hexsha_lens = index_source_commits(b, tags)
    # prev_commit, prev_testruns = None, None
    # for commit, testruns in iter_history(b, repo, testruns_map, hexsha_lens):
    #     print(commit.hexsha[:7], commit.summary)
    #     # find relevant testcases
    #     for testrun in testruns:
    #         print("* {} {} {} pass {} fail" \
    #               .format(testrun.year_month, testrun.bunsen_commit_id,
    #                       testrun.pass_count, testrun.fail_count))
    #         print(testrun.to_json())
    #         testrun = b.full_testrun(testrun) # XXX load testcases
    #         for tc in testrun.testcases:
    #             if key in tc['name']:
    #                 print ("  -", testrun.testcase_to_json(tc))
    #     print()
    #     prev_commit, prev_testruns = commit, testruns
