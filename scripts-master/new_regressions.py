#!/usr/bin/env python3
# Walk the history of the specified branch (default master) of the Git
# repo source_repo. For every commit, compare testruns under specified
# project with testruns for the parent commit. Report all regressions
# that did not already appear within the previous window_size (default
# infinity) commits.
usage = "new_regressions.py [[key=]<glob>] [[source_repo=]<path>] [branch=<name>] [project=<tag>] [window_size=<num>]"
default_args = {'project':None,     # restrict to testruns under <tag>
                'key':None,         # restrict to testcases matching <glob>
                'source_repo':None, # scan commits from source_repo
                'branch':'master',  # scan commits in branch <name>
                'window_size':-1,   # check against last N commits (0 for unbounded)
                'restrict':-1,      # TODOXXX restrict analysis to last N commits (0 for unbounded)
                'pretty':True,      # TODOXXX
               }

# TODO: Suggested options:
# - Restrict analysis to last N commits.
# - Restrict display to last N commits.
# - List in reverse order.

import sys
import bunsen
from git import Repo

import tqdm

# TODO: Add command line option to enable/disable profiler.
#import cProfile, pstats, io
#profiler = cProfile.Profile()
profiler = None

from common.format_output import get_formatter
from list_commits import index_source_commits, iter_history, iter_adjacent
from diff_runs import diff_testruns, fail_outcomes
from diff_commits import get_tc_key, find_summary_fields, summary_tuple

# TODOXXX merge to other scripts, harmonize with append_map() type functions
def pick_comparisons(summary_fields, baseline_runs, latest_runs,
                     merge_comparisons=False):
    '''Returns best_overall, comparisons, alt_comparisons.

    - summary_key := summary_tuple minus source_commit, version
    - comparisons,alt_comparisons := summary_key -> (baseline, latest)

    Here comparisons contains comparisons with matching summary,
    alt_comparisons contains other 'best effort' comparisons, where
    summary_key is the summary of configuration for latest.

    If merge_comparisons=True, alt_comparisons are added to comparisons
    wherever a matching comparison is missing.'''

    # (2a) build maps of metadata->testrun to match testruns with similar configurations
    # summary_key := (summary_tuple minus source_commit, version)
    baseline_map = {} # summary_key -> testrun
    for testrun in baseline_runs:
        t = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
        # XXX a sign of duplicate runs in the repo :(
        #assert t not in baseline_map # XXX would be kind of unforeseen
        baseline_map[t] = testrun
    latest_map = {} # summary_key -> testrun
    for testrun in latest_runs:
        t = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
        # XXX a sign of duplicate runs in the repo :(
        #assert t not in latest_map # XXX would be kind of unforeseen
        latest_map[t] = testrun

    # (2b) identify baseline testrun for baseline_commit
    # Everything will be compared relative to this single baseline.
    #
    # Reasoning
    # - prefer largest number of pass
    # - (XXX) prefer tuple present in both baseline_runs and latest_runs
    #   (minus source_commit, version data)
    best_overall = None
    best_with_latest = None
    for testrun in baseline_runs:
        t = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
        if t in latest_map:
            if best_with_latest is None \
               or int(testrun['pass_count']) > int(best_with_latest['pass_count']):
                best_with_latest = testrun
        if best_overall is None \
           or int(testrun['pass_count']) > int(best_overall['pass_count']):
            best_overall = testrun

    # (XXX) this rule needs refinement with incomplete datasets
    #if best_with_latest is not None:
    #    best_overall = best_with_latest

    comparisons = {}
    alt_comparisons = {}
    t1 = summary_tuple(best_overall, summary_fields)
    t1_exclude = summary_tuple(best_overall, summary_fields, exclude={'source_commit','version'})
    for testrun in latest_runs:
        t2 = summary_tuple(testrun, summary_fields)
        t2_exclude = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
        baseline, preferred_t1 = best_overall, t1
        if t2_exclude in baseline_map:
            baseline = baseline_map[t2_exclude]
            preferred_t1 = summary_tuple(baseline, summary_fields)
            comparisons[t2_exclude] = (baseline, testrun)
        else:
            alt_comparisons[t2_exclude] = (baseline, testrun)

    if merge_comparisons:
        for summary_key, comparison in alt_comparisons:
            if summary_key not in comparisons:
                comparisons[summary_key] = comparison

    return best_overall, comparisons, alt_comparisons

# TODOXXX merge to other scripts, harmonize with append_map() type functions
def assign_map2(m, key1, key2, val):
    if key1 not in m: m[key1] = {}
    m[key1][key2] = val

# TODOXXX merge to other scripts, harmonize with append_map() type functions
def del_map2(m, key1, key2):
    if key1 not in m: return
    if key2 not in m[key1]: return
    del m[key1][key2]
    if len(m[key1]) == 0: del m[key1]

# TODOXXX merge to other scripts, harmonize with append_map() type functions
def incr_map(m, key):
    if key not in m: m[key] = 0
    m[key] += 1

class Regression:
    def __init__(self, tc):
        self.tc = tc
        self.to_prev = None
        self.to_next = None
        self.num_flakes = 0

    @property
    def is_failing(self):
        return self.tc['baseline_outcome'] not in fail_outcomes \
            and self.tc['outcome'] in fail_outcomes

    # TODO: Estimate how many regressions will be revealed by lowering window_size.
    @property
    def separation(self):
        # XXX for display only
        dist = self.to_prev if self.is_failing else self.to_next
        if dist is None: return 'infinity'

    def separation_over(self, threshold, to_next=False):
        dist = self.to_prev if self.is_failing and not to_next else self.to_next
        if dist is None: return True # was never seen before/after
        if threshold is None: return False # must never be seen, but was seen
        return threshold <= dist

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, usage=usage, required_args=[],
                          optional_args=['source_repo'], defaults=default_args)
    out = get_formatter(b, opts)

    tags = opts.get_list('project', default=b.tags)
    repo = Repo(opts.source_repo)
    forward = False # TODOXXX Make this an option.
    window_size = None # TODOXXX Make this an option.

    testruns_map, hexsha_lens = index_source_commits(b, tags)

    # TODOXXX Merge with code in +diff_commits:
    # (0) Build summary_fields across the specified history.
    summary_fields = set()
    summary_vals = {}
    for commit, testruns in \
        iter_history(b, repo, testruns_map, hexsha_lens,
                     forward=True, branch=opts.branch):
        for testrun in testruns:
            find_summary_fields(testrun, summary_fields, summary_vals)
            
    # for displaying testruns:
    header_fields = list(summary_fields - {'source_branch', 'version'})

    # trim summary fields identical in all testruns
    for field in set(summary_fields):
        if summary_vals[field] is not None:
            summary_fields.discard(field)
    # TODOXXX End merge with code in +diff_commits.
    # summary_fields, all_fields = get_summary_fields(iter_history(b, repo, testruns_map, hexsha_lens, forward=True, branch=opts.branch))
    # TODOXXX Output summary_fields.

    # XXX purely for progress
    num_pairs = 0
    for commit, testruns, next_commit, next_testruns in \
        iter_adjacent(b, repo, testruns_map, hexsha_lens,
                      forward=True, branch=opts.branch):
        num_pairs += 1
    total_pairs = num_pairs
    if opts.restrict > 0:
        num_pairs = opts.restrict

    # (1) Index regressions in the specified history.
    # tc_key := name+subtest+outcome+baseline_outcome
    last_testruns = {}       # summary_key -> (commit_id, Testrun)
    last_valid = {}          # tc_key -> commit_id
    last_regression = {}     # tc_key -> (Regression, i)
    valid_regressions = {}   # commit_id -> tc_key -> Regression
    skipped_regressions = {} # commit_id -> n, just a statistic

    i, skip = 0, total_pairs - opts.restrict
    if opts.restrict <= 0: skip = 0
    num_seen, num_kept = 0, 0
    progress = None
    for commit, testruns, next_commit, next_testruns in \
        iter_adjacent(b, repo, testruns_map, hexsha_lens,
                      forward=True, branch=opts.branch):
        if skip > 0:
            # if opts.restrict > 0, analyze the *last* N commits:
            skip -= 1
            continue
        if progress is None:
            progress = tqdm.tqdm(iterable=None, desc='Finding regressions',
                                 total=num_pairs, leave=True, unit='commit')
            if profiler is not None: profiler.enable()
        if opts.restrict > 0 and i > opts.restrict:
            break
        num_added, num_removed = 0, 0

        #print("DEBUG find regressions in", next_commit.hexsha[:7], next_commit.summary, file=sys.stderr)

        # (1a) choose testruns to compare
        best_overall, comparisons, alt_comparisons = \
            pick_comparisons(summary_fields, testruns, next_testruns)
        for summary_key, comparison in alt_comparisons.items():
            baseline, latest = comparison
            if summary_key in last_testruns:
                # XXX prefer an older testrun for the same configuration
                _commit_id, prior_baseline = last_testruns[summary_key]
                comparisons[summary_key] = (prior_baseline, latest)
            else:
                comparisons[summary_key] = (baseline, latest)

        # (1b) update last_testruns, last_valid, last_regression, valid_regressions
        for summary_key, comparison in comparisons.items():
            baseline, latest = comparison
            #print("DEBUG diffing", baseline, latest)
            baseline, latest = b.testrun(baseline), b.testrun(latest)
            last_testruns[summary_key] = (next_commit.hexsha, latest)
            diff = diff_testruns(baseline, latest, key=opts.key)
            for tc in diff.testcases:
                # TODO: Combine similar changes e.g. FAIL->PASS vs KFAIL->PASS.
                # TODO: Filter trivial regressions e.g. FAIL->KFAIL.
                tc_key = get_tc_key(tc)
                #print("DEBUG evaluating", tc_key, file=sys.stderr)

                # check distance to last occurrence of same regression
                regression, delta, num_flakes = Regression(tc), None, 0
                passing_flake, failing_flake = False, False
                if tc_key in last_regression:
                    assert tc_key in last_valid # XXX at least one must remain valid
                    valid_commit_id, valid_i = last_valid[tc_key]
                    prev_regression, last_i = last_regression[tc_key]
                    assert valid_commit_id in valid_regressions \
                        and tc_key in valid_regressions[valid_commit_id] # XXX must store valid
                    valid_regression = valid_regressions[valid_commit_id][tc_key]

                    # update prev_regression; if separation is too small, delete:
                    # - next_regression if valid_regression failing (keep first fail)
                    # - valid_regression if valid_regression is passing (keep last pass)
                    delta = i - last_i
                    prev_regression.to_next = delta
                    if not prev_regression.separation_over(window_size, to_next=True):
                        # gap between prev_regression and next_regression is too small
                        num_flakes = prev_regression.num_flakes + 1
                        failing_flake = regression.is_failing
                        passing_flake = not failing_flake
                    else:
                        # XXX next_regression can be reported separately
                        pass
                    if passing_flake:
                        del_map2(valid_regressions, valid_commit_id, tc_key)
                        incr_map(skipped_regressions, valid_commit_id)
                        num_removed += 1
                    elif failing_flake:
                        # XXX below, don't mark next_regression as valid
                        pass
                regression.to_prev = delta
                # regression.to_next = None
                regression.num_flakes = num_flakes

                num_added += 1
                last_regression[tc_key] = (regression, i)
                if failing_flake:
                    # don't mark next_regression as valid
                    incr_map(skipped_regressions, next_commit.hexsha)
                    num_removed += 1
                else:
                    last_valid[tc_key] = (next_commit.hexsha, i)
                    assign_map2(valid_regressions, next_commit.hexsha, tc_key, regression)

        # basic diagnostic of how the report size will turn out
        num_seen = num_seen + num_added
        num_kept = num_kept + num_added - num_removed
        if num_added > 0 or num_removed > 0:
            print("DEBUG {}->{} added {} and removed {} regressions, summary size {}/{}".format(commit.hexsha, next_commit.hexsha, num_added, num_removed, num_kept, num_seen), file=sys.stderr)
            if profiler is not None:
                profiler.disable()
                s = io.StringIO()
                ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
                ps.print_stats(10)
                print(s.getvalue(), file=sys.stderr)
                profiler.enable()

        progress.update(n=1)
        i += 1

    if progress is not None:
        progress.close()
    if profiler is not None:
        profiler.disable()

    # (2) Display regressions over specified threshold.
    for commit in repo.iter_commits(opts.branch, forward=forward):
        if commit.hexsha not in skipped_regressions:
            skipped_regressions[commit.hexsha] = 0
        if commit.hexsha not in valid_regressions \
           and skipped_regressions[commit.hexsha] == 0:
            continue

        # TODOXXX Turn into generic out.commit_header() method?
        info = dict()
        # TODOXXX Shorten commit_id automatically, rename to source_commit
        info['commit_id'] = commit.hexsha[:7]+'...'
        info['summary'] = commit.summary
        out.section(minor=True)
        out.message(commit_id=info['commit_id'],
                    summary=info['summary'])

        iter = []
        if commit.hexsha in valid_regressions:
            iter = valid_regressions[commit.hexsha].items()
        for tc_key, regression in iter:
            # identify change_kind, distinguish quiet testcases
            _regression, i = last_valid[tc_key]
            if regression.is_failing:
                change_kind = 'failing'
            elif window_size is not None and num_pairs - i < window_size:
                change_kind = 'recently_fixed'
            else:
                change_kind = 'fixed'
            
            # TODOXXX: in HTML, colour table row based on change_kind
            out.show_testcase(None, regression.tc, header_fields=['change_kind'],
                              change_kind=change_kind, num_flakes=regression.num_flakes,
                              separation=regression.separation)
        if skipped_regressions[commit.hexsha] > 0:
            out.message("{} changes skipped because of similarity to other changes" \
                        .format(skipped_regressions[commit.hexsha]))

    out.finish()
