#!/usr/bin/env python3
# WIP -- Example parsing library for GDB buildbot DejaGNU test logs.
usage = "parse_dejagnu.py <buildbot_log_folder>"

# <buildbot_log_folder> example (individual files may be .xz):
# $ ls gdb-sample-logs/
# baseline  gdb.sum           README.txt  xfail.table
# gdb.log   previous_gdb.sum  xfail
#
# This assumes the format of the public GDB buildbot data:
# - https://gdb-buildbot.osci.io/results/
# - https://gdb-build.sergiodj.net/results/

# XXX The following fields must be added to a testrun by a caller:
# - osver (for GDB buildbot results, present in the path)

# TODO: Suggested options:
# - increase/decrease verbosity
quiet = False

import sys
from bunsen import Bunsen, Testrun, Cursor

from datetime import datetime
import dateparser
import os
import re

import lzma

# TODO: Modify to use common DejaGNU parsing code:
#from common.parse_dejagnu import *

# === TODO CREATE common.parse_dejagnu AND HARMONIZE WITH SYSTEMTAP ===

native_configuration_map = {"Native configuration is i686-pc-linux-gnu":"i686",
                            "Native configuration is i686-unknown-linux-gnu":"i686",
                            "Native configuration is x86_64-unknown-linux-gnu":"x86_64",
                            "Native configuration is powerpc64-unknown-linux-gnu":"ppc64",
                            # Older systemtap logs have "Native configuration is /usr/share/dejagnu/libexec/config.guess: unable to guess system type" for ppc64le.
                            "Native configuration is powerpc64le-unknown-linux-gnu":"ppc64le",
                            "Native configuration is aarch64-unknown-linux-gnu":"aarch64",
                            "Native configuration is armv7l-unknown-linux-gnueabihf":"armhf",
                            "Native configuration is s390x-ibm-linux":"s390x",
                            "Native configuration is x86_64-pc-linux-gnu":"x86_64", # seen on Ubuntu
                            }

# TODO Handle other exotic DejaGNU outcome codes if they come up.
test_outcome_map = {'PASS':'PASS', 'XPASS':'XPASS', 'IPASS':'PASS',
                    'FAIL':'FAIL', 'KFAIL':'KFAIL', 'XFAIL':'XFAIL',
                    'ERROR: tcl error sourcing':'ERROR',
                    'UNTESTED':'UNTESTED', 'UNSUPPORTED':'UNSUPPORTED',
                    'UNRESOLVED':'UNRESOLVED'}

def check_mapping(line, mapping, start=False):
    '''Check if line contains a magic string from specified mapping table.'''
    if line is None:
        return None
    for k, cand in mapping.items():
        if not start and k in line:
            return cand
        if start and line.startswith(k):
            return cand
    return None # not found

def get_outcome_line(testcase):
    cur = testcase['origin_sum']
    assert isinstance(cur, Cursor)
    cur.line_start = cur.line_end
    return cur.line

# === TODO ABOVE ARE PROBABLY COMMON ACROSS GDB/SYSTEMTAP ===

# === TODO BELOW ARE SPECIFIC TO GDB PARSING ===

datestamp_format = '%a %b %d %H:%M:%S %Y'

def openfile_or_xz(path):
    if os.path.isfile(path):
        return open(path, mode='rt')
    elif os.path.isfile(path+'.xz'):
        return lzma.open(path+'.xz', mode='rt')
    return open(path, mode='rt') # XXX trigger default error

def parse_README(testrun, READMEfile):
    if testrun is None: return None
    f = openfile_or_xz(READMEfile)
    for cur in Cursor(READMEfile, name=os.path.basename(READMEfile), input_file=f):
        line = cur.line
        if line.startswith("Logs for: "):
            t1 = line.find("Logs for: ") + len("Logs for: ")
            testrun.source_commit = line[t1:].strip()
        if line.startswith("Branch tested: "):
            t1 = line.find("Branch tested: ") + len("Branch tested: ")
            testrun.source_branch = line[t1:].strip()
    f.close()
    return testrun

expname_subtest_regex = re.compile(r"(?P<outcome>[A-Z]+): (?P<expname>[^:]*.exp): (?P<subtest>.*)\n?")

def get_expname_subtest(line):
    m = expname_subtest_regex.fullmatch(line)
    if m is None: return None
    return m.group('outcome'), m.group('expname'), m.group('subtest')

def parse_dejagnu_sum(testrun, sumfile, consolidate_pass=True, all_cases=None):
    if testrun is None: return None
    f = openfile_or_xz(sumfile)

    last_exp = None
    last_test_passed = False # at least one pass and no fails
    last_test_failed = False # at least one fail
    failed_subtests = [] # XXX Better known as 'unpassed'?
    passed_subtests = []
    failed_subtests_summary = 0
    passed_subtests_summary = 0

    for cur in Cursor(sumfile, name=os.path.basename(sumfile), input_file=f):
        line = cur.line

        # XXX all lines in these GDB sumfiles are outcome lines
        info = get_expname_subtest(line)
        if info is None: continue
        outcome, expname, subtest = info
        if all_cases is not None: all_cases.append(line)

        if expname != last_exp and last_exp is not None:
            running_cur.line_end = cur.line_end-1
            if consolidate_pass and last_test_passed:
                testrun.add_testcase(name=last_exp,
                                     outcome='PASS',
                                     origin_sum=running_cur)
            elif last_test_passed:
                # Report each passed subtest individually:
                for passed_subtest, outcome, cursor in passed_subtests:
                    testrun.add_testcase(name=last_exp,
                                         outcome=outcome,
                                         subtest=passed_subtest,
                                         origin_sum=cursor)
            # Report all failed and untested subtests:
            for failed_subtest, outcome, cursor in failed_subtests:
                testrun.add_testcase(name=last_exp,
                                     outcome=outcome,
                                     subtest=failed_subtest,
                                     origin_sum=cursor)
        if expname != last_exp:
            last_exp = expname
            running_cur = Cursor(start=cur)
            last_test_passed = False
            last_test_failed = False
            failed_subtests = []
            passed_subtests = []

        # TODO: Handle other dejagnu outcomes if they show up:
        if line.startswith("FAIL: ") \
           or line.startswith("KFAIL: ") \
           or line.startswith("XFAIL: ") \
           or line.startswith("ERROR: tcl error sourcing"):
            last_test_failed = True
            last_test_passed = False
            failed_subtests.append((line,
                                    check_mapping(line, test_outcome_map, start=True),
                                    cur)) # XXX single line
            failed_subtests_summary += 1
        if line.startswith("UNTESTED: ") \
           or line.startswith("UNSUPPORTED: ") \
           or line.startswith("UNRESOLVED: "):
            # don't update last_test_{passed,failed}
            failed_subtests.append((line,
                                    check_mapping(line, test_outcome_map, start=True),
                                    cur))
            # don't tally
        if line.startswith("PASS: ") \
           or line.startswith("XPASS: ") \
           or line.startswith("IPASS: "):
            if not last_test_failed: # no fails so far
                last_test_passed = True
            if not consolidate_pass:
                passed_subtests.append((line,
                                        check_mapping(line, test_outcome_map, start=True),
                                        cur))
            passed_subtests_summary += 1
    f.close()

    testrun.pass_count = passed_subtests_summary
    testrun.fail_count = failed_subtests_summary

    return testrun

# TODO: harmonize with SystemTap get_running_exp
def get_running_exp(running_test):
    t1 = 0
    # XXX use rfind because of gdb/testsuite/../../other_place/gdb/testsuite shenanigans:
    if "gdb/testsuite/" in running_test:
        t1 = running_test.rfind("gdb/testsuite/") \
            + len("gdb/testsuite/")
    elif "Running ./" in running_test:
        t1 = running_test.find("Running ./") + len("Running ./")
    t2 = running_test.find(".exp") + len(".exp")
    running_test = running_test[t1:t2]
    return running_test

def annotate_dejagnu_log(testrun, logfile, outcome_lines=[], handle_reordering=False):
    '''
    Annotate the testcases in a Testrun (presumably parsed from
    gdb.sum) with their locations in a corresponding gdb.log file.

    Also extract some metadata not present in gdb.sum.

    Here, outcome_lines is a list of all individual pass/fail lines in the file.
    '''
    if testrun is None: return None

    # (1a) Build a map of testcases.
    # XXX testcase_outcomes approach allows the parser to reorder subtests,
    # but may not work for some testsuites that use identical outcome lines
    testcases = testrun.testcases
    testcase_start = {} # .exp name -> index of first testcase with this name
    testcase_outcomes = {} # outcome line -> index of testcase with this outcome line
    for i in range(len(testcases)):
        name = testcases[i]['name']
        outcome_line = get_outcome_line(testcases[i])
        if name not in testcase_start:
            testcase_start[name] = i
        if handle_reordering:
            if outcome_line in testcase_outcomes:
                print("WARNING duplicate outcome lines in testcases {} and {}" \
                      .format(testcases[testcase_outcomes[outcome_line]], testcases[i]))
                handle_reordering = False
            else:
                testcase_outcomes[outcome_line] = i

    # (1b) Build a map of outcome_lines:
    testcase_line_start = {} # .exp name -> index of first outcome_line with this name
    for j in range(len(outcome_lines)):
        outcome, expname, subtest = get_expname_subtest(outcome_lines[j])
        if expname not in testcase_line_start:
            testcase_line_start[expname] = j

    # (2) Parse the logfile and match its segments to the map of testcases.
    i = None # XXX index into testcases
    j = 0 # XXX index into outcome_lines
    native_configuration_is = None
    year_month = None
    gdb_version = None
    running_test = None
    running_cur = None
    last_test_cur = None
    next_outcome = None # outcome of testcases[i]
    f = openfile_or_xz(logfile)
    for cur in Cursor(logfile, name=os.path.basename(logfile), input_file=f, fast_hack=True):
        line = cur.line

        if line.startswith("Native configuration is"):
            native_configuration_is = line
        if (line.startswith("Test Run By") and " on " in line) or (" completed at " in line):
            if line.startswith("Test Run By"):
                t1 = line.rfind(" on ") + len(" on ")
            else:
                t1 = line.find(" completed at ") + len(" completed at ")
            datestamp = line[t1:].strip()
            try:
                datestamp = dateparser.parse(datestamp)
                # XXX Below turns out a bit brittle in practice.
                #datestamp = datetime.strptime(datestamp, datestamp_format)
                year_month = datestamp.strftime("%Y-%m")
            except ValueError:
                print("WARNING: unknown datestamp in line --", line, file=sys.stderr)
        if line.startswith("GNU gdb (GDB) "):
            tentative_gdb_version = line[len("GNU gdb (GDB) "):].strip()
            if len(tentative_gdb_version) > 0:
                gdb_version = tentative_gdb_version # use the last occurrence

        # TODO: The log includes a number of UNSUPPORTED testcases not
        # in the sum, parse these for completeness?

        if (line.startswith("Running ") and ".exp ..." in line) \
           or ("Summary ===" in line and "sed -n" not in line): # XXX Aargh tricky case.
            if running_test is not None:
                running_cur.line_end = cur.line_end-1
                last_test_cur.line_end = cur.line_end-1

                if running_test not in testcase_start:
                    pass # XXX gdb has tons of UNSUPPORTED not showing up in the sum
                    #print("WARNING: no testcases for {}@{}, skipping".format(running_test, running_cur.to_str()))
                else:
                    i = testcase_start[running_test]
                    if 'subtest' not in testcases[i]:
                        testcases[i]['origin_log'] = running_cur

            if "Summary ===" in line:
                running_test = None
                continue # no more testcases, but should keep parsing for metadata

            running_test = get_running_exp(line)

            # XXX A moderate source of AAARGH is that outcome_lines
            # may not be in the same order in the .sum and .log files.
            # For example, with gdb.ada/info_types.exp:
            # - sum has subtest info types new_integer_type before set lang ada
            # - log has subtest set lang ada before info types new_integer_type
            #
            # Try to use testcase_line_start to jump to the correct location.
            #
            # TODO: This matching code is still imperfect but better
            # than falling off the end of the logfile at the very
            # first reordering.
            if j < len(outcome_lines) and running_test not in outcome_lines[j] and running_test in testcase_line_start:
                new_j = testcase_line_start[running_test]
                # TODO: Control this with verbose option.
                #print("WARNING: subtests reordered between .sum and .log, skipped{} from".format("" if new_j > j else " back"), str(j) + "::" + outcome_lines[j], "to", str(new_j) + "::" + outcome_lines[new_j])
                j = new_j
            elif j < len(outcome_lines) and running_test not in outcome_lines[j]:
                pass
                # TODO: Control this with verbose option.
                #print("WARNING: no outcome lines matching", running_test()

            running_cur = Cursor(start=cur)
            last_test_cur = Cursor(start=cur); last_test_cur.line_start += 1
            if running_test in testcase_start:
                i = testcase_start[running_test] # XXX for non-handle_reordering case
                next_outcome = get_outcome_line(testcases[i])
            else:
                i = None # XXX probably skip associated subtests as they're not parsed
                next_outcome = None
        elif j < len(outcome_lines) and outcome_lines[j] in line:
            # Might not be start of line, so we checked for outcome anywhere.
            last_test_cur.line_end = cur.line_end
            if handle_reordering and outcome_lines[j] in testcase_outcomes:
                ix = testcase_outcomes[outcome_lines[j]]
                testcases[ix]['origin_log'] = last_test_cur
            elif i is not None and i < len(testcases) and 'subtest' in testcases[i] and next_outcome in line:
                testcases[i]['origin_log'] = last_test_cur
                i += 1 # XXX advance testcases, assuming they are in order
                if i < len(testcases):
                    next_outcome = get_outcome_line(testcases[i])
            j += 1 # XXX advance outcome_lines
            last_test_cur = Cursor(start=cur); last_test_cur.line_start += 1
    f.close()

    uname_machine = None
    if uname_machine is None:
        uname_machine = check_mapping(native_configuration_is,
                                      native_configuration_map)

    testrun.arch = uname_machine
    # XXX testrun.osver should be extracted from buildbot repo path
    testrun.version = gdb_version
    testrun.year_month = year_month

    skip = False
    skip_reason = ""
    if testrun.year_month is None:
        skip = True
        skip_reason = "unknown year_month, "
    elif testrun.arch is None:
        skip = True
        skip_reason = "unknown arch, "
    # elif testrun.osver is None:
    #     skip = True
    #     skip_reason = "unknown osver, "
    if skip:
        print("WARNING: skipping logfile", logfile,
              "("+skip_reason+")", file=sys.stderr)
        return None

    if not quiet:
        print("Processed", logfile, testrun.version,
              testrun.arch, str(testrun.pass_count) + "pass",
              str(testrun.fail_count) + "fail", file=sys.stdout)
    return testrun

b = Bunsen()
if __name__ == '__main__':
    # TODO: enable cwd as the default command line argument
    logdir = b.cmdline_args(sys.argv, 1, usage=usage)

    # TODO: use Bunsen library to load testlogs
    # TODO: support reading testlogs from script's cwd or Bunsen repo
    READMEfile = os.path.join(logdir, 'README.txt')
    logfile = os.path.join(logdir, 'gdb.log')
    sumfile = os.path.join(logdir, 'gdb.sum')

    testrun = Testrun()
    all_cases = []
    testrun = parse_README(testrun, READMEfile)
    testrun = parse_dejagnu_sum(testrun, sumfile, all_cases=all_cases)
    testrun = annotate_dejagnu_log(testrun, logfile, all_cases)
    print(testrun.to_json(pretty=True))
