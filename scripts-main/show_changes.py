#!/usr/bin/env python3
info="""Display noteworthy changes in specified testcases within a specified version range."""
from bunsen import Bunsen, BunsenOptions
if __name__=='__main__': # XXX need a graceful solution for option conflicts; most are already defined in show_testcases
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
    BunsenOptions.add_option('baseline', group='version_range', default=None,
                             help_str="Baseline versions (or globs or version ranges A..B) against which to compare testcase results",
                             help_cookie="<refspecs_or_versions_or_ranges>")
    BunsenOptions.add_option('earliest', group='version_range', default=None,
                             help_str="Earliest commit or version for which to display testcase results (defaults to baseline if baseline is provided unambiguously)",
                             help_cookie="<refspec_or_version>")
    BunsenOptions.add_option('latest', group='version_range', default=None,
                             help_str="Latest commit or version for which to display testcase results",
                             help_cookie="<refspec_or_version>")
    BunsenOptions.add_option('versions', group='version_range', default=None,
                             help_str="List of additional versions, globs, or version ranges A..B for which to display testcase results (in addition to, or instead of earliest..latest)",
                             help_cookie="<refspecs_or_versions_or_ranges>")
    BunsenOptions.add_option('version_field', group='version_range', default='package_nvr',
                             help_str="Fields to use for specifying range of versions (e.g. package_nvr, package_ver, kernel_ver, ...)")
    # TODO: Control relative sorting of commits and versions,
    # e.g. <commits after tag release-N>,
    # <downstream-package-version-N>, <tag release-N>. Probably
    # something to leave until after the redesign or kluge
    # specifically for SystemTap.
    BunsenOptions.add_option('split_testcases', group='display', boolean=True, default=False,
                             cmdline='split-testcases',
                             help_str="Display a separate history for each testcase")
    BunsenOptions.add_option('threshold', group='filtering', default=3, help_cookie="<num>", help_str="Controls filtering, 0 -> show all, infty -> only last change")
    # XXX No option 'pretty' or 'output_format' -- for now, always output HTML.

import git
from show_testcases import Timecube
from common.parse_dejagnu import test_outcome_map
from diff_runs import append_map
from diff_commits import index_summary_fields

from bunsen.utils import warn_print
from common.format_output import get_formatter, html_sanitize
from list_versions import format_version_header
import tqdm

def prefix_len(s1, s2):
    """Length of common prefix of two strings."""
    l = -1
    for i in range(len(s1)):
        if i >= len(s2) or s1[i] != s2[i]:
            break
        l = i
    return (l+1)

def strip_outcome(subtest):
    """Remove the outcome prefix from a subtest string.

    This gives as 'subtest core' that should be more or less similar
    from testrun to testrun for subtests testing the 'same' thing.
    """
    for oc in test_outcome_map.keys():
        pref = oc+': '
        if subtest.startswith(pref):
            subtest = subtest[len(pref):]
    return subtest

class Change:
    """Identifies a single or grouped change within a ChangeSet."""
    # Fields for single or grouped changes:
    #
    # name                -> expname, identical for before/after
    # outcome_{pre,post}  -> in {None, PASS, FAIL, FLAKE}
    # subtest_{pre,post}  -> subtest string, usually similar for before/after
    # count_{pre,post}    -> number of times subtest string is duplicated
    # version_{pre,post}  -> versions immediately before and after the change
    # gk_{pre,post}, dist -> Timecube cells of version_{pre,post} and dist
    #
    # Fields for grouped changes:
    #
    # changes
    # -> list of single Change objects making up the current change
    # version_earlier
    # -> state before this Change extends from version_earlier
    # version_later
    # -> state created by this Change extends until version_later
    #
    # A Change can be displayed in the table as
    #
    #   *latest* <version_later> + <version_pre>
    #     <-(<dist> versions)-
    #   <version_post> + *earliest* <version_earlier>
    #
    # The *latest* and *earliest* are displayed inline when the
    # corresponding versions are earliest/latest in the entire
    # sequence.

    def __init__(self):
        self.dist = None
        self.changes = None
        self.version_earlier = None
        self.version_later = None
        # XXX outcome_{pre,post}, version_{pre,post}, subtest_post must be set

    @property
    def single(self):
        return self.changes is None

    # XXX plaintext 'dump' for debugging
    def dump(self):
        if self.single:
            print(f"** {self.name} {self.outcome_post}<-{self.outcome_pre} @{self.version_post}<-{self.version_pre}") # ... +gk {self.gk_post}<={self.gk_pre}")
            return
        print(f"DEBUG {self.name} {self.outcome_post}<-{self.outcome_pre}")
        print(f"*latest* {self.version_later} + {self.version_post}")
        print(f"    <-({self.dist} versions)-")
        print(f"{self.version_pre} + *earliest* {self.version_earlier}")
        print(f"* now {self.count_post}x {self.subtest_post}")
        print(f"* was {self.count_pre}x {self.subtest_pre}")
        for ch in self.changes: ch.dump()
        print("==")

class ChangeRun:
    """State machine processing a sequence of results into a set of Changes.

    It keeps track of 'solid' runs of the same test result and 'flaky'
    runs of frequently changing test results.
    """

    def __init__(self, cs, name, threshold):
        self._cs = cs
        self.name = name
        self.threshold = threshold

        self.last_outcome = None # -> last outcome seen in the sequence
        self.last_subtest = None # -> last subtest seen, or '<implicit>'
        self.last_count = None   # -> number of subtest dups for last outcome
        self.baseline_gk = None  # -> earliest extension of state pre 'flaky' run
        self.baseline_outcome = None # -> state pre 'flaky' run
        self.last_gk = None      # -> grid_key of last_outcome
        self.last_version = None # -> version of last_outcome
        self.start_flaky = None  # -> grid_key at start of open 'flaky' run
        self.start_solid = None  # -> grid_key at start of open 'solid' run
        self.len_solid = 0       # -> number of test results in open 'solid' run
        self.single_changes = [] # -> unused single Changes in chronological order

    @property
    def solid_run(self):
        return self.threshold is not None and self.len_solid >= self.threshold

    # Merge changes @[gk_start..gk_post_end)
    # e.g. for a flaky run
    #
    #   0123456789
    #   ---????+++
    #
    # we will call merge_changes(FAIL,FLAKE,3,7) => {3->4,4->5,5->6}, report @3
    # and merge_changes(FLAKE,PASS,7,9 or 10) => {6->7}, report @7
    def _merge_changes(self, last_outcome, next_outcome,
                       gk_start, gk_post_end, final=False):
        if gk_start == gk_post_end:
            return # empty interval

        # TODO skip this / fix the logic
        # XXX The final closing is treated as a solid run of baseline_outcome:
        # if final and next_outcome is None:
        #     next_outcome = self.last_outcome

        # Collect single_changes matching [gk_start..gk_post_end)
        p, q = None, None
        if gk_start is None: p = 0
        #print(f"\n=== collecting {next_outcome}<-{last_outcome} changes at {gk_start}<-{gk_post_end} ===\n") # DEBUG
        for i in range(len(self.single_changes)):
            ch = self.single_changes[i]
            #ch.dump() # DEBUG
            #print(ch.gk_post, "=>", ch.subtest_post) # DEBUG
            # XXX includes the initial change (gk_start-1)=>gk_start:
            if ch.gk_post == gk_start: p = i
            # XXX excludes the final change (gk_post_end-1)=>gk_post_end:
            if ch.gk_post == gk_post_end: q = i
        if q is None: q = len(self.single_changes)
        assert p is not None and q is not None # single_changes not collected properly
        changes = self.single_changes[p:q]
        if len(changes) == 0:
            return # empty interval
        self.single_changes = self.single_changes[:p] + self.single_changes[q:]

        # Create grouped Change:
        ch = Change()
        ch.name = self.name
        ch.outcome_pre, ch.outcome_post = last_outcome, next_outcome
        if last_outcome is None: ch.outcome_pre = changes[0].outcome_pre
        if next_outcome is None: ch.outcome_post = changes[-1].outcome_post
        ch.subtest_pre, ch.subtest_post = \
            changes[0].subtest_pre, changes[-1].subtest_post
        ch.count_pre, ch.count_post = \
            changes[0].count_pre, changes[-1].count_post
        ch.version_pre, ch.version_post = \
            changes[0].version_pre, changes[-1].version_post
        ch.gk_pre, ch.gk_post = changes[0].gk_pre, changes[-1].gk_post
        ch.dist = self._cs.cube.grid_dist(ch.gk_pre, ch.gk_post)

        ch.changes = changes
        ch.version_earlier = self._cs.cube.version_for_gk(self.baseline_gk)
        ch.version_later = self._cs.cube.version_for_gk(self.last_gk)
        # print("<pre>"); ch.dump(); print("</pre>") # DEBUG
        # print("<p>=> will file under", gk_start, "or", ch.gk_post, "</p>") # DEBUG

        # Changes are reported at gk_start, where the state transition occurred:
        if gk_start is not None:
            self._cs.add_change(gk_start, ch)
        else:
            # TODO is this an appropriate 'always' point?
            self._cs.add_change(ch.gk_post, ch)

    def extend(self, version_id, gk, outcome, subtest,
               count=1, implicit=False):
        # Create single Change:
        ch = Change()
        ch.name = self.name
        ch.outcome_pre, ch.outcome_post = self.last_outcome, outcome
        ch.subtest_pre, ch.subtest_post = self.last_subtest, \
            '<implicit>' if implicit else subtest
        ch.count_pre, ch.count_post = self.last_count, count
        ch.version_pre, ch.version_post = self.last_version, version_id
        ch.gk_pre, ch.gk_post = self.last_gk, gk
        ch.dist = self._cs.cube.grid_dist(ch.gk_pre, ch.gk_post)
        #ch.dump() # DEBUG
        assert ch.version_pre != ch.version_post # probable multiple invocation of extend() on the same version
        self.single_changes.append(ch)

        if self.baseline_gk is None:
            self.baseline_gk = gk

        # State machine: track 'solid' and 'flaky' runs since last change.
        if outcome == self.last_outcome:
            # extend the current solid and flaky runs
            # XXX only tested cases count for length of solid runs
            self.len_solid += 1
        if outcome != self.last_outcome and self.last_outcome != None \
           and not self.solid_run:
            # solid run was broken before reaching threshold,
            # open a new solid run, while the flaky run continues
            self.start_solid = gk
            self.len_solid = 0
        elif outcome != self.last_outcome and self.last_outcome != None \
             and self.solid_run:
            # solid run was broken after reaching threshold
            # close the current solid and flaky runs
            #self.close(gk=gk, outcome=outcome, final=False)
            self.close(gk=gk, final=False)

            # XXX save the previous solid run
            # to compute version_earlier of the next change:
            if self.start_solid is not None:
                self.baseline_gk = self.start_solid
                self.baseline_outcome = self.last_outcome
            # XXX else, prefer baseline_gk to be the first-seen gk

            # open new solid and flaky runs
            self.start_flaky = gk
            self.start_solid = gk
            self.len_solid = 0

        self.last_outcome, self.last_subtest, self.last_count = \
            outcome, subtest, count
        self.last_version, self.last_gk = \
            version_id, gk
        if implicit:
            self.last_subtest = '<implicit>'

    def close(self, gk=None, outcome=None, final=True):
        # close the remaining solid and flaky runs
        # - flaky run @[self.start_flaky..self.start_solid)
        # - solid run @[self.start_solid..gk)
        #
        # XXX calling close() at the end of scanning a ChangeSet
        # treats the final solid run as solid regardless of threshold
        if self.start_flaky == self.start_solid:
            #print("DEBUG single merge", self.start_solid, " => ", gk) # DEBUG
            #self._merge_changes(self.baseline_outcome, outcome,
            self._merge_changes(None, None,
                                self.start_solid, gk, final=final)
        else:
            #print("DEBUG dual merge", self.start_flaky, " => ", self.start_solid, " => ", gk) # DEBUG
            #self._merge_changes(self.baseline_outcome, 'FLAKE',
            self._merge_changes(None, 'FLAKE',
                                self.start_flaky, self.start_solid, final=final)
            #self._merge_changes('FLAKE', outcome,
            self._merge_changes('FLAKE', None,
                                self.start_solid, gk, final=final)

class ChangeSet:
    """Identifies a subset of changes filtered from the data in a Timecube."""
    def __init__(self, cube, threshold=None):
        self.cube = cube

        self.threshold = threshold
        if self.threshold < 0: # threshold=infy -> report final state only
            self.threshold = None

        self.grouped_changes_ending = {} # gk -> list of grouped Change

        # XXX Intermediate state for the iter_scan_versions() procedure
        self._open_runs = {}      # subk -> ChangeRun
        self._subkeys = {}        # maps (row_key, subtest) -> subk
        self._subkey_next = {}    # maps str -> next_id for disambiguation of subk
        self._subtest_counts = {} # maps row_key -> (subtest -> count)

    # XXX The nastiest part of the algorithm right now is this code to
    # match each 'new' subtest to the 'same' previous subtest, when
    # subtest strings are not the same. When we encounter a new
    # subtest, we assign it a 'subkey'. Then we try to assign the same
    # subkey to similar subtests in newer test results. This does not
    # need to be precise. The key criterion is to avoid paired
    # regressions along the lines of:
    #
    #   PASS <- FAIL subtest foo: answer is 42
    #   FAIL <- PASS subtest foo: answer is 43
    #
    # TODO Good testcases in stap: abort.exp, addr_op.exp, perf.exp
    # TODO Find good (suitably nightmarish) testcases in gdb.
    def _update_subkeys(self, rowk, prev_subtest_counts, new_subtest_counts):
        core_subkeys = {}
        for subtest, _count_pre in prev_subtest_counts.items():
            subtest_core = strip_outcome(subtest)
            # XXX (rowk, subtest) in self._subkeys ensured by prev _update_subkeys
            subk = self._subkeys[(rowk, subtest)]
            if subtest_core in core_subkeys:
                # TODO: Tackle nightmare edge case of identical cores
                # with different outcomes. There's a smart way to
                # match them, which we're not doing right now.
                #
                # Affected testcases in stap: plt.exp, sdt.exp, sdt_misc.exp, setjmp.exp, systemtap.pass1-4/semko.exp, systemtap.server/client.exp, systemtap.server/server_privilege.exp
                warn_print(f"BUG: Found otherwise identical subtest strings '{subtest_core}' with multiple outcomes in row '{rowk}', proceed with caution.")
            core_subkeys[subtest_core] = subk
        #print(rowk, "->", core_subkeys) # DEBUG
        for subtest, _count_post in new_subtest_counts.items():
            subtest_core = strip_outcome(subtest)
            subk = None
            # Use prev_subtest with longest matching prefix of core,
            # prefer an identical core whenever possible:
            if subtest_core in core_subkeys:
                subk = core_subkeys[subtest_core]
                #print(f"DEBUG: {rowk} -> exact match {subk}*for {subtest_core}")
                del core_subkeys[subtest_core]
                self._subkeys[(rowk, subtest)] = subk
                continue
            matching_core, subk, longest_match = None, None, 0
            for earlier_core, subk_cand in core_subkeys.items():
                # XXX heuristic to avoid aggressive merging of systemtap.examples/.../foo with systemtap.examples/.../bar
                if "systemtap.examples" in earlier_core:
                    continue

                match_len = prefix_len(subtest_core, earlier_core)
                if match_len > longest_match:
                    matching_core, subk, longest_match = \
                        earlier_core, subk_cand, match_len
            if subk is None:
                # No matching earlier subtest, generate a new subkey:
                if subtest_core not in self._subkey_next:
                    self._subkey_next[subtest_core] = 0
                subk = f"#{self._subkey_next[subtest_core]}::{subtest_core}"
                #subk = f"#{self._subkey_next}::{rowk}::{subtest_core}" # XXX works
                #subk = f"#{self._subkey_next[rowk]}::{subtest_core}" # XXX weird collision problems, debug
                #print(f"DEBUG: {rowk} -> new subk {subk}")
                self._subkey_next[subtest_core] += 1
                self._subkeys[(rowk, subtest)] = subk
                continue
            #print(f"DEBUG: {rowk} -> fuzzy match {subk}* {matching_core}*for {subtest_core}")
            del core_subkeys[matching_core]
            self._subkeys[(rowk, subtest)] = subk

    def _scan_grid_cell(self, testcase_name, version_id, sk):
        gk = self.cube.grid_key(testcase_name, sk, version_id)
        if gk not in self.cube.outcomes_grid:
            return # no test results -> no changes to ChangeSet
        outcome = self.cube.outcomes_grid[gk]

        rowk = self.cube.row_key(testcase_name, sk) # grid_key minus version
        prev_subtest_counts = {}
        if rowk in self._subtest_counts:
            prev_subtest_counts = self._subtest_counts[rowk]
        new_subtest_counts = self.cube.subtest_counts(gk)
        #print("DEBUG will scan new_subtest_counts", new_subtest_counts, "on", sk)
        self._update_subkeys(rowk, prev_subtest_counts, new_subtest_counts)
        #for foo,bar in self._subkeys.items():
        #    if not bar.endswith("(0 - 0)\n"): continue
        #    print("DEBUG ::",foo,"->",bar)
        self._subtest_counts[rowk] = new_subtest_counts

        # Any subtests that appear are reported to the ChangeRun.
        seen_subk = set()
        for subtest, count_post in new_subtest_counts.items():
            # XXX (rowk, subtest) in subk guaranteed by _update_subkeys
            subk = self._subkeys[(rowk, subtest)]
            if subk not in self._open_runs:
                self._open_runs[subk] = ChangeRun(self, testcase_name, self.threshold)
            #print("DEBUG appearing subk",subk,"on sk",sk)
            self._open_runs[subk].extend(version_id, gk, outcome, subtest,
                                         count_post)
            seen_subk.add(subk)

        # XXX Any subtests that disappear are reported to the
        # ChangeRun as implicit-PASS with count_post=1.
        #
        # This is probably even valid on data that stores PASS subtests
        # separately (the disappearance of a PASS is a PASS->PASS change),
        # TODO although the cosmetics of reporting this will need work.
        for subtest, _count_pre in prev_subtest_counts.items():
            subk = self._subkeys[(rowk, subtest)]
            if subk in seen_subk: continue
            # XXX subk in self._open_runs guaranteed
            #print("DEBUG disappearing subk",subk,"on sk", sk)
            self._open_runs[subk].extend(version_id, gk, 'PASS', subtest,
                                         implicit=True)

    def _close_final_runs(self):
        for subk, cr in self._open_runs.items():
            cr.close()

        # XXX Reset intermediate state
        self._open_runs = {}
        self._subkeys = {}
        self._subkey_counts = {}
        self._subtest_counts = {}

    def iter_scan_versions(self):
        """Scan the Timecube's version range to populate the ChangeSet.

        Yields (version_id, commit, testruns) in chronological order
        while the scan is ongoing.
        """
        for version_id, commit, testruns in self.cube.iter_versions():
            #print(f"DEBUG: === SCAN VERSION {version_id} ===")
            for testcase_name in self.cube.iter_testcases():
                for sk in self.cube.testcase_configurations[testcase_name]:
                    #print(f"= SUBSCAN row_key from {testcase_name} and {sk}")
                    self._scan_grid_cell(testcase_name, version_id, sk)
            yield version_id, commit, testruns
        self._close_final_runs()

    def scan_versions(self):
        """Scan the Timecube's version range to populate the ChangeSet."""
        for _version_id, _commit, _testruns in self.iter_scan_versions():
            pass

    def add_change(self, gk, ch):
        """Add a grouped change at the specified grid_key."""
        append_map(self.grouped_changes_ending, gk, ch)

    # def grouped_changes(self, testcase_name, version_id):
    #     """Return the set of grouped changes for testcase_name at version_id."""
    #     changes = []
    #     for sk in self.cube.testcase_configurations[testcase_name]:
    #         gk = self.cube.grid_key(testcase_name, sk, version_id)
    #         if gk in self.grouped_changes_ending:
    #             changes += self.grouped_changes_ending[gk]
    #     return changes

def _show_version_id(opts, version_id):
    return "<tt>" + html_sanitize(version_id) + "</tt>"
    pass # TODO also truncate hexshas, include gitweb link

def _show_change(out, opts, header_fields, cs,
                 testcase_name, sk, version_id, ch,
                 include_testcase=False, include_version=False):
    # (1) outcome before and after
    info = dict()
    field_order = ['post','pre']
    info['pre'] = ch.outcome_pre
    info['post'] = ch.outcome_post

    # (2) versions spanned by this change
    version_info = ""
    if ch.version_later is not None and ch.version_later != ch.version_post:
        version_info += "<b>latest</b> " + _show_version_id(opts, ch.version_later)
        version_info += " + "
    version_info += _show_version_id(opts, ch.version_post)
    version_info += " <-" # TODO unicode left arrow
    if ch.dist is not None and ch.dist > 1:
        version_info += "(<i>" + str(ch.dist) + " versions</i>)-"
    if ch.version_pre is not None:
        version_info += " " + _show_version_id(opts, ch.version_pre)
    if ch.version_earlier is not None and ch.version_earlier != ch.version_pre:
        if ch.version_pre is not None:
            version_info += " +"
        version_info += " <b>earliest</b>"
        if ch.version_earlier != ch.version_post:
            version_info += " " + _show_version_id(opts, ch.version_earlier)
    field_order += ['versions']
    info['versions'] = version_info

    # (3) configuration fields
    if sk in cs.cube.configurations:
        config = cs.cube.configurations[sk]
        for field in header_fields:
            if field in config:
                field_order += [field]
                info[field] = config[field]

    # (4) testcase + subtest
    if include_testcase:
        field_order += ['name']
        info['name'] = testcase_name
    field_order += ['subtest']
    if ch.subtest_post is not None and ch.subtest_post != "<implicit>":
        info['subtest'] = html_sanitize(ch.subtest_post)
    else:
        # show initial state, struck through
        info['subtest'] = "<s>" + html_sanitize(ch.subtest_pre) + "</s>"

    # (5) detailed change history in details view
    # TODO: ugly and redundant for checking purposes, will simplify
    details = ""
    details += "<p><b>initial:</b> "
    details += html_sanitize(ch.subtest_pre) + " at " + _show_version_id(opts, ch.version_pre) + "</p>"
    for single_ch in ch.changes:
        # details += "<p><b>pre:</b> " # TODO: redundant
        # details += html_sanitize(single_ch.subtest_pre) + "</p>"
        details += f"<p><i>change:</i> {single_ch.outcome_post}<-{single_ch.outcome_pre} @{single_ch.version_post}<-{single_ch.version_pre}"
        details += "</p>"
        details += "<p><b>post:</b> "
        details += html_sanitize(single_ch.subtest_post) + " at " + _show_version_id(opts, single_ch.version_post) + "</p>"
    # details += "<p><b>final: </b> " # TODO: redundant
    # details += html_sanitize(ch.subtest_post) + "</p>"

    out.table_row(info, details=details, order=field_order, merge_header=True)

def show_changes(out, opts, header_fields, cs, testcase_name, version_id,
                 include_testcase=False, include_version=False):
    for sk in cs.cube.testcase_configurations[testcase_name]:
        gk = cs.cube.grid_key(testcase_name, sk, version_id)
        if gk in cs.grouped_changes_ending:
            for change in cs.grouped_changes_ending[gk]:
                _show_change(out, opts, header_fields, cs,
                             testcase_name, sk, version_id, change,
                             include_testcase, include_version)

if __name__=='__main__':
    b, opts = Bunsen.from_cmdline(info=info,
                                  #required_args=['baseline','latest'],
                                  optional_args=['baseline', 'latest', 'source_repo'])

    opts.pretty = 'html' # XXX for now, always output HTML
    out = get_formatter(b, opts)

    projects = opts.get_list('project', default=b.projects)
    assert opts.source_repo is not None # XXX git.Repo(None) defaults to cwd, which is not what we want
    repo = git.Repo(opts.source_repo)
    Timecube.check_version_range(opts)

    # (1a) Use Timecube class to collect test results for versions in the specified range
    cube = Timecube(b, opts, repo)

    # (1b) Find summary fields present in all testruns
    header_fields, summary_fields = index_summary_fields(cube.all_testruns)
    # XXX summary_fields will also include source_commit, version
    # which are not used in get_summary. header_fields excludes these.

    # (1c) Scan the Timecube to collect the testcases for all versions in the range
    progress = tqdm.tqdm(iterable=None, desc='Scanning versions',
                         total=len(cube.version_range), leave=True, unit='version')
    for _version_id, _commit, _testruns in cube.iter_scan_versions():
        progress.update(n=1)

    # (2) Scan the Timecube to decide noteworthy changes:
    cs = ChangeSet(cube, threshold=opts.threshold)
    progress = tqdm.tqdm(iterable=None, desc='Scanning changes',
                         total=len(cube.version_range), leave=True, unit='version')
    for _version_id, _commit, _testruns in cs.iter_scan_versions():
        progress.update(n=1)

    # (3) Show a table of changes for every testcase / every version in the range
    if opts.split_testcases:
        progress = tqdm.tqdm(iterable=None, desc='Rendering changes',
                             total=len(cube.testcase_names), leave=True, unit='testcase')
        n_testcases_shown = 0

        for testcase_name in cube.iter_testcases():
            # XXX skip unchanged testcases without making a section
            if testcase_name in cube.untested_testcases \
               or testcase_name in cube.unchanged_testcases:
                continue

            out.section()
            out.message(testcase_name)

            for version_id, commit, testruns in cube.iter_versions(reverse=True):
                show_changes(out, opts, header_fields,
                             cs, testcase_name, version_id,
                             include_version=True)

            n_testcases_shown += 1
            progress.update(n=1)

    else: # list changes by version
        progress = tqdm.tqdm(iterable=None, desc='Rendering commits',
                             total=len(cube.version_range), leave=True, unit='commit')
        n_testcases_shown = None
        for version_id, commit, testruns in cube.iter_versions(reverse=True):
            # XXX redundant code with list_versions.py
            format_version_header(out, opts, version_id, commit)

            n_shown_here = 0
            for testcase_name in cube.iter_testcases():
                if testcase_name in cube.untested_testcases \
                   or testcase_name in cube.unchanged_testcases:
                    continue
                show_changes(out, opts, header_fields,
                             cs, testcase_name, version_id,
                             include_testcase=True)
                n_shown_here += 1

            if n_testcases_shown is None or n_shown_here > n_testcases_shown:
                n_testcases_shown = n_shown_here
            progress.update(n=1)

    out.section()
    out.message(f"showing {n_testcases_shown} testcases out of {len(cube.testcase_names)} total")
    branch = "" if opts.branch is None else ", for branch " + opts.branch
    out.message(f"showing {len(cube.version_range)} versions out of {cube.n_branch_commits} total{branch}")
