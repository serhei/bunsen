#!/usr/bin/env python3
# TODO from common.cmdline_args import default_args
info='''Compare the specified testruns.'''
cmdline_args = [
    ('baseline', None, '<bunsen_commit>', "baseline testrun to compare against"),
    ('latest', None, '<bunsen_commit>', "testrun to compare"),
    # TODO: support multiple testruns for latest?
    ('pretty', True, None, "pretty-print instead of showing JSON"),
]

# TODO: Add filtering option as in diff_commits.
#fail_outcomes = {'FAIL','KFAIL','XFAIL','UNTESTED','UNSUPPORTED','ERROR'}
fail_outcomes = {'FAIL','KFAIL','XFAIL','ERROR'}
# <- Most likely PASS->UNTESTED is not interesting, FAIL->UNTESTED is.
untested_outcomes = {'UNTESTED','UNSUPPORTED'}

import sys
from bunsen import Bunsen, Testrun

import re
from fnmatch import fnmatchcase
from common.format_output import get_formatter

def append_map(m, key, val):
    if key not in m: m[key] = []
    m[key].append(val)

def assign_map(m, key, val, warn_duplicate=True):
    if warn_duplicate and key in m and m[key] != val:
        print("WARNING: duplicate map values for key {}:".format(key))
        print("(1) EXISTING {}\n(2) NEW {}".format(m[key], val))
    m[key] = val

test_outcome_regex = re.compile(r"(K|I|X)?(PASS|FAIL|ERROR|UNTESTED|UNSUPPORTED|UNRESOLVED): (?P<content>.*)")

def subtest_name(s):
    # XXX Modify if you need to strip away any extra notes from subtest_name.
    m = test_outcome_regex.match(s)
    if m is not None:
        s = m.group('content')
    return s

def add_comparison(outdiffs, baseline_tc, latest_tc, assume_pass=False):
    baseline_outcome = baseline_tc['outcome'] if baseline_tc is not None \
        else "PASS" if assume_pass \
        else "(none)"
    latest_outcome = latest_tc['outcome'] if latest_tc is not None \
        else "PASS" if assume_pass \
        else "(none)"
    if baseline_outcome == latest_outcome:
        return
    #print ("<p>BASELINE {}:: {}<br/>LATEST {}:: {}</p>".format(baseline_outcome, baseline_tc, latest_outcome, latest_tc))
    if baseline_outcome in fail_outcomes and latest_outcome in fail_outcomes:
        # XXX skip FAIL->FAIL type changes:
        return
    if baseline_outcome not in fail_outcomes and latest_outcome not in fail_outcomes:
        # XXX skip PASS->PASS,PASS->UNTESTED type changes:
        return

    # simplify lookups
    if baseline_tc is None: baseline_tc = {}
    if latest_tc is None: latest_tc = {}

    tc2 = dict(latest_tc)
    if 'subtest' not in tc2 and 'subtest' in baseline_tc:
        tc2['subtest'] = baseline_tc['subtest']
    tc2['baseline_outcome'] = baseline_outcome
    if 'origin_sum' in baseline_tc:
        tc2['baseline_sum'] = baseline_tc['origin_sum']
    if 'origin_log' in baseline_tc:
        tc2['baseline_log'] = baseline_tc['origin_log']
    append_map(outdiffs, tc2['name'], tc2)

# TODOXXX replaces diff_testruns
def diff_testruns(baseline, latest, key=None):
    '''
    1st-order diff: testcases in latest that differ from baseline.
    '''
    # XXX Only writes 'testcases' field -- caller decides how to diff metadata.
    diff = Testrun()
    diff['bunsen_commit_id'] = latest['bunsen_commit_id']
    diff['baseline_bunsen_commit_id'] = baseline['bunsen_commit_id']
    diff_testcases = {} # name -> lst of tc comparisons (used to sort by exp)

    # XXX The logic on how to handle identically-named testcases can get tricky.
    #
    # 1. Some testcases have a subtest, others don't.
    #    In particular, parsing logs with consolidate_pass option
    #    will consolidate all PASS subtest into a single testcase entry.
    #
    #    In general, if a testcase name is present in the testrun,
    #    we consider all its subtests not explicitly mentioned as PASS.
    #    If a testcase name is not present in the testrun,
    #    we consider all its subtests not explicitly mentioned as (none).
    #
    # 2. DejaGNU log files can report several testcases
    #    with the same name and subtest. We don't consider changing
    #    the number of times a testcase reports the same outcome
    #    to be a significant difference.
    #
    # 3. Do pairwise comparisons of matching subtests in baseline and latest:
    #    - cur1->cur3 testcase.exp UNTESTED->FAIL: subtest1
    #    - cur2->cur3 testcase.exp FAIL->FAIL: subtest1
    #    In this example, the second comparison (FAIL->FAIL) is dropped,
    #    leaving a single UNTESTED->FAIL regression.

    tc1_entire_map = {} # name matching key -> tc with name but no subtest
    tc1_subtest_map = {} # name matching key -> lst of tc with name and subtest
    tc1_matching_map = {} # name+subtest -> lst of testcase

    for tc1 in baseline.testcases:
        name = tc1['name']
        diff_testcases[name] = [] # XXX ensure sane order in final diff
        if key is not None and not fnmatchcase(name, key): continue
        if 'subtest' in tc1:
            append_map(tc1_subtest_map, name, tc1)
            name_plus_subtest = name + '+' + subtest_name(tc1['subtest'])
            append_map(tc1_matching_map, name_plus_subtest, tc1)
        else:
            assign_map(tc1_entire_map, name, tc1)

    # First compare latest testcases with name and subtest:
    entire_names = set() # names of real or synthetic tcs
    latest_entire = [] # real or synthetic tcs with name but no subtest
    matched_subtests = set() # name+subtest already compared in tc2
    for tc2 in latest.testcases:
        name = tc2['name']
        if key is not None and not fnmatchcase(name, key): continue

        if 'subtest' not in tc2:
            latest_entire.append(tc2)
            entire_names.add(name)
            continue
        if name not in entire_names:
            entire_tc = {'name': name, 'outcome': 'PASS'}
            latest_entire.append(entire_tc)
            entire_names.add(name)

        name_plus_subtest = name + '+' + subtest_name(tc2['subtest'])
        matched_subtests.add(name_plus_subtest)

        baseline_entire = tc1_entire_map[name] if name in tc1_entire_map \
            else None
        tc1_has_entire = baseline_entire is not None
        baseline_subtests = tc1_subtest_map[name] if name in tc1_subtest_map \
            else []
        tc1_has_subtests = len(baseline_subtests) > 0
        matching_subtests = []
        if name_plus_subtest in tc1_matching_map:
            matching_subtests = tc1_matching_map[name_plus_subtest]
        tc1_has_matching = len(matching_subtests) > 0

        if tc1_has_matching:
            #print("<p>{} case1: matching subtests {}</p>".format(name, name_plus_subtest))
            for tc1 in matching_subtests:
                add_comparison(diff_testcases, tc1, tc2)
                # XXX already added to matched_subtests above
        if tc1_has_entire:
            #print("<p>{} case2: baseline entire, latest subtest {}</p>".format(name, name_plus_subtest))
            tc1 = baseline_entire; add_comparison(diff_testcases, tc1, tc2)
        if not tc1_has_matching and not tc1_has_entire:
            #print("<p>{} case3: baseline absent or non-matching subtest, latest subtest {}</p>".format(name, name_plus_subtest))
            # XXX tc1 is absent or has non-matching subtests
            assume_pass = tc1_has_subtests
            add_comparison(diff_testcases, None, tc2, assume_pass=assume_pass)
            #if tc1_has_subtests: print("<p>(baseline present, assuming subtest is PASS)</p>")

    # Then compare latest testcases with name but not subtest:
    for tc2 in latest_entire:
        name = tc2['name']
        #if key is not None and not fnmatchcase(name, key): continue

        baseline_entire = tc1_entire_map[name] if name in tc1_entire_map \
            else None
        tc1_has_entire = baseline_entire is not None
        baseline_subtests = tc1_subtest_map[name] if name in tc1_subtest_map \
            else []
        tc1_has_subtests = len(baseline_subtests) > 0
        # TODO: Need better caching: for name_plus_subtest in tc1_subtest_names[name]
        unmatched_subtests = []
        for name_plus_subtest in tc1_matching_map:
            if name_plus_subtest.startswith(name) \
               and name_plus_subtest not in matched_subtests:
                unmatched_subtests += tc1_matching_map[name_plus_subtest]
        tc1_has_unmatched = len(unmatched_subtests) > 0

        if tc1_has_entire:
            #print("<p>{} case4: matching entire</p>".format(name))
            tc1 = baseline_entire; add_comparison(diff_testcases, tc1, tc2)
        if tc1_has_unmatched:
            #print("<p>{} case5: baseline subtest, latest entire</p>".format(name))
            for tc1 in unmatched_subtests:
                add_comparison(diff_testcases, tc1, tc2)
        if not tc1_has_entire and not tc1_has_unmatched:
            #print("<p>{} case6: baseline absent, latest entire</p>".format(name))
            assume_pass = tc1_has_subtests
            add_comparison(diff_testcases, None, tc2, assume_pass=assume_pass)

    # Then sort results by .exp:
    for name, tcs in diff_testcases.items():
        diff.testcases += tcs
    return diff

def outcome_2or(tc):
    outcome1 = tc['baseline_outcome'] if 'baseline_outcome' in tc else None
    if outcome1 is None: outcome1 = 'null'
    outcome2 = tc['outcome'] if 'outcome' in tc else None
    if outcome2 is None: outcome2 = 'null'
    assert '->' not in outcome1 and '->' not in outcome2 # XXX Don't accidentally create 3or diffs!
    return outcome1 + '->' + outcome2

def add_2or_origins(tc, key, source_tc, cleanup=True):
    origins = {}
    if 'origin_sum' in source_tc:
        origins['origin_sum'] = source_tc['origin_sum']
    if 'origin_log' in source_tc:
        origins['origin_log'] = source_tc['origin_log']
    if len(origins) > 0:
        tc[key] = origins

    # XXX Also remove regular origin data:
    if cleanup:
        for old_key in ['origin_log','origin_sum',
                        'baseline_log','baseline_sum']:
            if old_key in tc:
                del tc[old_key]

def diff_2or_testcases(outdiff2, baseline_testcases, latest_tc):
    '''
    Pairwise compare latest_tc with baseline_testcases and append any
    differences to the 2nd-order diff outdiff2.
    '''
    for tc1 in baseline_testcases:
        outcome1 = outcome_2or(tc1)
        outcome2 = outcome_2or(latest_tc)
        if outcome2 != outcome1:
            tc2 = dict(latest_tc)
            if 'subtest' not in tc2 and 'subtest' in tc1:
                tc2['subtest'] = subtest_name(tc1['subtest'])
            tc2['outcome'] = outcome2
            tc2['baseline_outcome'] = outcome1
            add_2or_origins(tc2, 'origins', tc2)
            add_2or_origins(tc2, 'baseline_origins', tc1)
            outdiff2.testcases.append(tc2)

# TODO: REWRITE CONSISTENTLY WITH diff_testcases
def diff_2or(diff_baseline, diff_latest, key=None):
    '''
    2nd-order diff: changes in diff_latest that
    don't also appear in diff_baseline.
    '''
    # XXX Only writes 'testcases' field -- caller decides how to diff metadata.
    diff2 = Testrun()
    diff2['bunsen_commit_ids'] = [diff_latest['baseline_bunsen_commit_id'],
                                  diff_latest['bunsen_commit_id']]
    diff2['baseline_commit_ids'] = [diff_baseline['baseline_bunsen_commit_id'],
                                    diff_baseline['bunsen_commit_id']]

    tc1_map = {} # name -> lst of testcase
    tc1_name_map = {} # name -> lst of testcase (only if subtest missing)
    tc1_subtest_map = {} # name+subtest -> lst of testcase

    for tc1 in diff_baseline.testcases:
        name = tc1['name']
        if key is not None and not fnmatchcase(name, key): continue
        append_map(tc1_map, name, tc1)
        if 'subtest' in tc1:
            name_plus_subtest = name + '+' + subtest_name(tc1['subtest'])
            append_map(tc1_subtest_map, name_plus_subtest, tc1)
        else:
            append_map(tc1_name_map, name, tc1)

    for tc2 in diff_latest.testcases:
        name = tc2['name']
        if key is not None and not fnmatchcase(name, key): continue
        name_plus_subtest = None
        if 'subtest' in tc2:
            name_plus_subtest = name + '+' + subtest_name(tc2['subtest'])
        if name_plus_subtest is not None and \
           name_plus_subtest in tc1_subtest_map:
            diff_2or_testcases(diff2, tc1_subtest_map[name_plus_subtest], tc2)
        elif name in tc1_name_map: # XXX no subtest
            diff_2or_testcases(diff2, tc1_name_map[name], tc2)
        elif name in tc1_map: # XXX subtest in tc1, no subtest in tc2
            diff_2or_testcases(diff2, tc1_map[name], tc2)
        else: # XXX tc2 has no equivalent in baseline, use None as baseline outcome
            outcome2 = outcome_2or(tc2)
            if outcome2 != 'null->null':
                tc2 = dict(tc2)
                tc2['outcome'] = outcome_2or(tc2)
                tc2['baseline_outcome'] = None
                add_2or_origins(tc2, 'origins', tc2)
                diff2.testcases.append(tc2)

    return diff2

b = Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['baseline','latest'])
    out = get_formatter(b, opts)
    baseline = b.testrun(opts.baseline)
    testrun = b.testrun(opts.latest)

    testdiff = diff_testruns(baseline, testrun)
    if opts.pretty == False:
        print(testdiff.to_json(pretty=True))
    else:
        out.message(baseline=opts.baseline, latest=opts.latest)
        out.show_testrun(baseline, header_fields=['kind'], kind='baseline')
        out.show_testrun(testrun, header_fields=['kind'], kind='latest')
        out.section()
        # TODO: new section + header for each major .exp? + consolidate simple .exps?
        for tc in testdiff.testcases:
            out.show_testcase(testdiff, tc)
        out.finish()
