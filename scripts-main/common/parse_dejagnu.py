#!/usr/bin/env python3
# Library for DejaGNU parsing. Many, but by no means all, details of
# the DejaGNU log format are common between testsuites, so this
# library is meant to be used by more specialized log parsers that
# extract additional details (e.g. +gdb/parse_dejagnu).
info='''Example parsing library for DejaGNU test logs.
Based on DejaGNU parsing code written in C++ by Martin Cermak.'''
cmdline_args = [
    ('sumfile', None, '<path>', "DejaGNU .sum file"),
    ('logfile', None, '<path>', "DejaGNU .log file"),
    ('logdir', None, '<path>', "directory of DejaGNU test results"),
    ('verbose', False, None, "show less-important warnings"),
]

import sys
import os
import re
import dateutil.parser

# TODO also use warn_print from bunsen module
from bunsen import Bunsen, Testrun, Testlog, Cursor
from bunsen import Testcase

def check_mapping(line, mapping, start=False):
    '''Check if line contains a magic string from the key set of the
    specified mapping table. If so, return the value corresponding to
    the contained string.
    '''
    if line is None:
        return None
    for k, cand in mapping.items():
        if not start and k in line:
            return cand
        if start and line.startswith(k):
            return cand
    return None # not found

def check_regex_mapping(text, mapping, exact_match=False):
    for regex in mapping:
        m = re.match(regex, text) if exact_match else re.search(regex, text)
        if m is not None and isinstance(mapping[regex],str):
            return mapping[regex]
        elif m is not None:
            return mapping[regex](m)
    return None

# Tables of magic strings:

# Standardized architecture name mapping table.  The
# "Native configuration is " line of a test run is matched against the
# keys in this table.  If a match is found, the architecture is given
# by the corresponding value.
#
# Keys are regular expressions; values are functions which take the regexp
# match as parameter and return a string representation of the architecture.
standard_architecture_map = {r'powerpc64-(\w+)-linux.*':lambda m: 'ppc64',
                             r'powerpc64le-(\w+)-linux.*':lambda m: 'ppc64le',
                             # XXX Older systemtap logs have "Native configuration is /usr/share/dejagnu/libexec/config.guess: unable to guess system type" for ppc64le.
                             r'armv7l-(\w+)-linux-gnueabihf':lambda m: 'armhf',
                             r'(\w+)-(\w+)-linux.*':lambda m: m.group(1)}

# Deduce the architecture from TEXT, which must start with the string
# "Native configuration is ". If TEXT does not start with this string or
# the architecture is not deduced, return None.
def grok_architecture(text):
    if text is None:
        return None
    if text.startswith("Native configuration is "):
        text = text[len("Native configuration is "):-1]
        return check_regex_mapping(text, standard_architecture_map,
                                   exact_match=True)
    return None

# XXX more sloppy alternative to grok_architecture, results in a warning
native_configuration_fallback_map = {"i686":"i686", "x86_64":"x86_64",
                                     "powerpc64":"ppc64",
                                     "powerpc64le":"ppc64le",
                                     "aarch64":"aarch64", "armv7l":"armhf",
                                     "s390x":"s390x"}

uname_machine_map = {".i686-":"i686",
                     ".x86_64-":"x86_64",
                     ".ppc64-":"ppc64",
                     ".ppc64le-":"ppc64le",
                     ".aarch64-":"aarch64",
                     ".s390x-":"s390x"}

# Standardized distro name mapping tables:
standard_osver_map = {
    r'\.fc(\d+)\.':lambda m: "fedora-{}".format(m.group(1)),
    r"\.el6":"rhel-6",
    r"\.el7\.":"rhel-7",
    r"\.ael7\.":"rhel-alt-7",
    r"\.el8":"rhel-8", # .el8. found in log.filename
    #r"\.el8\+":"rhel-8", # Not found in log.version.
    r"\.el9":"rhel-9",
}

standard_osver_filename_map = dict(standard_osver_map)
# XXX Divergences between log.filename, log.version
del standard_osver_filename_map[r"\.el8"]
standard_osver_filename_map[r"\.el8\."] = "rhel-8"
standard_osver_filename_map[r"\.el8\+"] = "rhel-8"

standard_distro_map = {
    r'Fedora release (\d+) \([^R].*\)':lambda m: "fedora-{}".format(m.group(1)),
    r'Fedora release (\d+) \(Rawhide\)':lambda m: "fedora-{}-rawhide".format(m.group(1)),
    r'Ubuntu (\d\d\.\d\d)\.?\d*\w* LTS':lambda m: "ubuntu-{}".format(m.group(1).replace('.','-')),
    r'Red Hat Enterprise Linux Server release (\d+)(?:\.\d+)? \(.*\)':lambda m: "rhel-{}".format(m.group(1)),
    r'Red Hat Enterprise Linux Server release (\d+)(?:\.\d+)? Beta \(.*\)':lambda m: "rhel-{}-beta".format(m.group(1)),
}

# TODO Handle other exotic DejaGNU outcome codes if they come up.
test_outcome_map = {'PASS':'PASS', 'XPASS':'XPASS', 'KPASS':'KPASS',
                    #'IPASS':'IPASS', # XXX not sure this is used anywhere
                    'FAIL':'FAIL', 'KFAIL':'KFAIL', 'XFAIL':'XFAIL',
                    'ERROR':'ERROR', # usually 'ERROR: tcl error sourcing'
                    'DUPLICATE':'DUPLICATE', # used by gdb testsuite
                    'UNTESTED':'UNTESTED', 'UNSUPPORTED':'UNSUPPORTED',
                    'UNRESOLVED':'UNRESOLVED'}

def get_running_exp(running_test):
    # Extract exp name from e.g. 'Running path/to/testsuite/foo.exp ...'
    t1 = 0
    if "/testsuite/" in running_test:
        # XXX use rfind for e.g. 'proj/testsuite/../../other_place/testsuite'
        t1 = running_test.rfind("/testsuite/") \
            + len("/testsuite/")
    elif "Running ./" in running_test:
        t1 = running_test.find("Running ./") + len("Running ./")
    t2 = running_test.find(".exp") + len(".exp")
    running_test = running_test[t1:t2]
    return running_test

# For compact sumfiles that don't include 'Running foo.exp ...' separators:
expname_subtest_regex = re.compile(r"(?P<outcome>[A-Z]+): (?P<expname>[^:]*.exp): (?P<subtest>.*)\n?")

def get_expname_subtest(line):
    m = expname_subtest_regex.fullmatch(line)
    if m is None: return None
    return m.group('outcome'), m.group('expname'), m.group('subtest')

def get_outcome_line(testcase):
    cur = testcase['origin_sum']
    assert isinstance(cur, Cursor)
    cur.line_start = cur.line_end
    return cur.line

class DejaGNUParser:
    def __init__(self, testrun, logfile, logfile_name=None,
                 outcomes=None, consolidate_pass=True,
                 verbose=True):
        self.testrun = testrun
        self.logfile = logfile
        self.logfile_name = logfile_name
        self.outcomes = outcomes
        self.consolidate_pass = consolidate_pass
        self.verbose = verbose

        # XXX compute logfile_name, logfile_path
        self.logfile_path = None
        if isinstance(self.logfile, Testlog):
            self.logfile_path = self.logfile.path
        elif isinstance(self.logfile, str):
            self.logfile_path = self.logfile
        if self.logfile_name is None and self.logfile_path is not None:
            self.logfile_name = os.path.basename(self.logfile_path)
        if self.logfile_path is None and self.logfile_name is not None:
            logfile_path = self.logfile_name

        # XXX parser state tracked by __iter__
        self.running_test = None # XXX full 'Running foo.exp' line
        self.running_exp = None # XXX first outcome line if running_test is missing
        self.running_cur = None
        self.last_test_cur = None
        # XXX: Both of the following could be false if a testcase is UNTESTED:
        self.last_test_passed = False # at least one pass and no fails
        self.last_test_failed = False # at least one fail
        self.failed_subtests = [] # XXX Better described as 'unpassed'.
        self.passed_subtests = []

        self.should_skip = False # a problem was found
        self.skip_reason = ""

    def _collect_metadata(self):
        # Common metadata lines:
        runtest_timestamp = None    # Test run by <user> on <timestamp>
                                    # runtest completed at <timestamp>
        native_configuration = None # Native configuration is <config>
        runtest_hostname = None     # pushing config for host, name is <hostname>
        runtest_target = None       # target is <target>

        runtest_timestamp_full = None # XXX diagnostics only

        # TODOXXX Collect the following GDB metadata:
        # gdb_version      # GNU gdb (GDB) <version>

        # TODOXXX Also identify GCC metadata.

        for cur in Cursor(self.logfile, path=self.logfile_name):
            line = cur.line

            if line.startswith("Test run by ") and " on " in line:
                runtest_timestamp_full = line
                t1 = line.find(" on ") + len(" on ")
                runtest_timestamp = line[t1:].strip()
            elif line.startswith("Native configuration is "):
                t1 = len("Native configuration is ")
                native_configuration = line[t1:].strip()
            elif line.startswith("pushing config for host, name is "):
                t1 = len("pushing config for host, name is ")
                runtest_hostname = line[t1:].strip()
            elif line.startswith("target is "):
                t1 = len("target is ")
                runtest_target = line[t1:].strip()
            elif line.startswith("runtest completed at "):
                # XXX overrides timestamp from 'Test run by'
                runtest_timestamp_full = line
                t1 = len("runtest completed at ")
                runtest_timestamp = line[t1:].strip()

            # TODOXXX metadata lines for GDB (move to appropriate parser)
            # TODOXXX metadata lines for GCC (move to appropriate parser)

            yield cur

        # parse common metadata lines and populate testrun fields

        # mandatory fields + self.testrun.timestamp

        if 'timestamp' not in self.testrun:
            # TODOXXX if timestamp remains unset, commit_logs will pick the value + MARK PROBLEM
            self.testrun.timestamp = None
        if 'year_month' not in self.testrun:
            # TODOXXX if year_month remains unset, commit_logs will pick the value + MARK PROBLEM
            self.testrun.year_month = None
        if runtest_timestamp is not None:
            try:
                datestamp = dateutil.parser.parse(runtest_timestamp)
                self.testrun.timestamp = datestamp.ctime()
                self.testrun.year_month = datestamp.strftime("%Y-%m")
            except ValueError:
                print("WARNING: unknown datestamp in line --", runtest_timestamp_full, file=sys.stderr)
        # XXX bunsen_testlogs_branch, bunsen_testruns_branch set by commit_logs
        # XXX bunsen_commit_id set by commit_logs
        # XXX bunsen_version set by commit_logs

        # optional fields
        
        # XXX skip self.testrun.logfile_path = self.logfile_path

        if 'arch' not in self.testrun:
            self.testrun.arch = None
        if self.testrun.arch is None \
           and native_configuration is not None:
            self.testrun.arch = grok_architecture(native_configuration)
        if self.testrun.arch is None \
           and native_configuration is not None:
            self.testrun.arch = check_mapping(native_configuration,
                                              native_configuration_fallback_map)
            if self.verbose and self.testrun.arch is not None:
                print("WARNING: guessed arch '{}' from native configuration --" \
                      .format(self.testrun.arch), native_configuration,
                      file=sys.stderr)

        if 'origin_host' not in self.testrun:
            self.testrun.origin_host = None
        if self.testrun.origin_host is None \
           and runtest_hostname is not None:
            self.testrun.origin_host = runtest_hostname

        # XXX ignore runtest_target, usually uninformative e.g. 'unix'?
        # TODOXXX check if this is also the case for gdb/gcc and comment out

        # TODOXXX osver must be set by specialized parser or commit_logs
        if 'osver' not in self.testrun:
            self.testrun.osver = None

        # TODOXXX version, source_commit, source_branch set by specialized parser or commit_logs

        # XXX skip self.testrun.logfile_hash = get_hash_4_log(self.testrun)

    def _open_running_test(self, cur, running_test=None, running_exp=None):
        self.running_test = running_test
        self.running_exp = running_exp
        # XXX: Cursor for the entire test, starting on this line:
        self.running_cur = Cursor(start=cur)
        # XXX: Cursor for first subtest, starting on the next line:
        self.last_test_cur = Cursor(start=cur)
        if running_test is not None:
            # XXX: Only skip 'Running foo.exp ...' line if it exists.
            self.last_test_cur.line_start += 1
        self.last_test_passed = False
        self.last_test_failed = False
        self.failed_subtests = []
        self.passed_subtests = []

    def _close_running_test(self, cur, line):
        if self.running_test is None \
           and self.running_exp is None:
            return

        # close Cursor range for this test
        self.running_cur.line_end = cur.line_end-1
        # XXX This is fairly usual across a couple of cases:
        # if self.verbose \
        #    and running_cur.line_start >= running_cur.line_end \
        #    and self.running_test is None:
        #     print("single line testcase {}".format(running_cur.to_str()))
        self.last_test_cur.line_end = cur.line_end-1

        # handle result of previous tests
        # TODO: Perhaps sort *after* annotating?
        #self.failed_subtests.sort() # XXX need to keep in the same order as log!
        if self.running_exp is not None:
            expname = self.running_exp
        else:
            expname = get_running_exp(self.running_test)

        # XXX Assume that definitive testcase data comes from the
        # sumfile, and record the cursor as testcase.origin_sum:
        if self.consolidate_pass and self.last_test_passed:
            self.testrun.add_testcase(name=expname,
                                      outcome='PASS',
                                      origin_sum=self.running_cur)
        elif self.last_test_passed:
            # Report each passed_subtest individually.
            for passed_subtest, outcome, cursor in self.passed_subtests:
                self.testrun.add_testcase(name=expname,
                                          outcome=outcome,
                                          subtest=passed_subtest,
                                          origin_sum=cursor)
        # Report all failed and untested subtests:
        for failed_subtest, outcome, cursor in self.failed_subtests:
            self.testrun.add_testcase(name=expname,
                                      outcome=outcome,
                                      subtest=failed_subtest,
                                      origin_sum=cursor)

    def parse_testlog(self):
        '''Yields a Cursor for each line in the logfile.

        Along the way, parse and collect testcase
        information and common DejaGNU metadata fields.

        Should only be called once.
        '''
        failed_subtest_count = 0
        passed_subtest_count = 0

        for cur in self._collect_metadata():
            line = cur.line

            if (line.startswith("Running ") and ".exp ..." in line) \
               or "Summary ===" in line:
                self._close_running_test(cur, line)
                self._open_running_test(cur, line)
            elif check_mapping(line, test_outcome_map, start=True) is not None \
                 and self.running_test is None:
                # XXX This line starts with a dejagnu outcome
                # but outcomes were not separated by 'Running foo.exp ...'.
                # In such logfiles, exp is encoded in the outcome line.
                info = get_expname_subtest(line)
                if info is None:
                    print("WARNING: unknown expname/subtest in outcome line --", line, file=sys.stderr)
                    continue
                outcome, expname, subtest = info

                if expname != self.running_exp:
                    self._close_running_test(cur, line)
                    self._open_running_test(cur, running_exp=expname)

            # TODO: Handle other dejagnu outcomes if they show up:
            if line.startswith("FAIL: ") \
                or line.startswith("KFAIL: ") \
                or line.startswith("XFAIL: ") \
                or line.startswith("ERROR: tcl error sourcing"):
                self.last_test_cur.line_end = cur.line_end
                self.last_test_failed = True
                self.last_test_passed = False
                self.failed_subtests \
                .append((line,
                         check_mapping(line, test_outcome_map, start=True),
                         self.last_test_cur))
                failed_subtest_count += 1
                self.last_test_cur = Cursor(start=cur)
                self.last_test_cur.line_start += 1
                if self.outcomes is not None: self.outcomes.append(line)
            elif line.startswith("UNTESTED: ") \
                or line.startswith("UNSUPPORTED: ") \
                or line.startswith("UNRESOLVED: "):
                self.last_test_cur.line_end = cur.line_end
                # don't update last_test_{passed,failed}
                self.failed_subtests \
                .append((line,
                         check_mapping(line, test_outcome_map, start=True),
                         self.last_test_cur))
                # don't tally
                self.last_test_cur = Cursor(start=cur);
                self.last_test_cur.line_start += 1
                if self.outcomes is not None: self.outcomes.append(line)
            elif line.startswith("PASS: ") \
                or line.startswith("XPASS: ") \
                or line.startswith("IPASS: "):
                self.last_test_cur.line_end = cur.line_end
                if not self.last_test_failed: # no fails so far
                    self.last_test_passed = True
                if not self.consolidate_pass:
                    self.passed_subtests \
                    .append((line,
                             check_mapping(line, test_outcome_map, start=True),
                             self.last_test_cur))
                passed_subtest_count += 1
                self.last_test_cur = Cursor(start=cur)
                self.last_test_cur.line_start += 1
                if self.outcomes is not None: self.outcomes.append(line)

            yield cur

        self.testrun.pass_count = passed_subtest_count
        self.testrun.fail_count = failed_subtest_count

    def annotate_testlog(self, outcomes=None, handle_reordering=False):
        '''Yields a Cursor for each line in the logfile.

        Along the way, annotate the testcases in testrun (presumably
        already parsed from a DejaGNU sumfile) with their
        corresponding locations in the logfile.

        Should only be called once.
        '''
        if outcomes is None:
            outcomes = self.outcomes
        if outcomes is None:
            outcomes = []

        # (1) Build a map of testcases.
        # XXX testcase_outcomes approach allows the parser to reorder subtests,
        # but may not work for some testsuites that use identical outcome lines
        testcases = self.testrun.testcases
        testcase_start = {} # .exp name -> index of first testcase with this name
        testcase_outcomes = {} # outcome line -> index of testcase with this outcome line
        for i in range(len(testcases)):
            name = testcases[i].name
            outcome_line = testcases[i].outcome_line()
            if name not in testcase_start:
                testcase_start[name] = i
            if handle_reordering:
                if outcome_line in testcase_outcomes:
                    if verbose:
                        print("WARNING duplicate outcome lines in testcases {} and {}" \
                              .format(testcases[testcase_outcomes[outcome_line]], testcases[i]))
                    handle_reordering = False
                else:
                    testcase_outcomes[outcome_line] = i

        # (2) Parse the logfile and match its segments to the map of testcases.
        i = None # XXX index into testcases
        j = 0 # XXX index into outcomes
        self.running_test = None
        self.running_cur = None
        self.last_test_cur = None
        next_outcome = None # outcome of testcases[i]
        finished_tests = False
        for cur in self._collect_metadata():
            line = cur.line

            if finished_tests:
                # no more testcases to annotate, but keep collecting metadata:
                yield cur
                continue

            if (line.startswith("Running ") and ".exp ..." in line) \
               or "Summary ===" in line:
                if self.running_test is not None:
                    self.running_cur.line_end = cur.line_end-1
                    self.last_test_cur.line_end = cur.line_end-1

                    if self.running_test not in testcase_start and self.verbose:
                        # XXX Cut down on the chatter for normally-empty cases:
                        if not self.running_test.startswith('systemtap/notest.exp') \
                           and not self.running_test.startswith('systemtap.samples/examples.exp'):
                            print("WARNING: no testcases for {}@{}, skipping".format(self.running_test, self.running_cur.to_str()))
                    else:
                        i = testcase_start[self.running_test]
                        if 'subtest' not in testcases[i]:
                            testcases[i]['origin_log'] = self.running_cur

                    if "Summary ===" in line:
                        finished_tests = True
                        continue # no more testcases to annotate

                    self.running_test = get_running_exp(line)
                    self.running_cur = Cursor(start=cur)
                    self.last_test_cur = Cursor(start=cur)
                    self.last_test_cur.line_start += 1
                    if self.running_test in testcase_start:
                        i = testcase_start[self.running_test] # XXX for non-handle_reordering case
                        next_outcome = testcases[i].outcome_line()
                    else:
                        i = None # XXX probably skip associated subtests as they're not parsed
                        next_outcome = None
                elif j < len(outcomes) and outcomes[j] in line:
                    # Might not be start of line, so we checked for outcome anywhere.
                    self.last_test_cur.line_end = cur.line_end
                    if handle_reordering and outcomes[j] in testcase_outcomes:
                        ix = testcase_outcomes[outcomes[j]]
                        testcases[ix]['origin_log'] = last_test_cur
                    elif i is not None and i < len(testcases) and 'subtest' in testcases[i] and next_outcome in line:
                        testcases[i]['origin_log'] = last_test_cur
                        i += 1 # XXX advance testcases, assuming they are in order
                        if i < len(testcases):
                            next_outcome = testcases[i].outcome_line()
                    j += 1 # XXX advance outcomes
                    self.last_test_cur = Cursor(start=cur)
                    self.last_test_cur.line_start += 1

                yield cur

    def validate(self):
        '''Return True if testrun contains all important metadata.

        Add a string describing any problems to testrun.problems.
        '''
        # XXX Testruns that don't validate should still be added to
        # the repo, but we will document any missing (important)
        # metadata in self.testrun.problems.

        # XXX skip checking logfile_path, logfile_hash
        if self.testrun.arch is None:
            self.testrun.arch = 'unknown'
            self.should_skip = True
            self.skip_reason = "unknown arch, "
        elif self.testrun.osver is None:
            self.testrun.osver = 'unknown'
            self.should_skip = True
            self.skip_reason = "unknown osver, "

        # XXX skip if "free-form-hash" and self.testrun.version != self.testrun.logfile_hash:
        #     self.should_skip = True
        #     print("WARNING: skipping logfile", self.testrun.logfile_path, "since free-form-hash wasn't set and", self.testrun.version, "differs from", self.testrun.logfile_hash, ".", file=sys.stderr)
        #     return not self.should_skip

        if self.skip_reason.endswith(", "):
            self.skip_reason = self.skip_reason[:-2]
        self.testrun.problems = self.skip_reason

        # XXX skip self.testrun.key = self.testrun.logfile_hash \
        #    + "+" + self.testrun.osver \
        #    + "+" + self.testrun.arch
        if self.verbose:
            # print("Processed", self.logfile_path, self.testrun.version,
            #       self.testrun.arch, str(self.testrun.pass_count) + "pass",
            #       str(self.testrun.fail_count) + "fail", file=sys.stdout)
            print("Processed", self.logfile_path, self.testrun.to_json(summary=True))
        if self.should_skip:
            print("WARNING: incomplete information for logfile", self.logfile_path,
                  "("+self.skip_reason+")", file=sys.stderr)

        return not self.should_skip

def parse_dejagnu_log(testrun, logfile, logfile_name=None,
                      outcomes=None, consolidate_pass=True,
                      verbose=True, validate=True,
                      reject_problems=False):
    '''Parse a DejaGNU log or sum file with no special characteristics.

    Log files are less reliable since 'PASS:'/'FAIL:' lines in a log
    file can be interspersed with test output that fails to end on a
    newline.

    Conventions: Parsed testcases are added to 'testrun' argument. The
    'testrun' object is also returned. If essential metadata is
    missing, None is returned.

    Optionally, all raw 'PASS:'/'FAIL:' lines are appended to a list
    passed in 'outcomes' argument. The resulting list can be used with
    annotate_dejagnu_log().

    If 'consolidate_pass' is enabled, the parser only issues one
    'PASS' entry per a passing testcase (collapsing subtests) and
    drops 'PASS' entries for a failing testcase (leaving only 'FAIL'
    entries), which significantly speeds up log parsing.
    '''
    base_parser = \
        DejaGNUParser(testrun, logfile, logfile_name=logfile_name,
                      outcomes=outcomes, consolidate_pass=consolidate_pass,
                      verbose=verbose)
    for cur in base_parser.parse_testlog():
        # XXX perform additional parsing here
        # e.g. if cur.line.startswith("MyCoolProject version"): ...
        pass

    # XXX populate additional metadata here

    success = base_parser.validate() if validate else True
    return testrun if success or not reject_problems else None

# XXX mcermak's idea when iterating multiple logfiles
# all_testruns.append(testrun)
# valid_arches.append(testrun.arch)
# valid_osvers.append(testrun.osver)
# valid_hashes.append(testrun.logfile_hash)
# for tc in testrun.testcases: valid_testcases.append(tc['name'])

# TODOXXX move to systemtap/parse_dejagnu.py
# lots of information to extract SystemTap metadata lines:
host_is_regex = re.compile(r"Linux (?P<origin_host>\S+) (?P<kernel_version>\S+)")
# TODOXXX host_is_regex = re.compile(r"Linux (?P<origin_host>\S+) (?P<kernel_version>\S+)( \S+ \S+ (?P<timestamp>\S+ \S+ +\S+ \S+ \S+ \S+)?)?") # XXX allow ignoring trailing text
commit_release_regex = re.compile(r"commit release\S+-g(?P<source_commit>[0-9a-fA-F]+)")

# TODOXXX move to systemtap/parse_dejagnu.py
def parse_dejagnu_log_SYSTEMTAP(testrun, logfile, logfile_name=None,
                                outcomes=None, consolidate_pass=True,
                                verbose=True, validate=True,
                                reject_problems=False):
    # SystemTap metadata lines:

    host_is = None           # Host: Linux <hostname> <kernel_uname> ... <timestamp>
    snapshot_version = None  # Snapshot: version <version>, commit release-<version>-g<source_commit_id>
    gcc_version = None       # GCC: <version> [<fullversion>]
    distro_is = None         # Distro: <distro>
    # TODO: selinux_is       # SElinux: <state>

    translator_driver = None # (.log only) Systemtap translator/driver (version <version> rpm <rpm>)
    # TODO: session_arch     # (.log only) Session arch: <arch> release: <kernel_version>

    uname_machine_raw = None # UNAME_MACHINE
    uname_machine = None

    rhel7_base_seen = False
    rhel7_alt_seen = False

    base_parser = \
        DejaGNUParser(testrun, logfile, logfile_name=logfile_name,
                      outcomes=outcomes, consolidate_pass=consolidate_pass,
                      verbose=verbose)
    for cur in base_parser.parse_testlog():
        line = cur.line

        # metadata lines for SystemTap
        if line.startswith("Host: "):
            t1 = len("Host: ")
            host_is = line[t1:]
        elif line.startswith("Snapshot: version"):
            t1 = len("Snapshot: ")
            snapshot_version = line[t1:]
            if "rpm " in snapshot_version:
                t1 = snapshot_version.find("rpm ") + len("rpm ")
                snapshot_version = snapshot_version[t1:]
        elif line.startswith("GCC: "):
            t1 = len("GCC: ")
            gcc_version = line[t1:]    
        elif line.startswith("Distro: "):
            t1 = len("Distro: ")
            distro_is = line[t1:]
        # elif line.startswith("SElinux: "):
        #     t1 = len("SElinux: ")
        #     selinux_is = line[t1:]

        elif line.startswith("Systemtap translator/driver (version"):
            translator_driver = line
            if "rpm " in translator_driver:
                t1 = translator_driver.find("rpm ") + len("rpm ")
                translator_driver = translator_driver[t1:]
            if translator_driver.endswith(")"):
                translator_driver = translator_driver[:-1]

        elif line.startswith("UNAME_MACHINE"):
            uname_machine_raw = line

        # XXX Following check for rhel-{base,alt} is obscure,
        # but the logfiles lack better information, it seems.
        elif "Session arch: " in line \
           and "release: 3." in line \
           and ".el7." in line:
            rhel7_base_seen = True
        elif "Session arch: " in line \
           and "release: 4." in line \
           and ".el7." in line:
            rhel7_alt_seen = True

    # parse common metadata

    host_m = None
    if host_is is not None:
        host_m = host_is_regex.match(host_is)

    commit_release_m = None
    if snapshot_version is not None:
        commit_release_m = commit_release_regex.match(snapshot_version)
    if commit_release_m is None and translator_driver is not None:
        commit_release_m = commit_release_regex.match(translator_driver)

    if 'timestamp' not in testrun:
        testrun.timestamp = None
    if testrun.timestamp \
       and host_m is not None \
       and 'timestamp' in host_m:
        testrun.timestamp = host_m['timestamp']

    # XXX snapshot_version is preferred
    testrun.version = translator_driver \
        if translator_driver is not None and snapshot_version is None \
        else snapshot_version if snapshot_version is not None \
        else testrun.version

    if 'source_commit' not in testrun:
        testrun.source_commit = None
    if testrun.source_commit is None \
       and commit_release_m is not None:
        testrun.source_commit = commit_release_m['source_commit']

    # XXX do not set source_branch; TODO: could be checked by commit_logs?
    # XXX do not set arch
    # TODO commit logs could calculate source_branch

    if 'arch' not in testrun:
        testrun.arch = None
    if uname_machine is None:
        uname_machine = check_mapping(uname_machine_raw,
                                      uname_machine_map)
    if uname_machine is None:
        uname_machine = check_mapping(base_parser.logfile_path,
                                      uname_machine_map)
    # TODO: Could also check session_arch
    if testrun.arch is None:
        testrun.arch = uname_machine

    if 'osver' not in testrun:
        testrun.osver = None
    if testrun.osver is None and 'version' in testrun:
        testrun.osver = check_regex_mapping(testrun.version,
                                            standard_osver_map)
    if testrun.osver is None:
        # XXX valid for SystemTap buildbots
        testrun.osver = check_regex_mapping(logfile_path,
                                            standard_osver_filename_map)
        # TODOXXX add similar check for GDB buildbots
    if testrun.osver is None:
        # XXX distro_map is valid for many systems,
        # but distro_is only specified for SystemTap testsuite
        testrun.osver = check_regex_mapping(distro_is,
                                            standard_distro_map)
    if testrun.osver is None:
        print("WARNING: unknown distro_is", distro_is,
              "using full distro name", file=sys.stderr)
        testrun.osver = distro_is

    if rhel7_alt_seen:
        testrun.osver = "rhel-alt-7"

    if rhel7_base_seen and rhel7_alt_seen:
        base_parser.should_skip = True
        base_parser.skip_reason += "seen both rhel7_base and rhel7_alt, "

    if 'origin_host' not in testrun:
        testrun.origin_host = None
    if testrun.origin_host is None \
       and host_m is not None:
        testrun.origin_host = host_m['origin_host']

    # parse additional metadata

    if 'gcc_version' not in testrun:
        testrun.gcc_version = None
    if testrun.gcc_version is None and gcc_version is not None:
        testrun.gcc_version = gcc_version

    if 'kernel_version' not in testrun \
       and host_m is not None:
        testrun.kernel_version = host_m['kernel_version']
    # TODO: Could also check session_arch

    if validate and testrun.gcc_version is None:
        testrun.gcc_version = 'unknown'
    if validate and testrun.kernel_version is None:
        testrun.kernel_version = 'unknown'
    success = base_parser.validate() if validate else True
    return testrun if success or not reject_problems else None

# TODOXXX incorporate numerous tweaks for gdb
def annotate_dejagnu_log(testrun, logfile, logfile_name=None,
                         outcomes=[], handle_reordering=False,
                         verbose=True, validate=True,
                         reject_problems=False):
    '''Annotate the testcases in a Testrun with information from a log file.

    Here, outcomes is a list of all individual pass/fail lines in the
    file, presumably collected by calling parse_dejagnu_log() with a
    sum file.
    '''
    base_parser = \
        DejaGNUParser(testrun, logfile, logfile_name=logfile_name,
                      outcomes=outcomes, verbose=verbose)
    for cur in base_parser.annotate_testlog(handle_reordering=handle_reordering):
        # XXX perform additional parsing here
        # e.g. if cur.line.startswith("MyCoolProject version"): ...
        pass

    success = base_parser.validate() if validate else True
    return testrun if success or not reject_problems else None

# TODOXXX collect_testlogs :: various arguments -> list of Testlog
def collect_testlogs(*args):
    testlogs = []
    for arg in args:
        if arg is None:
            continue
        elif isinstance(arg, Testlog):
            testlogs.append(arg)
        elif isinstance(arg, str) and os.path.isfile(arg):
            testlogs.append(Testlog(None, path=arg))
        # TODOXXX handle directory
        # TODOXXX handle file stream? -> use Testlog?
        # TODOXXX handle tarfile stream? -> use Testlog?
        # TODOXXX handle network stream? -> use Testlog?
        # TODOXXX handle list
        else:
            print ("WARNING: unknown testlog object", arg)
    return testlogs

# TODOXXX parse_testlogs :: list of Testlog -> Testrun
def parse_testlogs(testlogs):
    '''Identify relevant files in a list of Testlogs.

    Parse the DejaGNU sumfile and annotate with the contents of
    corresponding DejaGNU logfiles.
    '''
    sumfile, logfile = None, None
    for testlog in testlogs:
        # TODO: warn if multiple sumfiles/logfiles are found
        if '.sum.' in testlog.path or testlog.path.endswith('.sum'):
            sumfile = testlog
        elif '.log.' in testlog.path or testlog.path.endswith('.log'):
            logfile = testlog
    if sumfile is None and logfile is not None:
        # TODOXXX: warn that results may not be accurate
        sumfile, logfile = logfile, None

    testrun = Testrun()
    outcomes = []
    if sumfile is not None:
        parse_dejagnu_log(testrun, sumfile, outcomes=outcomes,
                          validate=(logfile is None))
    if logfile is not None:
        annotate_dejagnu_log(testrun, logfile, outcomes=outcomes,
                             validate=True)
    # TODOXXX: warn if testrun is empty / did not validate (may still want to add to repo)
    return testrun

b = Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          optional_args=['sumfile','logfile'])
    testlogs = collect_testlogs(opts.logfile, opts.sumfile, opts.logdir)
    testrun = parse_testlogs(testlogs)
    print(testrun.to_json(pretty=True))
