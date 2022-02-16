#!/usr/bin/env python3
info='''WIP -- Example parsing library for SystemTap DejaGNU test logs.
Based on some DejaGNU parsing code written in C++ by Martin Cermak.'''
cmdline_args = [
    ('logfile', None, '<path>', "SystemTap log file"),
    ('sumfile', None, '<path>', "SystemTap sum file"),
    ('verbose', False, None, "show less-important warnings"),
]

# TODO: Additional information & fields to parse (harmonize with gdb):
# - source_commit (extract from version field?)
# - year_month (extract from sum/logfiles, sysinfo, or path)
# - gcc_version
# - kernel_version

import sys
from bunsen import Bunsen, Testrun, Cursor

import os

# TODO: Modify to use common DejaGNU parsing code:
# from common.parse_dejagnu import *
from common.parse_dejagnu import test_outcome_map, check_mapping, check_regex_mapping, grok_architecture, get_outcome_line, get_running_exp
from common.parse_dejagnu import standard_osver_map, standard_osver_filename_map, standard_distro_map, uname_machine_map

stap_testrun_fields = {'arch', 'version', 'pass_count', 'fail_count', 'osver'}
# XXX Not used from mcermak's script: logfile_path, logfile_hash, key

def validate_testrun(testrun, print_errors=True):
    '''
    XXX Check if a Testrun contains relevant metadata for SystemTap.
    '''
    result = True
    for field in stap_testrun_fields:
        if field not in testrun:
            result = False
            if print_errors:
                print("ERROR: missing {} in testrun {}".format(field, testrun),
                      file=sys.stderr)
            else:
                return result
    return result

def parse_dejagnu_log(testrun, logfile, logfile_name=None, all_cases=None,
                      consolidate_pass=True, verbose=True):
    '''
    Parse log or sum file. Sum files are more reliable since
    PASS:/FAIL: results can sometimes be interspersed with SystemTap
    output in a garbled manner.

    Optionally, append all raw pass/fail lines to all_cases array.

    Enabling consolidate_pass option issues only one 'PASS' entry for
    a passing testcase (collapsing subtests) and drops 'PASS' entries
    for a failing testcase (leaving only the 'FAIL's), which
    significantly speeds up the log parsing.
    '''
    snapshot_version = None
    translator_driver_version = None
    uname_machine_raw = None
    uname_machine = None
    running_test = None
    running_cur = None
    native_configuration_is = None
    distro_is = None
    # XXX: Both of the following can be False if a testcase is UNTESTED:
    last_test_passed = False  # at least one pass and no fails
    last_test_failed = False  # at least one fail
    failed_subtests = [] # XXX Better known as 'unpassed'?
    passed_subtests = []
    failed_subtests_summary = 0
    passed_subtests_summary = 0

    rhel7_base_seen = False
    rhel7_alt_seen = False

    if logfile_name is None:
        logfile_name = os.path.basename(logfile)
    logfile_path = logfile
    if not isinstance(logfile_path, str):
        logfile_path = logfile_name

    for cur in Cursor(logfile, path=logfile_name):
        line = cur.line
        # TODO: Also extract year_month
        if line.startswith("Snapshot: version"):
                snapshot_version = line
                if "rpm " in snapshot_version:
                    t1 = snapshot_version.find("rpm ") + len("rpm ")
                    snapshot_version = snapshot_version[t1:]
                if snapshot_version.endswith('\n'):
                    snapshot_version = snapshot_version[:-1]
        if line.startswith("Systemtap translator/driver (version"):
                translator_driver_version = line
                if "rpm " in translator_driver_version:
                    t1 = translator_driver_version.find("rpm ") + len("rpm ")
                    translator_driver_version = translator_driver_version[t1:]
                if translator_driver_version.endswith('\n'):
                    translator_driver_version = translator_driver_version[:-1]
        if line.startswith("UNAME_MACHINE"):
                uname_machine_raw = line
        if line.startswith("Distro: "):
                distro_is = line
        if line.startswith("Native configuration is"):
                native_configuration_is = line
        if (line.startswith("Running ") and ".exp ..." in line) \
           or "Summary ===" in line:
                if running_test is not None:
                    # close Cursor range for this test
                    running_cur.line_end = cur.line_end-1
                    # XXX This is fairly usual across a couple of cases:
                    # if verbose \
                    #    and running_cur.line_start >= running_cur.line_end:
                    #     print("single line testcase {}".format(running_cur.to_str()))
                    last_test_cur.line_end = cur.line_end-1

                    # handle result of previous tests
                    # TODO: Perhaps sort *after* annotating?
                    #failed_subtests.sort() # XXX need to keep in the same order as log!
                    running_test = get_running_exp(running_test)

                    if consolidate_pass and last_test_passed:
                        testrun.add_testcase(name=running_test,
                                             outcome='PASS',
                                             origin_sum=running_cur)
                    elif last_test_passed:
                        # Report each passed_subtest individually.
                        for passed_subtest, outcome, cursor in passed_subtests:
                            testrun.add_testcase(name=running_test,
                                                 outcome=outcome,
                                                 subtest=passed_subtest,
                                                 origin_sum=cursor)
                    # Report all failed and untested subtests:
                    for failed_subtest, outcome, cursor in failed_subtests:
                        testrun.add_testcase(name=running_test,
                                             outcome=outcome,
                                             subtest=failed_subtest,
                                             origin_sum=cursor)

                running_test = line
                running_cur = Cursor(start=cur)
                last_test_cur = Cursor(start=cur); last_test_cur.line_start += 1
                last_test_passed = False
                last_test_failed = False
                failed_subtests = []
                passed_subtests = []
        # TODO: Handle other dejagnu outcomes if they show up.
        if line.startswith("FAIL: ") \
           or line.startswith("KFAIL: ") \
           or line.startswith("XFAIL: ") \
           or line.startswith("ERROR: tcl error sourcing"):
            last_test_cur.line_end = cur.line_end
            last_test_failed = True
            last_test_passed = False
            failed_subtests.append((line,
                                    check_mapping(line, test_outcome_map, start=True),
                                    last_test_cur))
            failed_subtests_summary += 1
            last_test_cur = Cursor(start=cur); last_test_cur.line_start += 1
            if all_cases is not None: all_cases.append(line)
        if line.startswith("UNTESTED: ") \
           or line.startswith("UNSUPPORTED: ") \
           or line.startswith("UNRESOLVED: "):
            last_test_cur.line_end = cur.line_end
            # don't update last_test_{passed,failed}
            failed_subtests.append((line,
                                    check_mapping(line, test_outcome_map, start=True),
                                    last_test_cur))
            # don't tally
            last_test_cur = Cursor(start=cur); last_test_cur.line_start += 1
            if all_cases is not None: all_cases.append(line)
        if line.startswith("PASS: ") \
           or line.startswith("XPASS: ") \
           or line.startswith("IPASS: "):
            last_test_cur.line_end = cur.line_end
            if not last_test_failed: # no fails so far
                last_test_passed = True
            if not consolidate_pass:
                passed_subtests.append((line,
                                        check_mapping(line, test_outcome_map, start=True),
                                        last_test_cur))
            passed_subtests_summary += 1
            last_test_cur = Cursor(start=cur); last_test_cur.line_start += 1
            if all_cases is not None: all_cases.append(line)
        # XXX Following check for rhel-{base,alt} is obscure,
        # but the logfiles lack better information, it seems.
        if "Session arch: " in line \
           and "release: 3." in line \
           and ".el7." in line:
            rhel7_base_seen = True
        if "Session arch: " in line \
           and "release: 4." in line \
           and ".el7." in line:
            rhel7_alt_seen = True

    if uname_machine is None:
        uname_machine = check_mapping(uname_machine_raw,
                                      uname_machine_map)
    if uname_machine is None:
        uname_machine = grok_architecture(native_configuration_is)
    if uname_machine is None:
        uname_machine = check_mapping(logfile_path,
                                      uname_machine_map)

    # XXX skip testrun.logfile_path = logfile_path
    if testrun.arch is None and uname_machine is not None: # XXX could set before parsing
        testrun.arch = uname_machine
    testrun.version = translator_driver_version if snapshot_version is None \
        else snapshot_version
    # XXX skip testrun.logfile_hash = get_hash_4_log(testrun)
    testrun.pass_count = passed_subtests_summary
    testrun.fail_count = failed_subtests_summary

    if 'osver' in testrun: # XXX could set before parsing
        default_osver = testrun.osver
    testrun.osver = None
    if testrun.osver is None:
        testrun.osver = check_regex_mapping(testrun.version,
                                            standard_osver_map)
    if testrun.osver is None:
        testrun.osver = check_regex_mapping(logfile_path,
                                            standard_osver_filename_map)
    if testrun.osver is None:
        testrun.osver = check_regex_mapping(distro_is,
                                            standard_distro_map)
    if testrun.osver is None:
        testrun.osver = default_osver
    if testrun.osver is None:
        print("WARNING: ignoring unknown distro_is", distro_is, file=sys.stderr)

    if rhel7_alt_seen:
        testrun.osver = "rhel-alt-7"

    skip = False
    skip_reason = ""
    # XXX skip checking logfile_path, logfile_hash
    if rhel7_base_seen and rhel7_alt_seen:
        skip = True
        skip_reason += "seen both rhel7_base and rhel7_alt, "
    elif testrun.arch is None:
        skip = True
        skip_reason = "unknown arch, "
    elif testrun.osver is None:
        skip = True
        skip_reason = "unknown osver, "
    if skip_reason.endswith(", "):
        skip_reason = skip_reason[:-2]
    if skip:
        print("WARNING: skipping logfile", logfile_path,
              "("+skip_reason+")", file=sys.stderr)
        testrun.problems = skip_reason
        return testrun # return None

    if testrun.version[-1] == ')':
        testrun.version = testrun.version[:-1]

    # XXX skip if "free-form-hash" and testrun.version != testrun.logfile_hash:
    #     print("WARNING: skipping logfile", testrun.logfile_path, "since free-form-hash wasn't set and", testrun.version, "differs from", testrun.logfile_hash, ".", file=sys.stderr)
    #     return None

    # XXX skip testrun.key = testrun.logfile_hash \
    #    + "+" + testrun.osver \
    #    + "+" + testrun.arch
    if verbose:
        print("Processed", logfile_path, testrun.version,
              testrun.arch, str(testrun.pass_count) + "pass",
              str(testrun.fail_count) + "fail", file=sys.stdout)

    return testrun

# XXX mcermak's idea when iterating multiple logfiles
# all_testruns.append(testrun)
# valid_arches.append(testrun.arch)
# valid_osvers.append(testrun.osver)
# valid_hashes.append(testrun.logfile_hash)
# foreach tc in testrun.testcases: valid_testcases.append(tc['name'])

def annotate_dejagnu_log(testrun, logfile, outcome_lines=[],
                         logfile_name=None, handle_reordering=False,
                         verbose=True):
    '''
    Annotate the testcases in a Testrun (presumably parsed from
    systemtap.sum) with their locations in a corresponding systemtap.log file.

    Here, outcome_lines is a list of all individual pass/fail lines in the file.
    '''
    if logfile_name is None:
        logfile_name = os.path.basename(logfile)

    # (1) Build a map of testcases.
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

    # (2) Parse the logfile and match its segments to the map of testcases.
    i = None # XXX index into testcases
    j = 0 # XXX index into outcome_lines
    running_test = None
    running_cur = None
    last_test_cur = None
    next_outcome = None # outcome of testcases[i]
    for cur in Cursor(logfile, path=logfile_name):
        line = cur.line
        if (line.startswith("Running ") and ".exp ..." in line) \
           or "Summary ===" in line:
            if running_test is not None:
                running_cur.line_end = cur.line_end-1
                last_test_cur.line_end = cur.line_end-1

                if running_test not in testcase_start and verbose:
                    # XXX Cut down on the chatter for normally-empty cases:
                    if not running_test.startswith('systemtap/notest.exp') \
                       and not running_test.startswith('systemtap.samples/examples.exp'):
                        print("WARNING: no testcases for {}@{}, skipping".format(running_test, running_cur.to_str()))
                else:
                    i = testcase_start[running_test]
                    if 'subtest' not in testcases[i]:
                        testcases[i]['origin_log'] = running_cur

            if "Summary ===" in line:
                break # no more testcases

            running_test = get_running_exp(line)
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

    return testrun

b = Bunsen()
if __name__ == '__main__':
    # TODO: enable the following default command line arguments
    #wd_defaults = ['systemtap.log', 'systemtap.sum']
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['logfile', 'sumfile'])
    # TODO: use Bunsen library to load testlogs
    # TODO: support reading testlogs from script's cwd or Bunsen repo
    #logfile = b.logfile(opts.logfile)
    #sumfile = b.logfile(opts.sumfile)
    testrun = Testrun()
    all_cases = []
    testrun = parse_dejagnu_log(testrun, opts.sumfile, all_cases=all_cases,
                                verbose=opts.verbose)
    testrun = annotate_dejagnu_log(testrun, opts.logfile, all_cases,
                                   verbose=opts.verbose)
    print(testrun.to_json(pretty=True))
