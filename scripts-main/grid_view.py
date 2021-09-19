#!/usr/bin/env python3
info='''WIP/EXPERIMENTAL based on a script by Martin Cermak.
Show a grid of test results for recent commits.'''
cmdline_args = [
    ('project', None, '<tags>',
     "restrict to testruns under <tags>"),
    ('key', None, '<glob>',
     "restrict to testcases matching <glob>"),
    ('source_repo', None, '<path>',
     "scan commits from source_repo <path>"),
    ('gitweb_url', None, '<url>',
     "for pretty=html only -- link to gitweb at <url>"),
    ('branch', 'master', '<name>',
     "scan commits in branch <name>"),
    ('latest', None, '<source_commit>',
     "last commit for which to show testruns"),
    ('baseline', None, '<source_commit>',
     "first commit for which to show testruns"),
    ('show_subtests', False, None,
     "show subtest details (increases file size significantly)"),
    # XXX no option 'pretty': for now, always output HTML
    ('filter_unchanged', True, None,
     "filter out testcases whose fail counts did not change"),
]

import sys
import bunsen
from git import Repo
from fnmatch import fnmatchcase

import tqdm

from list_commits import index_source_commits, iter_history
from diff_runs import append_map, fail_outcomes, untested_outcomes
from diff_commits import index_summary_fields, get_summary, get_summary_key, get_tc_key
from common.format_output import get_formatter

# TODOXXX use in other scripts; move to show_testcases.py
def refspec_matches(repo, refspec, hexsha_prefix):
    return repo.commit(refspec).hexsha.startswith(hexsha_prefix)

def get_grid_key(testcase_name, summary_key, hexsha):
    return "{}+{}+{}".format(testcase_name, summary_key, hexsha)

def merge_outcome(outcomes_grid, gk, outcome):
    if outcome in untested_outcomes:
        return
    if outcome in fail_outcomes:
        outcomes_grid[gk] = 'FAIL'
    if gk not in outcomes_grid:
        outcomes_grid[gk] = 'PASS'
    if outcomes_grid[gk] == 'FAIL':
        return

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['baseline','latest'],
                          optional_args=['source_repo'])
    opts.pretty = 'html' # XXX for now, always output HTML
    out = get_formatter(b, opts)

    tags = opts.get_list('project', default=b.tags)
    repo = Repo(opts.source_repo)

    # (1a) find all testruns between the specified commits
    testruns_map, hexsha_lens = index_source_commits(b, tags)
    commit_range = [] # list of (commit, testruns)
    all_testruns = [] # list of testruns
    started_range = False
    n_commits = 0
    for commit, testruns in iter_history(b, repo, testruns_map, hexsha_lens,
                                         forward=True, branch=opts.branch,
                                         include_empty_commits=True):
        n_commits += 1
        if not started_range and refspec_matches(repo, opts.baseline, commit.hexsha):
            started_range = True
        if not started_range:
            continue
        commit_range.append((commit, testruns))
        all_testruns += testruns
        if refspec_matches(repo, opts.latest, commit.hexsha):
            break

    # (1b) find summary fields present in all testruns
    header_fields, summary_fields = index_summary_fields(all_testruns)
    # XXX summary_fields may also include source_commit, version
    # which are not used in get_summary. header_fields excludes these.

    # (1b) collect the testcases for all commits in the range
    testcase_names = set()
    # TODOXXX skip over completely-untested testcases with a message 'no results' instead of a table
    # TODOXXX skip over identical-results testcases with a message 'no changes' instead of a table
    testcase_configurations = {} # testcase_name -> set of summary_key
    configurations = {} # summary_key -> configuration_summary
    outcomes_grid = {} # testcase_name+summary_key+hexsha -> outcome {PASS,FAIL} only
    subtests_grid1 = {} # testcase_name+summary_key+hexsha -> set of tc_key(name+outcome+subtest)
    subtests_info = {} # tc_key -> testcase
    # TODOXXX subtests_grid = {} # testcase_name+summary_key+hexsha -> list of testcase + refs to original testruns/configurations

    progress = tqdm.tqdm(iterable=None, desc='Scanning commits',
                         total=len(commit_range), leave=True, unit='commit')

    for commit, testruns in commit_range:
        for testrun in testruns:
            summary = get_summary(testrun, summary_fields)
            sk = get_summary_key(summary)
            if sk not in configurations:
                configurations[sk] = summary
            testrun = b.testrun(testrun)
            for testcase in testrun.testcases:
                if opts.key is not None and \
                   not fnmatchcase(testcase.name, opts.key):
                    continue
                if testcase.name not in testcase_names:
                    testcase_names.add(testcase.name)
                if testcase.name not in testcase_configurations:
                    testcase_configurations[testcase.name] = set()
                testcase_configurations[testcase.name].add(sk)
                gk = get_grid_key(testcase.name, sk, commit.hexsha)
                merge_outcome(outcomes_grid, gk, testcase.outcome)
                tk = get_tc_key(testcase) # TODOXXX exclude baseline_outcome
                # TODOXXX need to support gdb repo with separate pass-subtest storage
                if gk not in subtests_grid1:
                    subtests_grid1[gk] = set()
                subtests_grid1[gk].add(tk)
                subtests_info[tk] = testcase
                pass # TODOXXX add to subtests_grid for more details
        progress.update(n=1)

    # XXX (1c) for tracking which testcases to skip over
    testcase_unchanged = set()
    testcase_untested = set()
    max_fails = {} # testcase_name -> max # of fails seen
    n_configs = {} # testcase_name -> # of configs seen
    # XXX since results don't change, calculation of n_configs is simple
    # however, a calculation on all testcases for ranking is more complex
    if opts.filter_unchanged:
        testcase_state = {} # testcase_name+summary_key -> # of fails expected
        for testcase_name in testcase_names:
            is_unchanged = True
            is_untested = True
            n_configs[testcase_name] = 0
            failed_configs = set()
            for sk in testcase_configurations[testcase_name]:
                state_key = testcase_name+'+'+sk
                for commit, _testruns in commit_range:
                    gk = get_grid_key(testcase_name, sk, commit.hexsha)
                    if gk not in outcomes_grid:
                        continue
                    is_untested = False
                    n_fails = 0
                    if outcomes_grid[gk] == 'FAIL' and gk in subtests_grid1:
                        n_fails = len(subtests_grid1[gk])
                        if testcase_name not in max_fails or n_fails > max_fails[testcase_name]:
                            max_fails[testcase_name] = n_fails
                        failed_configs.add(sk)
                    if state_key not in testcase_state:
                        testcase_state[state_key] = n_fails
                    elif testcase_state[state_key] != n_fails:
                        is_unchanged = False
            if is_unchanged:
                testcase_unchanged.add(testcase_name)
                n_configs[testcase_name] = len(failed_configs)
            if is_untested:
                testcase_untested.add(testcase_name)

    # (2) for each testcase, show a grid of test results
    testcase_names = list(testcase_names)
    testcase_names.sort()
    n_testcase_names = 0
    for testcase_name in testcase_names:
        # XXX skip without making a section
        if testcase_name in testcase_untested or testcase_name in testcase_unchanged:
            pass # TODOXXX continue

        out.section()
        out.message(testcase_name)

        # XXX skip while still including the section
        # TODOXXX comment out 'continue' to verify results
        if testcase_name in testcase_untested:
            out.message("no test results over specified time period")
            continue
        elif testcase_name in testcase_unchanged:
            msg = "no failure count changes over specified time period"
            if testcase_name in max_fails:
                # TODOXXX HTML ONLY
                msg += "<br/>" + "(failures occur in up to {} subtests on up to {} configurations)".format(max_fails[testcase_name], n_configs[testcase_name])
            out.message(msg)
            continue

        n_testcase_names += 1
        for sk in testcase_configurations[testcase_name]:
            summary = configurations[sk]
            # TODOXXX HTML table should default to showing columns in order added
            # XXX show earliest commits on the right
            field_order = list(header_fields) + ['first']
            for commit, _testruns in reversed(commit_range):
                # TODOXXX add commit msg to header tooltip?
                hexsha = commit.hexsha[:7]
                field_order.append(hexsha)
                if opts.gitweb_url is not None:
                    commitdiff_url = opts.gitweb_url + ";a=commitdiff;h={}" \
                        .format(commit.hexsha)
                    out.table.header_href[hexsha] = commitdiff_url # XXX HACK
            out.table_row(summary, order=field_order)
            # XXX for glanceability, show the first commit on the very left
            first_val = '?' # <- will be the rightmost value
            for commit, _testruns in reversed(commit_range):
                gk = get_grid_key(testcase_name, sk, commit.hexsha)
                hexsha = commit.hexsha[:7]
                # TODOXXX mark category = pass, fail, better, worse
                # TODOXXX
                #n_testcases = 0
                uniq_subtests = {}
                if gk in subtests_grid1:
                    tc_keys = subtests_grid1[gk]
                    for tk in subtests_grid1[gk]:
                        #n_testcases += 1
                        testcase = subtests_info[tk]
                        if 'subtest' not in testcase:
                            continue
                        if testcase.subtest not in uniq_subtests:
                            uniq_subtests[testcase.subtest] = 0
                        uniq_subtests[testcase.subtest] += 1
                n_testcases = len(uniq_subtests)
                details = None
                if opts.show_subtests:
                    details = ""
                    need_br = False
                    for st, num in uniq_subtests.items():
                        if need_br: details += "<br/>" # XXX HTML ONLY
                        if num > 1:
                            details += "{}x {}".format(num, st)
                        else:
                            details += st
                            need_br = True

                if gk not in outcomes_grid:
                    out.table_cell(hexsha, '?')
                    # out.table_cell(hexsha, '?') # XXX may be too visually cluttered
                elif outcomes_grid[gk] == "PASS":
                    out.table_cell(hexsha, '+')
                    first_val = '+'
                elif outcomes_grid[gk] == "FAIL":
                    out.table_cell(hexsha, '-'+str(n_testcases), details=details) # XXX mark number of fails
                    first_val = '-'+str(n_testcases)
                else:
                    # XXX should not happen
                    out.table_cell(hexsha, '???')
            out.table_cell('first',first_val)
    out.section()
    out.message("showing {} testcases out of {} total".format(n_testcase_names, len(testcase_names)))
    out.message("showing {} commits out of {} total for branch {}".format(len(commit_range), n_commits, opts.branch))

    out.finish() # TODOXXX out.finish() should be called on __del__?
