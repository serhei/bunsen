#!/usr/bin/env python3
info="""Display the changes in specified testcases within a specified version range."""
from bunsen import Bunsen, BunsenOptions
BunsenOptions.add_option('source_repo', group='source_repo',
                         cmdline='source-repo', default=None,
                         help_str="Use project commit history from Git repo <path>",
                         help_cookie="<path>")
BunsenOptions.add_option('branch', group='source_repo', default=None,
                         help_str="Use project commit history from <branch> in source_repo",
                         help_cookie="<branch>")
BunsenOptions.add_option('project', group='filtering', default=None,
                         help_str="Restrict the analysis to testruns in <projects>",
                         help_cookie="<projects>")
BunsenOptions.add_option('key', group='filtering', default=None,
                         help_str="Restrict the analysis to testcases containing <glob>",
                         help_cookie="<glob>")
BunsenOptions.add_option('baseline', group='commit_range',
                         help_str="First commit for which to display testcase results",
                         help_cookie="<refspec>")
BunsenOptions.add_option('latest', group='commit_range',
                         help_str="Last commit for which to display testcase results",
                         help_cookie="<refspec>")
BunsenOptions.add_option('split_testcases', group='display', boolean=True, default=False,
                         cmdline='split-testcases',
                         help_str="Display a separate history for each testcase")
BunsenOptions.add_option('threshold', group='filtering', default=3, help_cookie="<num>") # TODO help_str
# TODO option 'threshold' controls filtering; threshold=0 -> show all changes, threshold=infty -> show only latest
# XXX No option 'pretty' or 'output_format' -- for now, always output HTML.

import git
from show_testcases import Timecube
from diff_runs import append_map
from diff_commits import index_summary_fields

from common.format_output import get_formatter
import tqdm

# Identifies a single change within a ChangeSet:
class Change:
    # baseline, latest = INIT/PASS/FAIL/FLAKE
    # baseline_gk, latest_gk # individual changes only
    # configurations = set() # set of summary_key
    # individual = True/False # is this a combination of changes on different configurations?
    # changes = [] # grouped changes only -- list of Change objects this was combined from
    pass

# Identifies a subset of changes filtered from the data in a Timecube:
class ChangeSet:
    # XXX: needs to allow custom 'version' fields instead of 'commit'
    # XXX: needs docstrings explaining the following fields and all methods
    #
    # gk_slice :: grid_key minus commit
    # _last_exact = {}  # gk_slice -> latest INIT/PASS/FAIL
    # _last_gk = {}     # gk_slice -> gk of _last_exact
    # _start_flaky = {} # gk_slice -> gk at start of flaky run of _last_exact
    # _start_solid = {} # gk_slice -> gk at start of consecutive run of _last_exact
    # _len_solid = {}   # gk_slice -> number of consecutive _last_exact values seen
    # changes = {}     # gk -> list of Change

    def __init__(self, cube, threshold=None):
        self.cube = cube

        self.threshold = threshold
        if self.threshold < 0: # threshold=infty -> report last run only
            self.threshold = None

        self._last_exact = {}  # gk_slice -> latest INIT/PASS/FAIL
        self._last_gk = {}     # gk_slice -> gk of _last_exact
        self._start_flaky = {} # gk_slice -> gk at start of flaky run of _last_exact
        self._start_solid = {} # gk_slice -> gk at start of consecutive run of _last_exact
        self._len_solid = {}   # gk_slice -> number of consecutive _last_exact values seen
        self._baseline = {}    # gk_slice -> INIT/PASS/FAIL/FLAKE before start_flaky
        self.changes = {}      # gk -> list of Change

    def scan_commits(self):
        """Scan the grid data for all commits in the range and use it to populate the changeset."""
        for _commit, _testruns in self.iter_scan_commits():
            pass

    def _add_change(self, baseline, latest, start_run, _end_run, sk):
        change = Change()
        change.baseline = baseline
        change.latest = latest
        change.baseline_gk = None
        if start_run in self.cube.prev_tested:
            change.baseline_gk = self.cube.prev_tested[start_run]
        change.latest_gk = start_run
        change.configurations = {sk}
        change.individual = True

        append_map(self.changes, start_run, change)

    def _solid_run(self, gk_slice):
        return self.threshold is not None and self._len_solid[gk_slice] >= self.threshold

    # TODO: Ideally we would look at correlated changes across configurations.
    def _scan_grid_cell(self, testcase_name, sk, hexsha):
        gk_slice = f"{testcase_name}+{sk}"
        if gk_slice not in self._last_exact:
            self._last_exact[gk_slice] = 'INIT'

        gk = self.cube.grid_key(testcase_name, sk, hexsha)
        if gk not in self.cube.outcomes_grid:
            return # no test results in this cell
        next_exact = self.cube.outcomes_grid[gk]
        prev_exact = self._last_exact[gk_slice]
        prev_gk = None
        if gk_slice in self._last_gk:
            prev_gk = self._last_gk[gk_slice]

        # update _last_exact, _last_gk
        self._last_exact[gk_slice] = next_exact
        self._last_gk[gk_slice] = gk

        if next_exact == prev_exact:
            # extend the current solid and flaky runs
            # XXX untested testcases don't count for incrementing threshold!
            self._len_solid[gk_slice] += 1
        if next_exact != prev_exact and prev_exact != 'INIT' \
           and not self._solid_run(gk_slice):
            # solid run was broken before reaching threshold
            # open new solid run
            self._start_solid[gk_slice] = gk
            self._len_solid[gk_slice] = 0
        elif next_exact != prev_exact and prev_exact != 'INIT' \
             and self._solid_run(gk_slice):
            # solid run was broken after reaching threshold
            # close the current solid and flaky runs
            # - flaky run [start_flaky..start_solid-1]
            # - exact run [start_solid..prev_gk]
            baseline = self._baseline[gk_slice]
            start_flaky = self._start_flaky[gk_slice]
            start_solid = self._start_solid[gk_slice]
            end_flaky = None
            if start_solid in self.cube.prev_tested:
                end_flaky = self.cube.prev_tested[start_solid]
            end_solid = prev_gk
            if start_flaky != start_solid:
                self._add_change(baseline, 'FLAKE', start_flaky, end_flaky, sk)
                self._add_change('FLAKE', prev_exact, start_solid, end_solid, sk)
            else:
                self._add_change(baseline, prev_exact, start_solid, end_solid, sk)
        if next_exact != prev_exact:
            # open new solid and flaky runs
            self._baseline[gk_slice] = prev_exact
            self._start_flaky[gk_slice] = gk
            self._start_solid[gk_slice] = gk
            self._len_solid[gk_slice] = 0

    def _close_runs(self):
        for testcase_name in self.cube.iter_testcases():
            for sk in self.cube.testcase_configurations[testcase_name]:
                gk_slice = f"{testcase_name}+{sk}"
                if gk_slice not in self._last_exact:
                    continue # XXX no open run
                if gk_slice not in self._baseline:
                    continue
                if gk_slice not in self._start_solid:
                    continue
                baseline = self._baseline[gk_slice]
                start_solid = self._start_solid[gk_slice]
                if start_solid in self.cube.prev_tested:
                    baseline_gk = self.cube.prev_tested[start_solid]
                    baseline = self.cube.outcomes_grid[baseline_gk]
                latest = self._last_exact[gk_slice]
                if self._solid_run(gk_slice) or self.threshold is None:
                    # TODO: need to handle flaky run if self.threshold is not None
                    self._add_change(baseline, latest, start_solid, None, sk)
                else:
                    self._add_change(baseline, 'FLAKE', start_solid, None, sk)

    def iter_scan_commits(self):
        """Scan the grid data for all commits in the range and use it to populate the changeset.

        Yields (commit, testruns) in chronological order while the scan is ongoing."""
        for commit, _testruns in self.cube.iter_commits():
            for testcase_name in self.cube.iter_testcases():
                for sk in self.cube.testcase_configurations[testcase_name]:
                    self._scan_grid_cell(testcase_name, sk, commit.hexsha)
            yield commit, testruns
        self._close_runs()

    def grouped_changes(self, testcase_name, hexsha):
        """Group changes to testcase_name at commit into one Change object per change type (e.g. PASS->FAIL)."""
        change_groups = {} # change_type e.g. PASS->FAIL -> Change
        for sk in self.cube.testcase_configurations[testcase_name]:
            gk = self.cube.grid_key(testcase_name, sk, hexsha)
            if gk not in self.changes:
                continue
            for change in self.changes[gk]:
                change_type = f"{change.baseline}->{change.latest}"
                if change_type not in change_groups:
                    grouped_change = Change()
                    grouped_change.baseline = change.baseline
                    grouped_change.latest = change.latest
                    grouped_change.individual = False
                    grouped_change.configurations = set()
                    grouped_change.changes = []
                    change_groups[change_type] = grouped_change
                change_groups[change_type].configurations.update(change.configurations)
                change_groups[change_type].changes.append(change)
        changes = []
        for _change_type, change in change_groups.items():
            changes.append(change)
        return changes

def _show_change(out, header_fields, cs, commit, testcase_name, change,
                 include_testcase=False, include_commit=False):
    info = dict()
    field_order = ['baseline','latest']
    info['latest'] = change.latest
    info['baseline'] = change.baseline
    if include_testcase:
        field_order += ['testcase']
        info['testcase'] = out.sanitize(testcase_name)
    if include_commit:
        field_order += ['commit_id', 'summary']
        info['commit_id'] = out.sanitize(commit.hexsha[:7])
        info['summary'] = out.sanitize(commit.summary)
    field_order += ['info']
    info['info'] = f"seen on {len(change.configurations)} configurations"

    # TODO build a details view from configurations, change.{baseline,latest}_gk
    out.table_row(info, order=field_order, merge_header=True)

def show_changes(out, header_fields, cs, commit, testcase_name,
                 include_testcase=False, include_commit=False):
    changes = cs.grouped_changes(testcase_name, commit.hexsha)
    for change in changes:
        _show_change(out, header_fields, cs, commit, testcase_name, change, include_testcase, include_commit)

if __name__=='__main__':
    b, opts = Bunsen.from_cmdline(info=info,
                                  required_args=['baseline','latest'],
                                  optional_args=['source_repo'])

    opts.pretty = 'html' # XXX for now, always output HTML
    out = get_formatter(b, opts)

    projects = opts.get_list('project', default=b.projects)
    repo = git.Repo(opts.source_repo) 

    # (1a) Use Timecube class to collect test results for commits in the specified range
    cube = Timecube(b, opts, repo)

    # (1b) Find summary fields present in all testruns
    header_fields, summary_fields = index_summary_fields(cube.all_testruns)
    # XXX summary_fields will also include source_commit, version
    # which are not used in get_summary. header_fields excludes these.

    # (1c) Scan the Timecube to collect the testcases for all commits in the range
    progress = tqdm.tqdm(iterable=None, desc='Scanning commits',
                         total=len(cube.commit_range), leave=True, unit='commit')
    for commit, testruns in cube.iter_scan_commits():
        progress.update(n=1)

    # (2) Scan the Timecube to decide noteworthy changes
    cs = ChangeSet(cube, threshold=opts.threshold)
    progress = tqdm.tqdm(iterable=None, desc='Scanning changes',
                         total=len(cube.commit_range), leave=True, unit='commit')
    for commit, testruns in cs.iter_scan_commits():
        progress.update(n=1)

    # (3) Show a table of changes for every commit / for every testcase in the specified commit range
    if opts.split_testcases:
        progress = tqdm.tqdm(iterable=None, desc='Rendering changes',
                             total=len(cube.testcase_names), leave=True, unit='testcase')
        n_testcases_shown = 0
        for testcase_name in cube.iter_testcases():
            # XXX skip unchanged testcases without making a section
            if testcase_name in cube.untested_testcases or testcase_name in cube.unchanged_testcases:
                continue

            out.section()
            out.message(testcase_name)

            for commit, testruns in cube.iter_commits(reverse=True):
                show_changes(out, header_fields, cs, commit, testcase_name, include_commit=True)

            n_testcases_shown += 1
            progress.update(n=1)
    else:
        progress = tqdm.tqdm(iterable=None, desc='Rendering commits',
                             total=len(cube.commit_range), leave=True, unit='commit')
        n_testcases_shown = None
        for commit, testruns in cube.iter_commits(reverse=True):
            # XXX redundant code with list_commits.py
            info = dict()
            #info['commit_id'] = commit.hexsha[:7]+'...' # for compact=True
            info['commit_id'] = out.sanitize(commit.hexsha)
            info['summary'] = out.sanitize(commit.summary)
            if opts.pretty == 'html' and opts.gitweb_url is not None:
                commit_url = opts.gitweb_url + ";a=commit;h={}" \
                                 .format(commit.hexsha)
                commitdiff_url = opts.gitweb_url + ";a=commitdiff;h={}" \
                                     .format(commit.hexsha)
                gitweb_info = "<a href=\"{}\">commit</a>, ".format(commit_url) + \
                    "<a href=\"{}\">commitdiff</a>".format(commitdiff_url)
                info['gitweb_info'] = gitweb_info

            out.section()
            out.message(compact=False, sanitize=False, **info)

            n_shown = 0
            for testcase_name in cube.iter_testcases():
                if testcase_name in cube.untested_testcases or testcase_name in cube.unchanged_testcases:
                    continue
                show_changes(out, header_fields, cs, commit, testcase_name, include_testcase=True)
                n_shown += 1

            if n_testcases_shown is None:
                n_testcases_shown = n_shown
            progress.update(n=1)

    out.section()
    out.message(f"showing {n_testcases_shown} testcases out of {len(cube.testcase_names)} total")
    branch_name = "main branch" if opts.branch is None else "branch " + opts.branch
    out.message(f"showing {len(cube.commit_range)} commits out of {cube.n_branch_commits} total for {branch_name}")
