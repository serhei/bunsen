#!/usr/bin/env python3
info="""Display testcase results within a specified version range.
Based on a script by Martin Cermak."""
from bunsen import Bunsen, BunsenOptions
if __name__=='__main__': # XXX need a graceful solution for option conflicts
    BunsenOptions.add_option('source_repo', group='source_repo',
                             cmdline='source-repo', default=None,
                             help_str="Use project commit history from Git repo <path>",
                             help_cookie="<path>")
    BunsenOptions.add_option('branch', group='source_repo', default=None,
                             help_str="Use project commit history from <branch> in source_repo",
                             help_cookie="<branch>")
    BunsenOptions.add_option('gitweb_url', group='source_repo',
                             cmdline='gitweb-url', default=None,
                             help_str="Link to gitweb at <url>",
                             help_cookie="<url>")
    BunsenOptions.add_option('project', group='filtering', default=None,
                             help_str="Restrict the analysis to testruns in <projects>",
                             help_cookie="<projects>")
    BunsenOptions.add_option('key', group='filtering', default=None,
                             help_str="Restrict the analysis to testcases containing <glob>",
                             help_cookie="<glob>")
    # XXX Note on behaviour: If earliest..latest are refspecs, show a range of git commits.
    # If earliest..latest are package_nvrs, show a range of package_nvr versions.
    BunsenOptions.add_option('baseline', group='commit_range', default=None,
                             help_str="Baseline versions (or globs or version ranges A..B) against which to compare testcase results",
                             help_cookie="<refspecs_or_versions_or_ranges>")
    BunsenOptions.add_option('earliest', group='commit_range', default=None,
                             help_str="Earliest commit or version for which to display testcase results (defaults to baseline if baseline is provided unambiguously)",
                             help_cookie="<refspec_or_version>")
    BunsenOptions.add_option('latest', group='commit_range', default=None,
                             help_str="Latest commit or version for which to display testcase results",
                             help_cookie="<refspec_or_version>")
    BunsenOptions.add_option('versions', group='commit_range', default=None,
                             help_str="List of additional versions, globs, or version ranges A..B for which to display testcase results (in addition to, or instead of earliest..latest)",
                             help_cookie="<refspecs_or_versions_or_ranges>")
    BunsenOptions.add_option('version_field', group='commit_range', default='package_nvr',
                             help_str="Fields to use for specifying range of versions (e.g. package_nvr, package_ver, kernel_ver, ...)")
    # TODO: Control relative sorting of commits and versions,
    # e.g. <commits after tag release-N>,
    # <downstream-package-version-N>, <tag release-N>. Probably
    # something to leave until after the redesign or kluge
    # specifically for SystemTap.
    BunsenOptions.add_option('show_subtests', group='display',
                             cmdline='show-subtests', boolean=True, default=False,
                             help_str="Show subtest details (increases output size significantly)")
# XXX No option 'pretty' or 'output_format' -- for now, always output HTML.

import sys
import git
import git.exc
from bunsen.utils import warn_print
from list_versions import index_testrun_versions, iter_history
from diff_runs import append_map, fail_outcomes, untested_outcomes
from diff_commits import index_summary_fields, get_summary, get_summary_key, get_tc_key
from fnmatch import fnmatchcase

from common.format_output import get_formatter
import tqdm

def refspec_matches(repo, refspec, hexsha_prefix):
    if refspec.startswith(hexsha_prefix):
        return True
    if hexsha_prefix.startswith(refspec):
        return True
    try:
        return repo.commit(refspec).hexsha.startswith(hexsha_prefix)
    except git.exc.BadName:
        return False # no refspec

def _find_matching_key(repo, vkeys, vid):
    for vkey in vkeys:
        if refspec_matches(repo, vkey, vid): # refspec match
            return vkey
        if fnmatchcase(vid, vkey): # glob match
            return vkey
    return None

# Used by Timecube _scan_gather_versions:
def _open_range(repo, vid, range_starts, range_ends):
    range_start = _find_matching_key(repo, range_starts, vid)
    if range_start is not None:
        range_ends.add(range_starts[range_start])

# Used by Timecube _scan_gather_versions:
def _close_range(repo, vid, range_ends):
    range_end = _find_matching_key(repo, range_ends, vid)
    if range_end is not None:
        range_ends.remove(range_end)

# Used by Timecube class to reference original testrun of each testcase:
class TestcaseRef:
    # testcase -> Testcase
    # testrun -> Testrun
    # tc_key
    # summary_key
    pass

# XXX sketch for LPC presentation slides - comment out otherwise
# class Timecube:
#     def __init__(self,...):
#         self.outcomes_grid = {} # grid_key -> PASS,FAIL
#         self.subtests_grid = {} # grid_key -> set of (name,outcome,subtest)
#         self.prev_tested = {} # grid_key -> grid_key for prev tested version
#         self.next_tested = {} # grid_key -> grid_key for next tested version 
#     def grid_key(self, testcase, configuration, commit):
#         """Returns string ID of the specified grid cell."""
#     def scan_versions(self):
#         """Populate the grid."""
#     def iter_scan_versions(self):
#         """Populate the grid while yielding (version_id, commit, testruns)."""
#     def iter_commits(self):
#         """Yield (commit, testruns) in chronological order."""
#     def iter_testcases(self):
#         """Yield testcase names."""
#     def commit_dist(self, v1, v2):
#         """Returns distance in # of commits between v1 and v2."""

class Timecube:
    # XXX: needs to allow custom 'version' fields instead of 'commit'
    # XXX: needs docstrings explaining the following fields and all methods
    #
    # XXX populated by __init__()
    # commit_range = [] # list of (version_id, commit, testruns); TODO change to version_range
    # commit_indices = {} # hexsha -> index in commit_range (for finding distance between commits)
    # all_testruns = [] # list of testruns
    # n_branch_commits = 0 # total commits in branch, >len(self.commit_range) if using commits only
    #
    # XXX everything below populated by scan_versions()/iter_scan_versions()
    # testcase_names = set() # set(str) or list(str) in alphabetical order
    #
    # summary_key :: string ID of a configuration, computed by get_summary_key()
    # testcase_configurations = {} # testcase_name -> set of summary_key
    # configurations = {} # summary_key -> configuration_summary dict, computed by get_summary()
    #
    # grid_key :: string ID of a grid cell, "testcase_name+summary_key+hexsha"
    # tc_key :: string ID of a testcase, "name+outcome+subtest"
    # outcomes_grid = {} # grid_key -> outcome {PASS,FAIL} only
    # subtests_grid = {} # testcase_name+summary_key+hexsha -> list of TestcaseRef
    # subtests_grid1 = {} # testcase_name+summary_key+hexsha -> set of tc_key, computed by get_tc_key()
    #
    # XXX tables for differential scan of 'adjacent' results (skipping empty grid cells)
    # prev_tested = {} # grid_key -> grid_key for previous test results for this configuration
    # next_tested = {} # grid_key -> grid_key for next test results for this configuration
    # commits_grid = {} # grid_key -> commit (for finding distance between grid keys)
    #
    # untested_commits = set() # hexshas of commits with no testruns
    # untested_testcases = set() # set of testcase_names with no test results
    # unchanged_testcases = set() # set of testcase_names with no changes in # fails seen
    # unchanged_max_fails = {} # testcase_name -> max # of fails seen
    # unchanged_n_configs = {} # testcase_name -> # of configurations seen

    def __init__(self, bunsen, opts, repo):
        self._bunsen = bunsen
        self._opts = opts
        self._repo = repo

        # Collect all testruns between the specified commits:
        self.commit_range = [] # list of (version_id, commit_or_None, testruns)
        self.commit_indices = {} # hexsha -> index in commit_range (for finding distance between commits)
        self.all_testruns = [] # list of testruns, in no particular order
        self.baseline_versions = [] # list of version_id to use for baseline comparison

        # XXX opts.earliest is assumed already populated from opts.baseline if necessary
        # Collect commit ranges within baseline, earliest..latest, versions:
        self._range_start = {} # maps earliest -> latest or list of latest
        self._baseline_range_start = {} # maps earliest -> latest or list of latest
        self._single_versions = set() # set of glob
        self._baseline_single_versions = set() # set of glob
        for item in opts.baseline:
            self._add_version_item(item, baseline=True)
        for item in opts.versions:
            self._add_version_item(item)
        if opts.earliest is not None and opts.latest is not None:
            self._add_version_item(opts.earliest, opts.latest)

        self.n_branch_commits = 0 # XXX total commits in branch, >len(self.commit_range)
        self._scan_gather_versions()
        # TODO warn if collected range of versions is empty or some refspecs were not found

        # To be populated by scan_versions()/iter_scan_versions():
        self.testcase_names = set() # set(str) or list(str) in alphabetical order

        # summary_key :: string ID of a configuration, computed by get_summary_key()
        self.testcase_configurations = {} # testcase_name -> set of summary_key
        self.configurations = {} # summary_key -> configuration_summary dict, computed by get_summary()

        # grid_key :: string ID of a grid cell, "testcase_name+summary_key+hexsha"
        # tc_key :: string ID of a testcase, "name+outcome+subtest"
        self.outcomes_grid = {} # grid_key -> outcome {PASS,FAIL} only
        self.subtests_grid = {} # testcase_name+summary_key+hexsha -> list of TestcaseRef
        self.subtests_grid1 = {} # testcase_name+summary_key+hexsha -> set of tc_key, computed by get_tc_key()

        # XXX tables for differential scan of 'adjacent' results (skipping empty grid cells)
        self.prev_tested = {} # grid_key -> grid_key for previous test results for this configuration
        self.next_tested = {} # grid_key -> grid_key for next test results for this configuration
        self.commits_grid = {} # grid_key -> commit (for finding distance between grid keys)

        self.untested_commits = set() # hexshas of commits with no testruns
        self.untested_testcases = set() # set of testcase_names with no test results
        self.unchanged_testcases = set() # set of testcase_names with no changes in # fails seen
        self.unchanged_max_fails = {} # testcase_name -> max # of fails seen
        self.unchanged_n_configs = {} # testcase_name -> # of configurations seen

    # Mark glob or range of versions for _scan_gather_versions()
    def _add_version_item(self, item1, item2=None, baseline=False):
        # TODO change to handle multiple items:
        # if item2 is not None and baseline:
        #     append_map(self._baseline_range_start, item1, item2)
        if item2 is not None and baseline:
            self._baseline_range_start[item1] = item2
            return
        if item2 is not None:
            self._range_start[item1] = item2
        if '..' in item1: # range A..B
            items = item1.split('..')
            self._add_version_item(items[0], items[-1])
            return
        if baseline:
            self._baseline_single_versions.add(item1)
            return
        self._single_versions.add(item1)

    # XXX Call in forward chronological order of versions:
    def _add_version(self, version_id, commit, testruns, baseline=False):
        self.commit_indices[version_id] = len(self.commit_range)
        self.commit_range.append((version_id, commit, testruns))
        self.all_testruns += testruns
        if baseline:
            self.baseline_versions.append(version_id)

    # XXX Call once after building self._{baseline_,}range_start, self._{baseline_,}single_versions
    def _scan_gather_versions(self):
        # TODO: remove projects, tvix from caller?
        projects = opts.get_list('project', default=self._bunsen.projects)
        tvix = index_testrun_versions(self._bunsen, projects) # TODO check package_nvr
        repo = self._repo
        # XXX self.n_branch_commits assumed to be 0
        active_range_ends = set()
        baseline_range_ends = set()
        #print("DEBUG requests", self._range_start, self._single_versions, file=sys.stderr)
        for version_id, commit, testruns in iter_history(self._bunsen, repo, tvix,
                                                         forward=True, branch=opts.branch,
                                                         include_empty_versions=True,
                                                         include_downstream_versions=True):
            #print("DEBUG checking", version_id, commit, len(testruns), file=sys.stderr)
            vid = version_id
            if commit is not None:
                vid = commit.hexsha
            self.n_branch_commits += 1
            _open_range(repo, vid, self._range_start, active_range_ends)
            _open_range(repo, vid, self._baseline_range_start, baseline_range_ends)
            single_version = _find_matching_key(repo, self._single_versions, vid)
            baseline_single_version = _find_matching_key(repo, self._baseline_single_versions, vid)
            if len(active_range_ends) > 0 or len(baseline_range_ends) > 0 \
               or single_version is not None or baseline_single_version is not None:
                is_baseline = len(baseline_range_ends) > 0 or baseline_single_version
                self._add_version(version_id, commit, testruns, is_baseline)
            _close_range(repo, vid, active_range_ends)
            _close_range(repo, vid, baseline_range_ends)

    def row_key(self, testcase_name, summary_key):
        """Returns a string ID of the specified (testcase, configuration) grid row."""
        return(f'{testcase_name}+{summary_key}')

    def grid_key(self, testcase_name, summary_key, hexsha):
        """Returns the string ID of the specified (testcase, configuration, commit) grid cell."""
        return(f'{testcase_name}+{summary_key}+{hexsha}')

    def scan_versions(self):
        """Scan the detailed testcase data for all commits in the range and use it to populate the grid."""
        for _version_id, _commit, _testruns in self.iter_scan_versions():
            pass

    def _merge_outcome(self, gk, outcome):
        if outcome in untested_outcomes:
            return
        if outcome in fail_outcomes:
            self.outcomes_grid[gk] = 'FAIL'
        if gk not in self.outcomes_grid:
            self.outcomes_grid[gk] = 'PASS'

    def _scan_testrun(self, version_id, commit, testrun, summary_fields):
        summary = get_summary(testrun, summary_fields)
        sk = get_summary_key(summary)

        # populate self.configurations
        if sk not in self.configurations:
            self.configurations[sk] = summary

        testrun = self._bunsen.full_testrun(testrun, raise_error=False) # XXX should remove this & have Testrun load on-demand
        if testrun is None: return None
        tc_names = set() # XXX testcase names for this testrun only
        for testcase in testrun.testcases:
            if self._opts.key is not None and \
               not fnmatchcase(testcase.name, '*'+opts.key+'*'): # XXX change glob to 'contains' in other scripts
                continue
            tc_names.add(testcase.name)

            # populate self.testcase_names, self.testcase_configurations
            if testcase.name not in self.testcase_names:
                self.testcase_names.add(testcase.name)
            if testcase.name not in self.testcase_configurations:
                self.testcase_configurations[testcase.name] = set()
            self.testcase_configurations[testcase.name].add(sk)

            # populate self.outcomes_grid, self.subtests_grid1, self.subtests_grid
            gk = self.grid_key(testcase.name, sk, version_id)
            tk = get_tc_key(testcase) # XXX should exclude baseline_outcome
            self._merge_outcome(gk, testcase.outcome) # populates outcomes_grid
            if gk not in self.subtests_grid1:
                self.subtests_grid1[gk] = set()
            self.subtests_grid1[gk].add(tk)
            tc_ref = TestcaseRef()
            tc_ref.testcase = testcase
            tc_ref.testrun = testrun
            tc_ref.tc_key = tk
            tc_ref.summary_key = sk
            append_map(self.subtests_grid, gk, tc_ref)
            # XXX need to check against gdb repo with separate pass-subtest storage

        # populate self.prev_tested, self.next_tested, self.commits_grid
        for testcase_name in tc_names:
            gk = self.grid_key(testcase_name, sk, version_id)
            self.commits_grid[gk] = commit

            rowk = self.row_key(testcase_name, sk) # grid_key minus version
            if rowk in self._last_tested:
                prev_gk = self._last_tested[rowk]
                self.prev_tested[gk] = prev_gk
                self.next_tested[prev_gk] = gk
            self._last_tested[rowk] = gk # XXX only once per testcase_name!

    def iter_scan_versions(self):
        """Scan the detailed testcase data for all commits in the range and use it to populate the grid.

        Yields (version_id, commit, testruns) in chronological order while the scan is ongoing."""
        header_fields, summary_fields = index_summary_fields(self.all_testruns) # XXX redundant with calling script
        self._last_tested = {} # testcase_name+summary_key -> grid_key with a previous result for this testcase
        for version_id, commit, testruns in self.commit_range:
            if not testruns:
                self.untested_commits.add(version_id)
            for testrun in testruns:
                self._scan_testrun(version_id, commit, testrun, summary_fields)
            yield version_id, commit, testruns

        # populate self.untested_testcases, self.unchanged_{testcases,max_fails,n_configs}
        testcase_state = {} # grid_key minus version -> # of fails expected
        # XXX since results don't change, calculation of n_configs is simple
        # however, a calculation on all testcases for ranking is more complex
        for testcase_name in self.testcase_names:
            is_unchanged, is_untested = True, True
            failed_configs = set()
            for sk in self.testcase_configurations[testcase_name]:
                rowk = self.row_key(testcase_name, sk) # grid_key minus version
                for version_id, commit, _testruns in self.commit_range:
                    gk = self.grid_key(testcase_name, sk, version_id)
                    if gk not in self.outcomes_grid:
                        continue
                    is_untested = False
                    n_fails = 0
                    if self.outcomes_grid[gk] == 'FAIL' and gk in self.subtests_grid1:
                        n_fails = len(self.subtests_grid1[gk])
                        if testcase_name not in self.unchanged_max_fails or \
                           n_fails > self.unchanged_max_fails[testcase_name]:
                            self.unchanged_max_fails[testcase_name] = n_fails
                        failed_configs.add(sk)
                    if rowk not in testcase_state:
                        testcase_state[rowk] = n_fails
                    elif testcase_state[rowk] != n_fails:
                        is_unchanged = False
            if is_unchanged:
                self.unchanged_testcases.add(testcase_name)
                self.unchanged_n_configs[testcase_name] = len(failed_configs)
            if is_untested:
                self.untested_testcases.add(testcase_name)

        self.testcase_names = list(self.testcase_names)
        self.testcase_names.sort()

    def iter_versions(self, reverse=False):
        """Yields (version_id, commit or None, testruns) in chronological order."""
        if reverse:
            for version_id, commit, testruns in reversed(self.commit_range):
                yield version_id, commit, testruns
        else:
            for version_id, commit, testruns in self.commit_range:
                yield version_id, commit, testruns

    def iter_testcases(self):
        """Yields testcase_name for all testcases."""
        for tc_name in self.testcase_names:
            yield tc_name

    def iter_configurations(self, testcase_name=None):
        """Yields configuration summary_keys for testcase_name (for all testcases if None)."""
        if testcase_name is None:
            configurations = set()
            for testcase_name in self.testcase_names:
                for sk in self.iter_configurations(testcase_name):
                    configurations.add(sk)
        else:
            configurations = self.testcase_configurations[testcase_name]
        for sk in configurations:
            yield sk

    def subtest_counts(self, gk):
        """Returns a map subtest -> # of occurrences in grid entry gk."""
        counts = {}
        if gk in self.subtests_grid:
            for tc_ref in self.subtests_grid[gk]:
                testcase = tc_ref.testcase
                if 'subtest' not in testcase:
                    continue
                if testcase.subtest not in counts:
                    counts[testcase.subtest] = 0
                counts[testcase.subtest] += 1
        return counts

    def commit_dist(self, baseline, latest):
         """Returns distance in number of commits between baseline and latest."""
         return self.commit_indices[latest] - self.commit_indices[baseline]

    def grid_dist(self, gk_baseline, gk_latest):
        """Returns distance in number of commits between grid cells gk_baseline and gk_latest."""
        baseline = self.commits_grid[gk_baseline].hexsha
        latest = self.commits_grid[gk_latest].hexsha
        return self.commit_dist(baseline,latest)

    # TODO docstring
    def has_regression(self, testcase_name, sk):
        first_outcome = None
        last_outcome = None
        for version_id, commit, _testruns in self.iter_versions(reverse=True):
            gk = self.grid_key(testcase_name, sk, version_id)
            if gk in self.outcomes_grid:
                last_outcome = self.outcomes_grid[gk]
                break
        for version_id, commit, _testruns in self.iter_versions():
            gk = self.grid_key(testcase_name, sk, version_id)
            if gk in self.outcomes_grid:
                first_outcome = self.outcomes_grid[gk]
                break
        return first_outcome == "PASS" and last_outcome == "FAIL"

    @classmethod
    def check_version_range(cls, opts):
        """Normalize version range selection options in opts.

        These are options 'baseline', 'earliest', 'latest', 'versions'
        whose values define the version range of a TimeCube.
        """
        opts.baseline = opts.get_list('baseline', default=[])
        opts.versions = opts.get_list('versions', default=[])
        if opts.earliest is None and opts.baseline is not None and len(opts.baseline) == 1:
            opts.earliest = opts.baseline[0]
        if opts.latest is not None and opts.earliest is None:
            # TODO: Alternately, default to earliest=1st-version, latest=last-version, print a warning.
            raise BunsenError("No commit range, latest is specified but {baseline|earliest} is not, or baseline is ambiguous.")

if __name__=='__main__':
    b, opts = Bunsen.from_cmdline(info=info,
                                  #required_args=['baseline','latest'],
                                  optional_args=['baseline','latest','source_repo'])

    opts.pretty = 'html' # XXX for now, always output HTML
    out = get_formatter(b, opts)

    projects = opts.get_list('project', default=b.projects)
    assert opts.source_repo is not None # XXX git.Repo(None) defaults to cwd, which is not what we want
    repo = git.Repo(opts.source_repo)
    Timecube.check_version_range(opts)

    # (1a) Use Timecube class to collect test results for commits in the specified range
    cube = Timecube(b, opts, repo)

    # (1b) Find summary fields present in all testruns
    header_fields, summary_fields = index_summary_fields(cube.all_testruns)
    # XXX summary_fields will also include source_commit, version
    # which are not used in get_summary. header_fields excludes these.

    # (1c) Scan the Timecube to collect the testcases for all commits in the range
    progress = tqdm.tqdm(iterable=None, desc='Scanning commits',
                         total=len(cube.commit_range), leave=True, unit='commit')
    for version_id, commit, testruns in cube.iter_scan_versions():
        progress.update(n=1)

    # (2) Show a grid of test results for every testcase in the specified commit range
    progress = tqdm.tqdm(iterable=None, desc='Rendering grid',
                         total=len(cube.testcase_names), leave=True, unit='testcase')
    n_testcases_shown = 0
    n_headings_shown = 0
    for testcase_name in cube.iter_testcases():
        # XXX skip unchanged testcases without making a section
        if testcase_name in cube.untested_testcases or testcase_name in cube.unchanged_testcases:
            # continue
            pass

        n_headings_shown += 1
        out.section()
        out.message(f"<a id=test\"{n_headings_shown}\">", raw=True) # XXX HTML ONLY; a name?
        out.message(f"<h3>{n_headings_shown}. {testcase_name}</h3>", raw=True) # XXX HTML ONLY
        out.message(f"</a>", raw=True) # XXX HTML ONLY

        # XXX skip unchanged testcases while still including the section
        # XXX comment out 'continue' to verify results
        if testcase_name in cube.untested_testcases:
            out.message("no test results over specified version range")
            continue
        elif testcase_name in cube.unchanged_testcases:
            msg = "no failure count changes over specified version range"
            if testcase_name in cube.unchanged_max_fails:
                # XXX HTML only
                msg += "<br/>" + f"(failures occur in up to {cube.unchanged_max_fails[testcase_name]} subtests on up to {cube.unchanged_n_configs[testcase_name]} configurations)"
            out.message(msg)
            # XXX probably more helpful to see this on downstream data
            #continue

        for sk in cube.iter_configurations(testcase_name):
            summary = cube.configurations[sk]
            # XXX HTML table should default to showing columns in order added
            # XXX for glanceability, show first and last commits on the left
            field_order = ['last','first'] + list(header_fields)
            for version_id, commit, _testruns in cube.iter_versions(reverse=True):
                if commit is not None:
                    hexsha = commit.hexsha[:7]
                    field_order.append(hexsha)
                else:
                    field_order.append(version_id) # package_nvr
                if commit is not None and opts.gitweb_url is not None:
                    commitdiff_url = opts.gitweb_url + ";a=commitdiff;h={}" \
                        .format(commit.hexsha)
                    out.table.header_href[hexsha] = commitdiff_url # XXX HACK
                if commit is not None:
                    out.table.header_tooltip[hexsha] = out.sanitize(hexsha+' '+commit.summary) # XXX HACK

            out.table_row(summary, order=field_order)
            first_val = "?" # <- will be the value in 'first' column
            first_tooltip = None
            # TODO display baselines instead of (first,last)
            last_val = "?" # <- will be the value in 'last' column
            last_tooltip = None
            for version_id, commit, _testruns in cube.iter_versions(reverse=True):
                gk = cube.grid_key(testcase_name, sk, version_id)
                if commit is not None:
                    hexsha = commit.hexsha[:7]
                    summary = commit.summary
                else:
                    hexsha = version_id # package_nvr
                    summary = "release"

                subtest_counts = cube.subtest_counts(gk)
                details = None
                if opts.show_subtests:
                    details = ""
                    need_br = False
                    for subtest, num in subtest_counts.items():
                        if need_br: details += "<br/>" # XXX HTML only
                        if num > 1:
                            details += f"{num}x {subtest}"
                        else:
                            details += subtest
                        need_br = True

                if gk not in cube.outcomes_grid:
                    out.table_cell(hexsha, '?') # XXX will be blanked out by the stylesheet
                elif cube.outcomes_grid[gk] == 'PASS':
                    out.table_cell(hexsha, "+")
                    first_val, first_tooltip = "+", hexsha+" "+summary
                    if last_val == "?": last_val, last_tooltip = "+", hexsha+" "+summary
                elif cube.outcomes_grid[gk] == 'FAIL':
                    out.table_cell(hexsha, f"-{len(subtest_counts)}", details=details) # XXX mark number of fails
                    #out.table_cell(hexsha, "-")
                    first_val, first_tooltip = "-", hexsha+" "+summary
                    if last_val == "?": last_val, last_tooltip = "-", hexsha+" "+summary
                else:
                    warn_print(f"BUG: unsure what to do with outcomes_grid[\"{gk}\"]")
                    out.table_cell(hexsha, 'BUG')

            out.table_cell('last', last_val)
            if last_tooltip: out.table.header_tooltip['last'] = last_tooltip
            out.table_cell('first', first_val)
            if first_tooltip: out.table.header_tooltip['first'] = first_tooltip
            # TODO add gitweb link to first, last headers

        n_testcases_shown += 1
        progress.update(n=1)

    out.section()
    out.message(f"showing {n_testcases_shown} testcases out of {len(cube.testcase_names)} total")
    branch_name = "main branch" if opts.branch is None else "branch " + opts.branch
    out.message(f"showing {len(cube.commit_range)} versions out of {cube.n_branch_commits} total for {branch_name}")
