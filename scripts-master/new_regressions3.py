#!/usr/bin/env python3
# WIP -- Walk the history of the specified branch (default master) of the Git
# repo source_repo. For each commit in the branch, diff testruns under
# specified project with testruns for the parent commits. Report all
# regressions that did not already appear within the prior
# novelty_threshold (default infinity) commits. More precisely, report
# the first occurrence of a failed change ('first failed') and the
# last occurrence of a fixed change ('last fixed').
usage = "+new_regressions [[key=]<glob>] [[source_repo=]<path>] [branch=<name>] [project=<tags>] [novelty_threshold=<num>] [sort=[least_]recent] [show_both_ends=no|yes] [diff_earlier=yes|no] [diff_baseline=no|yes] [cached_data=<path>] [rebuild_cache=no|yes]..."
default_args = {'project':None,         # restrict to testruns under <tags>
                'key':None,             # restrict to testcases matching <glob>
                'source_repo':None,     # scan commits from source_repo
                'branch':'master',      # scan commits in branch <name>
                # TODO: accept 'infinity' as a value for novelty_threshold
                'novelty_threshold':-1, # distance at which to merge changes (-1 denotes infinity)
                'sort':'recent',        # sort by date of commit
                'show_both_ends':False, # also consider 'last failed' and 'first fixed'
                # XXX note that diff_earlier takes precedence over diff_baseline
                'diff_earlier':True,    # diff against earlier commit if same configuration is missing
                'diff_baseline':False,  # diff against probable baseline if same configuration is missing
                'cached_data':None,     # <path> of JSON cached data from prior runs
                'rebuild_cache':False,  # compute each diff even if already present in cache
                'verbose':False,        # report progress of 'training' (# testcases added vs. merged)
                # TODO: add options restrict, restrict_training
                # TODO: add option pretty=yes/html
                # TODO: add option profile ??
               }

# TODOXXX Some stats on how much information is filtered out:
# - 216 commits from GDB project, novelty_threshold=infinity, summary ? changes of ? total
# - 216 commits from GDB project, novelty_threshold=50, summary 8539 changes of 37784 total (filter out 77.4%) over 1h15min
# - TODOXXX ? commits from SystemTap project, novelty_threshold=infinity, summary ? changes of ? total
# - ? commits from SystemTap project, novelty_threshold=50, summary ? changes of ? total

import sys
import os
import bunsen
from git import Repo

import tqdm

from common.format_output import get_formatter
from list_commits import index_source_commits, iter_testruns, iter_adjacent
from diff_runs import fail_outcomes
from diff_commits import get_tc_key, strip_tc, index_summary_fields, summary_tuple, get_comparison, diff_all_testruns

# TODO: Similar to make_comparison_str in diff_commits
def comparison_key(comparison):
    s = "{}->{}".format(comparison['baseline_summary_tuple'],comparison['summary_tuple'])
    if 'minus_baseline_summary_tuple' in comparison:
        assert('minus_sumary_tuple' in comparison)
        s += "-{}->{}".format(comparison['minus_baseline_summary_tuple'],comparison['minus_summary_tuple'])
    return s

def load_full_runs(b, testruns):
    full_testruns = []
    for testrun in testruns:
        full_testruns.append(b.testrun(testrun))
    return full_testruns

class Change:
    def __init__(self, tc, commit_pair, first_commit_pair=None):
        self.tc = strip_tc(tc)
        self.commit_pair = commit_pair
        self.first_commit_pair = first_commit_pair
        if self.first_commit_pair is None:
            self.first_commit_pair = self.commit_pair
        self.num_merged = 1

        # XXX pair (start, end) set by ChangeSet _cache_change
        self.commit_nos = None

        # XXX not used for single changes
        self.comparisons = set() # set of index into ChangeSet all_comparisons

    def copy(self):
        other = Change(self.tc, self.commit_pair, self.first_commit_pair)
        other.num_merged = self.num_merged
        other.commit_nos = self.commit_nos
        other.comparisons = self.comparisons
        return other

    @property
    def tc_key(self):
        return get_tc_key(self.tc)

    @property
    def is_single(self):
        return self.commit_pair == self.first_commit_pair

    @property
    def is_interesting(self):
        # XXX only consider changes passing<->failing
        if self.tc['baseline_outcome'] in fail_outcomes \
           and self.tc['outcome'] in fail_outcomes:
            return False
        elif self.tc['baseline_outcome'] not in fail_outcomes \
             and self.tc['outcome'] not in fail_outcomes:
            return False
        return True

    @property
    def is_failing(self):
        return self.tc['baseline_outcome'] not in fail_outcomes \
            and self.tc['outcome'] in fail_outcomes

    def signed_dist(self, other_change):
        assert(self.commit_nos is not None \
               and other_change.commit_nos is not None)
        self_start, self_end = self.commit_nos
        other_start, other_end = other_change.commit_nos
        if self_end <= other_start:
            return other_start - self_end # positive
        elif other_end <= self_start:
            return other_end - self_start # negative
        else:
            return 0 # there is overlap

    def dist(self, other_change):
        return abs(self.signed_dist(other_change))

    @property
    def span(self):
        assert(self.commit_nos is not None)
        start, end = self.commit_nos
        return end - start + 1

    def merge(self, other_change, comparison_ix=None):
        assert(self.commit_nos is not None \
               and (other_change.commit_nos is not None \
                    or comparison_ix is not None))
        self_start, self_end = self.commit_nos
        other_start, other_end = other_change.commit_nos
        if other_start < self_start:
            self_start = other_start
            self.first_commit_pair = other_change.first_commit_pair
        if other_end > self_end:
            self_end = other_end
            self.commit_pair = other_change.commit_pair
        self.commit_nos = self_start, self_end
        self.num_merged += other_change.num_merged
        self.comparisons.update(other_change.comparisons)
        if comparison_ix is not None:
            # XXX used when merging a single_change
            self.comparisons.add(comparison_ix)

class ChangeSet:
    def __init__(self, cachefile=None, novelty_threshold=None):
        self.novelty_threshold = novelty_threshold
        if self.novelty_threshold < 0: # XXX infinity
            self.novelty_threshold = None

        # XXX +new_regressions can be run with different key= and
        # novelty_threshold= arguments. To allow recomputation of this
        # data, we store single_changes and associate them to
        # particular commit_pairs in a compact fashion,
        # then recompute combined Change objects after loading the cache
        # (by invoking merge_changes for each commit_pair in order).
        #
        # Having the cachefile lets us avoid the main time sink of
        # computing +diff_runs for every commit pair.
        # XXX commit_pair ::= commit_id, prev_commit_id
        # XXX testcase_key ::= name+subtest+outcome_pair
        self.all_changes = []       # list of single_change, cached
        self.single_change_map = {} # maps testcase_key -> index in all_changes, computed
        self.all_comparisons = []   # list of comparison, cached
        self.comparison_map = {}    # maps comparison_key -> index in all_comparisons, computed
        self.known_keys = {}        # map commit_pair -> list of key, cached
        self.known_diffs = {}       # map commit_pair -> list of (index in all_changes, index in all_comparisons), cached
        self.known_commits = set()  # set of commit_id, computed

        if cachefile is not None and os.path.isfile(cachefile):
            input_file = open(cachefile, 'r')
            json_data = json.loads(cachefile.read().decode('utf8'))
            input_file.close()

            pass # TODO parse JSON, use _cache_change, handle opts.rebuild_cache option

        self.merged_changes = []      # list of Change, computed
        self.num_skipped_changes = {} # maps commit_id -> int, computed
        self.changes_starting = {}    # maps commit_id -> set of indices in merged_changes, computed
        self.changes_ending = {}      # maps commit_id -> set of indices in merged_changes, computed

        # XXX enabled only during build_merged_changes iteration
        self._commit_no = None     # index of current commit in sequence, used for distance calculation
        self.recent_changes = None # maps testcase_key -> index in merged_changes, computed

        # XXX stats, enabled during build_merged_changes iteration
        self.num_kept, self.num_seen = None, None    # totals across all commit_pairs
        self.num_added, self.num_merged = None, None # totals across one commit_pair
        self.max_commit_no = 0                       # use for calculating recency in final display

    def has_key(self, commit_pair, key):
        if key is None: key = '*'
        if commit_pair not in self.known_keys:
            self.known_keys[commit_pair] = []
        return key in self.known_keys[commit_pair] or '*' in self.known_keys[commit_pair]

    def add_key(self, commit_pair, key):
        if commit_pair not in self.known_keys:
            self.known_keys[commit_pair] = []
        if '*' not in self.known_keys[commit_pair]:
            self.known_keys[commit_pair].append(key)

    def _cache_change(self, single_change, comparison_ix):
        assert(single_change.is_single)
        if single_change.tc_key not in self.single_change_map:
            ix = len(self.all_changes)
            # XXX could/should clear commit_pair here as it is not
            # valid when the single_changes is referenced by other
            # commits, but _merge_changes will overwrite/fix it anyways
            self.all_changes.append(single_change)
            self.single_change_map[single_change.tc_key] = ix
        else:
            ix = self.single_change_map[single_change.tc_key]
        if single_change.commit_pair not in self.known_diffs:
            self.known_diffs[single_change.commit_pair] = []
        self.known_diffs[single_change.commit_pair].append((ix,comparison_ix))

    # XXX call in forward order for all commit_pairs across a branch!
    def _merge_changes(self, commit_pair):
        if commit_pair not in self.known_diffs:
            return # no cached changes
        for ix, comparison_ix in self.known_diffs[commit_pair]:
            sc = self.all_changes[ix].copy()
            comparison = self.all_comparisons[comparison_ix]
            assert(sc.is_single)
            sc.commit_pair, sc.first_commit_pair = commit_pair, commit_pair
            # TODO: perhaps exclude cached changes from the add_change() stats update?
            self.add_change(sc, comparison, already_cached=True)

    def build_merged_changes(self, b, repo, testruns_map, hexsha_lens, branch='master'):
        self._commit_no = 0
        self.recent_changes = {}
        self.num_kept, self.num_seen = 0, 0
        for commit, testruns, next_commit, next_testruns in \
            iter_adjacent(b, repo, testruns_map, hexsha_lens,
                          forward=True, branch=branch):
            self.num_added, self.num_merged = 0, 0
            # XXX first, merge already_cached changes
            self._merge_changes((next_commit.hexsha,commit.hexsha))
            # XXX next, allow new changes to be added
            yield commit, testruns, next_commit, next_testruns
            self.known_commits.add(next_commit.hexsha)
            self._commit_no += 1
        if self._commit_no > self.max_commit_no:
            self.max_commit_no = self._commit_no
        self._commit_no = None
        self.recent_changes = None
        self.num_kept, self.num_seen = None, None
        self.num_added, self.num_merged = None, None

    # XXX updates changes_starting, changes_ending
    def _remove_bounds(self, change, change_ix):
        start_commit, end_commit = change.first_commit_pair[1], change.commit_pair[1]
        if start_commit in self.changes_starting:
            self.changes_starting[start_commit].discard(change_ix)
        if end_commit in self.changes_ending:
            self.changes_ending[end_commit].discard(change_ix)

    # XXX updates changes_starting, changes_ending
    def _add_bounds(self, change, change_ix):
        start_commit, end_commit = change.first_commit_pair[1], change.commit_pair[1]
        if start_commit not in self.changes_starting:
            self.changes_starting[start_commit] = set()
        self.changes_starting[start_commit].add(change_ix)
        if end_commit not in self.changes_ending:
            self.changes_ending[end_commit] = set()
        self.changes_ending[end_commit].add(change_ix)

    # XXX call within the context of build_merged_changes!
    def add_change(self, single_change, comparison=None, already_cached=False):
        assert(single_change.is_single)
        assert(self._commit_no is not None \
               and self.recent_changes is not None)

        # add comparison to all_comparisons
        # TODO: when building JSON, move this to a separate method
        comparison_ix = None
        if comparison is not None:
            ck = comparison_key(comparison)
            # TODO: here and elsewhere comparison_ix = find_or_add_ix(self.all_comparisons, self.comparison_map, ck, comparison)
            if ck not in self.comparison_map:
                comparison_ix = len(self.all_comparisons)
                self.all_comparisons.append(comparison)
                self.comparison_map[ck] = comparison_ix
            else:
                comparison_ix = self.comparison_map[ck]

        # add single_change to all_changes
        if not already_cached:
            self._cache_change(single_change, comparison_ix)
        if single_change.commit_nos is None:
            single_change.commit_nos = (self._commit_no, self._commit_no)

        # update merged_changes, changes_starting, changes_ending
        prev_change, prev_change_ix = None, None
        if single_change.tc_key in self.recent_changes:
            prev_change_ix = self.recent_changes[single_change.tc_key]
            prev_change = self.merged_changes[prev_change_ix]
        if prev_change is None or \
           (self.novelty_threshold is not None \
            and prev_change.dist(single_change) > self.novelty_threshold):
            next_change_ix = len(self.merged_changes)
            next_change = single_change.copy()
            self.merged_changes.append(next_change)
            self.recent_changes[single_change.tc_key] = next_change_ix
            self._add_bounds(next_change, next_change_ix)
            # update stats
            self.num_added += 1
            self.num_kept += 1
        else:
            self._remove_bounds(prev_change, prev_change_ix)
            prev_change.merge(single_change, comparison_ix)
            self._add_bounds(prev_change, prev_change_ix)
            # update stats
            self.num_merged += 1
        self.num_seen += 1

    def significant_changes(self, commit_id, show_both_ends=False):
        change_list = []
        if commit_id in self.changes_starting:
            for ix in self.changes_starting[commit_id]:
                change = self.merged_changes[ix]
                if show_both_ends or change.is_failing:
                    # report 'first failed'
                    change_list.append(change)
        if commit_id in self.changes_ending:
            for ix in self.changes_ending[commit_id]:
                change = self.merged_changes[ix]
                if show_both_ends or not change.is_failing:
                    # report 'last fixed'
                    change_list.append(change)
        return change_list

    def skipped_changes(self, commit_id):
        return self.num_skipped_changes[commit_id]

    def get_age(self, change):
        _start_commit_no, end_commit_no = change.commit_nos
        assert(end_commit_no <= self.max_commit_no)
        return self.max_commit_no-end_commit_no

    def save_data(self, cachefile):
        pass # TODO produce JSON

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, usage=usage, required_args=[],
                          optional_args=['key', 'source_repo'], defaults=default_args)
    out = get_formatter(b, opts)

    tags = opts.get_list('project', default=b.tags)
    repo = Repo(opts.source_repo)
    forward = True if opts.sort == 'least_recent' else False
    required_key = '*' if opts.key is None else opts.key

    # (0) Restore cached data:
    cs = ChangeSet(opts.cached_data, novelty_threshold=opts.novelty_threshold)

    # (1) Index regressions in the specified history:
    testruns_map, hexsha_lens = index_source_commits(b, tags)
    header_fields, summary_fields = \
        index_summary_fields(iter_testruns(b, repo, testruns_map, hexsha_lens,
                                          forward=True, branch=opts.branch))

    # XXX count commit pairs for progress bar
    num_pairs = 0
    for commit, testruns, next_commit, next_testruns in \
        iter_adjacent(b, repo, testruns_map, hexsha_lens,
                      forward=True, branch=opts.branch):
        commit_pair = (next_commit.hexsha, commit.hexsha)
        if not opts.rebuild_cache \
           and next_commit.hexsha in cs.known_commits \
           and cs.has_key(commit_pair, opts.key):
            continue
        num_pairs += 1
    total_pairs = num_pairs
    # TODO: implement options to restrict analysis
    progress = None

    # Store the most recent testrun for each configuration:
    recent_testruns = {} if opts.diff_earlier else None # summary_key -> testrun
    for commit, testruns, next_commit, next_testruns in \
        cs.build_merged_changes(b, repo, testruns_map, hexsha_lens, branch=opts.branch):
        if progress is None:
            progress = tqdm.tqdm(iterable=None, desc='Finding regressions',
                                 total=num_pairs, leave=True, unit='commit')
        commit_pair = (next_commit.hexsha, commit.hexsha)
        if not opts.rebuild_cache \
           and next_commit.hexsha in cs.known_commits \
           and cs.has_key(commit_pair, opts.key):
            continue

        # TODO: b.testrun() should have some limited caching to avoid redundancy here
        testruns = load_full_runs(b, testruns)
        next_testruns = load_full_runs(b, next_testruns)

        diffs = diff_all_testruns(testruns, next_testruns, summary_fields,
                                  diff_previous=recent_testruns,
                                  diff_baseline=opts.diff_baseline,
                                  key=opts.key)
        if recent_testruns is not None:
            # update recent_testruns from testruns
            for testrun in testruns:
                t = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
                recent_testruns[t] = testrun # XXX overwrite earlier run with the same configuration

        for diff in diffs:
            comparison = get_comparison(diff)
            for tc in diff.testcases:
                single_change = Change(tc, commit_pair)
                if not single_change.is_interesting:
                    continue
                cs.add_change(single_change, comparison)
        cs.add_key(commit_pair, required_key)

        progress.update(n=1)
        if opts.verbose:
            print("added {} and merged {} changes, summary size {}/{}" \
                  .format(cs.num_added, cs.num_merged, cs.num_kept, cs.num_seen),
                  file=sys.stderr)
            print("- latest {} {}\n- baseline {} {}" \
                  .format(next_commit.hexsha[:7], next_commit.summary,
                          commit.hexsha[:7], commit.summary),
                  file=sys.stderr)

    if progress is not None:
        progress.close()

    # (2) Display regressions over specified novelty_threshold:
    for commit in repo.iter_commits(opts.branch, forward=forward):
        change_list = cs.significant_changes(commit.hexsha)
        if len(change_list) == 0 and cs.skipped_changes(commit.hexsha) == 0:
            continue

        info = dict()
        info['commit_id'] = commit.hexsha[:7]+'...'
        info['summary'] = commit.summary
        out.section(minor=True)
        out.message(commit_id=info['commit_id'],
                    summary=info['summary'])

        for change in change_list:
            if opts.key is not None and not fnmatchcase(change.tc['name'], key): continue
            # TODOXXX show <baseline_outcome>-><outcome> <num_merged>times <name> <subtest> <change_kind>={failing,recently_fixed,fixed} + details:
            # - first occurs in: <commit_id> <summary>
            # - last occurs in: <commit_id> <summary>
            # - across: <dist> commits
            # - seen in comparisons: <comparisons>
            # where <change_kind> in {failing,recently_fixed,fixed}
            # TODOXXX compute dist using change.commit_nos / change.span()
            # TODOXXX compute recently_fixed using cs.get_age(change)
            out.show_testcase(None, change.tc, num_merged="{} times".format(regression.num_merged))
            # TODO: optionally, provide more detail on when / how many times each comparison was seen
            # TODO: identify corresponding failing/fixed change
            # TODO: colorize depending on change_kind
            # TODO: colorize depending on whether last occurrence is fix or fail
        if cs.skipped_changes(commit.hexsha) > 0:
            out.message("{} changes skipped as similar to other changes" \
                        .format(cs.skipped_changes(commit.hexsha)))

    out.finish()

    # (3) Save cached data:
    cs.save_data(opts.cached_data)
