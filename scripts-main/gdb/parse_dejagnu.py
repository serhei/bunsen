#!/usr/bin/env python3
info='''WIP -- Example parsing library for GDB buildbot DejaGNU test logs.'''
cmdline_args = [
    ('logdir', None, '<path>', "buildbot log folder"),
    ('verbose', False, None, "show less-important warnings"),
]

# logdir example (individual files may be .xz):
# $ ls gdb-sample-logs/
# baseline  gdb.sum           README.txt  xfail.table
# gdb.log   previous_gdb.sum  xfail
#
# This assumes the format of the public GDB buildbot data:
# - https://gdb-buildbot.osci.io/results/
# - https://gdb-build.sergiodj.net/results/

# XXX The following fields must be added to a testrun by a caller:
# - osver (for GDB buildbot results, present in the path)

import sys
from bunsen import Bunsen, Testrun, Cursor

from datetime import datetime
import dateutil.parser
import os
import re

import lzma

# TODO: Modify to use common DejaGNU parsing code:
#from common.parse_dejagnu import *
from common.parse_dejagnu import test_outcome_map, check_mapping, grok_architecture, get_outcome_line, get_running_exp

datestamp_format = '%a %b %d %H:%M:%S %Y'

def openfile_or_xz(path):
    # Read in bary mode to suppress encoding problems that might occur
    # from reading gdb.{log,sum}. Sometimes inferiors or gdb can just output
    # garbage bytes.
    if os.path.isfile(path):
        return open(path, mode='rb')
    elif os.path.isfile(path+'.xz'):
        return lzma.open(path+'.xz', mode='rb')
    return open(path, mode='rb') # XXX trigger default error

def parse_README(testrun, READMEfile):
    if testrun is None: return None
    f = openfile_or_xz(READMEfile)
    for cur in Cursor(READMEfile, path=os.path.basename(READMEfile), input_stream=f):
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
outcome_subtest_regex = re.compile(r"(?P<outcome>[A-Z]+): (?P<subtest>.*)\n?")

# TODOXXX Redundant with scripts-main/common:
def get_expname_subtest(line):
    m = expname_subtest_regex.fullmatch(line)
    if m is None: return None
    return m.group('outcome'), m.group('expname'), m.group('subtest')

def get_outcome_subtest(line):
    m = outcome_subtest_regex.fullmatch(line)
    if m is None: return None
    return m.group('outcome'), m.group('subtest')

# Normalize the test named NAME. NAME_DICT is used to track these.
#
# This is unfortunately quite complex.

def normalize_name(name_dict, name):

    assert(name is not None)
    assert(name != "")

    # The buildbot does:
    #
    #  test_name = re.sub (r'(\s+)? \(.*\)$', r'', orig_name)
    #
    # But this is overly aggressive, causing thousands of duplicate
    # names to be recorded.
    #
    # Instead, try to remove known constant statuses. Unfortunately, this is
    # quite slow, but it is the most reliable way to avoid 10,000 duplicate
    # names from invading the database.
    test_name = re.sub(r' \((PRMS.*|timeout|eof|GDB internal error'
                       r'|the program exited|the program is no longer running'
                       r'|got interactive prompt|got breakpoint menu'
                       r'|resync count exceeded|bad file format|file not found'
                       r'|incomplete note section|unexpected output'
                       r'|inferior_not_stopped|stopped at wrong place'
                       r'|unknown output after running|dwarf version unhandled'
                       r'|line numbers scrambled\?|out of virtual memory'
                       r'|missing filename|missing module'
                       r'|missing /usr/bin/prelink)\)', r'', name)

    if test_name in name_dict:
        # If the test is already present in the file list, then
        # we include a unique identifier in the end of it, in the
        # form or '<<N>>' (where N is a number >= 2).  This is
        # useful because the GDB testsuite is full of non-unique
        # test messages.
        i = 2
        while True:
            nname = test_name + ' <<' + str (i) + '>>'
            if nname not in name_dict:
                break
            i += 1
        test_name = nname

    name_dict[test_name] = test_name
    return test_name

def parse_dejagnu_sum(testrun, sumfile, all_cases=None,
                      consolidate_pass=False, verbose=True):
    if testrun is None: return None
    f = openfile_or_xz(sumfile)

    last_exp = None
    counts = dict()
    names = dict()

    # The global test_outcome_map doesn't contain all of our
    # outcomes. Add those now.
    test_outcome_map['PATH'] = 'PATH'  # Tests with paths in their names

    # Clear counts dictionary
    counts = dict.fromkeys(test_outcome_map, 0)

    # Iterate over lines in the sum file.
    for cur in Cursor(sumfile, path=os.path.basename(sumfile), input_stream=f):
        line = cur.line

        # There's always an exception.  ERRORs are not output the same
        # way as other test results.  They simply list a reason.
        # FIXME: ERRORs typically span a range of lines
        info = get_expname_subtest(line)
        if info is None:
            if line.startswith('ERROR:'):
                # In this case, the "subtest" is actually the reason
                # for the failure. LAST_EXP is not necessarily strictly
                # correct, but we would have to watch for additional
                # messages (Running TESTFILE ...) to make this work properly.
                # In practice, it's not typically a problem.
                info = ('ERROR', last_exp, line[len('ERROR: '):])
            elif line.endswith(".exp:\n"):
                # An unnamed test. It happens.
                line = line[:-1] + " " + "UNNAMED_TEST" + "\n"
                info = get_expname_subtest(line)
                if info is None:
                    # We tried. Nothing else we can do.
                    print("WARNING: unknown expname/subtest in outcome line --", line, file=sys.stderr)
                    continue
            else:
                continue

        outcome, expname, subtest = info

        # Warn and skip any outcome that is not in test_outcome_map!
        # It will cause an exception later.
        if outcome not in test_outcome_map:
            print(f'WARNING: unexpected test outcome ({outcome}) in line -- {line}')
            continue
        if last_exp != expname:
            last_exp = expname
            names.clear()

        # Normalize the name to account for duplicates.
        subtest = normalize_name(names, subtest)

        if all_cases is not None:
            # ERRORs are not appended to outcome_lines!
            if outcome != "ERROR":
                all_cases.append(line)

        if consolidate_pass:
            pass # not implemented
        else:
            testrun.add_testcase(name=expname, outcome=outcome,
                                 subtest=subtest, origin_sum=cur)
            counts[outcome] += 1
    f.close()

    testrun.pass_count = counts['PASS'] + counts['XPASS'] + counts['KPASS']
    testrun.fail_count = counts['FAIL'] + counts['XFAIL'] + counts['KFAIL'] \
        + counts['ERROR'] # UNTESTED, UNSUPPORTED, UNRESOLVED not tallied
    return testrun

def annotate_dejagnu_log(testrun, logfile, outcome_lines=[],
                         handle_reordering=False, verbose=True):
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
                if verbose:
                    print("WARNING duplicate outcome lines in testcases {} and {}" \
                          .format(testcases[testcase_outcomes[outcome_line]], testcases[i]))
                handle_reordering = False
            else:
                testcase_outcomes[outcome_line] = i

    # (1b) Build a map of outcome_lines:
    testcase_line_start = {} # .exp name -> index of first outcome_line with this name
    for j in range(len(outcome_lines)):
        info = get_expname_subtest(outcome_lines[j])
        if info is None:
            print("WARNING: unknown expname/subtest in outcome line --", outcome_lines[j], file=sys.stderr)
            continue
        outcome, expname, subtest = info
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
    for cur in Cursor(logfile, path=os.path.basename(logfile), input_stream=f, ephemeral=True):
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
                datestamp = dateutil.parser.parse(datestamp)
                # XXX Below turns out a bit brittle in practice.
                #datestamp = datetime.strptime(datestamp, datestamp_format)
                year_month = datestamp.strftime("%Y-%m")
                print("FOUND {} for {}".format(year_month, os.path.basename(logfile)))
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
                    pass # XXX gdb has tons of UNSUPPORTED not showing up in the sum, probably too much even for a verbose run
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
                if verbose:
                    print("WARNING: subtests reordered between .sum and .log, skipped{} from".format("" if new_j > j else " back"), str(j) + "::" + outcome_lines[j], "to", str(new_j) + "::" + outcome_lines[new_j])
                j = new_j
            elif j < len(outcome_lines) and running_test not in outcome_lines[j]:
                if verbose:
                    print("WARNING: no outcome lines matching", running_test)

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

    testrun.arch = grok_architecture(native_configuration_is)
    # XXX testrun.osver should be extracted from buildbot repo path
    #if 'osver' not in testrun:
    #    testrun.osver = '<unknown>' # TODO enable for dry-run parser test
    testrun.version = gdb_version
    testrun.year_month = year_month

    skip = False
    skip_reason = ""
    if testrun.year_month is None:
        skip = True
        skip_reason = "unknown year_month, "
    #elif testrun.arch is None:
    #    skip = True
    #    skip_reason = "unknown arch, "
    # elif testrun.osver is None:
    #     skip = True
    #     skip_reason = "unknown osver, "
    if skip_reason.endswith(", "):
        skip_reason = skip_reason[:-2]
    if skip:
        print("WARNING: skipping logfile", logfile,
              "("+skip_reason+")", file=sys.stderr)
        testrun.problems = skip_reason
        return testrun # return None

    if True:
    #if verbose:
        print("Processed", logfile, testrun.version,
              testrun.arch, testrun.osver, str(testrun.pass_count) + "pass",
              str(testrun.fail_count) + "fail", file=sys.stdout)
    return testrun

b = Bunsen()
if __name__ == '__main__':
    # TODO: enable cwd as the default logdir argument
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['logdir'])

    # TODO: use Bunsen library to load testlogs
    # TODO: support reading testlogs from script's cwd or Bunsen repo
    READMEfile = os.path.join(opts.logdir, 'README.txt')
    logfile = os.path.join(opts.logdir, 'gdb.log')
    sumfile = os.path.join(opts.logdir, 'gdb.sum')

    testrun = Testrun()
    all_cases = []
    testrun = parse_README(testrun, READMEfile)
    testrun = parse_dejagnu_sum(testrun, sumfile, all_cases=all_cases,
                                verbose=opts.verbose)
    testrun = annotate_dejagnu_log(testrun, logfile, all_cases,
                                   verbose=opts.verbose)
    print(testrun.to_json(pretty=True))
