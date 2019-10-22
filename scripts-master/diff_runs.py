#!/usr/bin/env python3
# Compare the testrun <testrun_id> against the baseline testrun <baseline_id>.
usage = "diff_runs.py <baseline_id> <testrun_id>"

# TODO: Suggested options:
# - pretty-print or show JSON

import sys
from bunsen import Bunsen, Testrun

def append_map(m, key, val):
    if key not in m: m[key] = []
    m[key].append(val)

def assign_map(m, key, val, warn_duplicate=True):
    if warn_duplicate and key in m and m[key] != val:
        print("WARNING: duplicate map values for key {}:".format(key))
        print("(1) EXISTING {}\n(2) NEW {}".format(m[key], val))
    m[key] = val

def subtest_name(str):
    # XXX Modify if you need to strip away any extra notes from subtest_name.
    return str

def diff_testcases(outdiff, baseline_testcases, latest_tc):
    '''
    Pairwise compare latest_tc with baseline_testcases
    and append any differences to outdiff.
    '''
    for tc1 in baseline_testcases:
        if latest_tc['outcome'] != tc1['outcome']:
            tc2 = dict(latest_tc)
            if 'subtest' not in tc2 and 'subtest' in tc1:
                tc2['subtest'] = subtest_name(tc1['subtest'])
            tc2['baseline_outcome'] = tc1['outcome']
            if 'origin_sum' in tc1:
                tc2['baseline_sum'] = tc1['origin_sum']
            if 'origin_log' in tc1:
                tc2['baseline_log'] = tc1['origin_log']
            outdiff.testcases.append(tc2)

def diff_testruns(baseline, latest):
    '''
    1st-order diff: testcases in latest that
    differ from baseline.
    '''
    # XXX Only writes 'testcases' field -- caller decides how to diff metadata.
    diff = Testrun()
    diff['bunsen_commit_id'] = latest['bunsen_commit_id']
    diff['baseline_bunsen_commit_id'] = baseline['bunsen_commit_id']

    # XXX The logic on how to handle identically-named testcases can get tricky.
    #
    # Supposing we have:
    # - baseline@cur1 testcase.exp XFAIL: subtest1
    # - baseline@cur2 testcase.exp FAIL: subtest1
    # - latest@cur3 testcase.exp FAIL: subtest1
    #
    # We do pairwise comparisons of matching subtests in baseline and latest:
    # - cur1->cur3 testcase.exp XFAIL->FAIL: subtest1
    # - cur2->cur3 testcase.exp FAIL->FAIL: subtest1
    #
    # The second comparison (FAIL->FAIL) is dropped, leaving a single
    # XFAIL->FAIL regression.
    #
    # Note this means merely changing the number of times a subtest
    # reports failure is not considered a significant difference.
    tc1_map = {} # name -> lst of testcase
    tc1_name_map = {} # name -> lst of testcase (only if subtest missing)
    tc1_subtest_map = {} # name+subtest -> lst of testcase

    for tc1 in baseline.testcases:
        name = tc1['name']
        append_map(tc1_map, name, tc1)
        if 'subtest' in tc1:
            name_plus_subtest = name + '+' + subtest_name(tc1['subtest'])
            append_map(tc1_subtest_map, name_plus_subtest, tc1)
        else:
            append_map(tc1_name_map, name, tc1)

    for tc2 in latest.testcases:
        name = tc2['name']
        name_plus_subtest = None
        if 'subtest' in tc2:
            name_plus_subtest = name + '+' + subtest_name(tc2['subtest'])
        if name_plus_subtest is not None and \
           name_plus_subtest in tc1_subtest_map:
            diff_testcases(diff, tc1_subtest_map[name_plus_subtest], tc2)
        elif name in tc1_name_map: # XXX no subtest
            diff_testcases(diff, tc1_name_map[name], tc2)
        elif name in tc1_map: # XXX subtest in tc1, no subtest in tc2
            diff_testcases(diff, tc1_map[name], tc2)
        else: # XXX tc2 has no equivalent in baseline, use None as baseline outcome
            if tc2['outcome'] != None:
                tc2 = dict(tc2)
                tc2['baseline_outcome'] = None
                diff.testcases.append(tc2)

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

def diff_2or(diff_baseline, diff_latest):
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
        append_map(tc1_map, name, tc1)
        if 'subtest' in tc1:
            name_plus_subtest = name + '+' + subtest_name(tc1['subtest'])
            append_map(tc1_subtest_map, name_plus_subtest, tc1)
        else:
            append_map(tc1_name_map, name, tc1)

    for tc2 in diff_latest.testcases:
        name = tc2['name']
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
    # TODO: Handle tag:commit format for baseline_id, testrun_id in b.testrun().
    baseline_id, testrun_id = b.cmdline_args(sys.argv, 2, usage=usage)
    baseline = b.testrun(baseline_id)
    testrun = b.testrun(testrun_id)
    testdiff = diff_testruns(baseline, testrun)
    print(testdiff.to_json(pretty=True))
