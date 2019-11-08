#!/usr/bin/env python3
# Compare all testruns for two commits in the Git repo source_repo
# and summarize regressions.
usage = "diff_commits.py [baseline=]<source_commit> [latest=]<source_commit> [[source_repo=]<path>]\n" \
        "                       [exclude={new,unresolved,f2f}] [pretty=yes|no|html]"
default_args = {'source_repo':None, # obtain commits from source_repo
                'baseline':None,    # baseline commit to compare against
                'latest':None,      # commit to compare
                'exclude':None,     # list of exclusions (see below)
                'pretty':True,      # pretty-print info instead of showing JSON
               }

# List of exclusions 'new', 'unresolved', 'f2f' (XXX values overridden by opts):
filter_new = False # XXX e.g. null->PASS type of regressions
filter_unresolved = False # XXX UNRESOLVED type of outcome
filter_f2f = False # XXX e.g. KFAIL->FAIL type of regressions

import sys
import bunsen
from git import Repo

import tqdm # for find_testruns()

from common.format_output import get_formatter, field_summary, html_field_summary

from list_commits import get_source_commit
from diff_runs import append_map, subtest_name, diff_testruns, diff_2or

# XXX Common wisdom around XFAIL is a bit strange, but the dejagnu doc states:
#
#   "A test failed, but it was expected to fail.
#    This result indicates no change in a known bug."
#
# So I will tentatively classify this as a fail.
fail_type_outcomes = {'FAIL', 'KFAIL', 'XFAIL'}
f2f_type_outcomes = set()
for f1 in fail_type_outcomes:
    for f2 in fail_type_outcomes:
        f2f_type_outcomes.add(f1+'->'+f2)

# XXX global for find_testruns
num_testruns = None

def find_testruns(b, source_hexsha, msg='Finding testruns'):
    global num_testruns
    if num_testruns is None: # XXX purely for progress
        num_testruns = 0
        for tag in b.tags:
            for testrun_summary in b.testruns(tag):
                num_testruns += 1
    testruns = []
    for tag in b.tags:
        progress = tqdm.tqdm(iterable=None, desc=msg,
                             total=num_testruns, leave=True, unit='run')
        for testrun_summary in b.testruns(tag):
            hexsha = get_source_commit(testrun_summary)
            if hexsha.startswith(source_hexsha) or source_hexsha.startswith(hexsha):
                testrun = b.testrun(testrun_summary)
                testruns.append(testrun)
            progress.update(n=1)
        progress.close()
    return testruns

def find_summary_fields(testrun, summary_fields, summary_vals):
    excluded = {'pass_count', 'fail_count', 'year_month', 'testcases'}
               # 'source_commit', 'version'} # XXX implied by choice of commit?
    found_fields = set()
    for field in testrun:
        if field in excluded or field.startswith('bunsen_'):
            continue
        found_fields.add(field)
        if field not in summary_vals:
            summary_vals[field] = testrun[field]
        elif summary_vals[field] != testrun[field]:
            summary_vals[field] = None # XXX Mark as not all identical.
    if len(summary_fields) == 0:
        summary_fields.update(found_fields)
    else:
        summary_fields.intersection_update(found_fields)
        if len(summary_fields) == 0:
            raise ValueError('No metadata overlap between selected testruns.')

def summary_tuple(testrun, summary_fields, exclude=set()):
    vals = []
    for field in summary_fields:
        if field in exclude: continue
        vals.append(testrun[field])
    return tuple(vals)

# TODO: change the 'comparison'/'baseline_comparison' format in 2or diffs
# to match the output of get_comparison() for 1or diffs
def get_comparison(diff):
    if 'summary_tuple' in diff and 'baseline_summary_tuple' in diff: # 1or diff
        comp = {'summary_tuple': diff['summary_tuple'],
                'baseline_summary_tuple': diff['baseline_summary_tuple']}
    elif 'comparison' in diff and 'baseline_comparison' in diff: # 2or diff
        comp = {'summary_tuple': diff['comparison'][1],
                'baseline_summary_tuple': diff['comparison'][0],
                'minus_summary_tuple': diff['baseline_comparison'][1],
                'minus_baseline_summary_tuple': diff['baseline_comparison'][0]}
    else:
        assert False # XXX unknown diff format
    return comp

# XXX for pretty-printing
def make_summary_str(opts, summary_tuple, summary_fields):
    # TODO: Avoid needless opts.pretty == 'html' checking?
    d = {}
    summary_fields = list(summary_fields)
    for i in range(len(summary_fields)):
        if summary_fields[i] == 'source_commit':
            continue # XXX this is just clutter
        d[summary_fields[i]] = summary_tuple[i]
    if opts.pretty == 'html':
        return "(" + html_field_summary(d) + ")"
    else:
        return "(" + field_summary(d) + ")"

# XXX for pretty-printing
def make_comparison_str(opts, comparison, summary_fields, single=False):
    # TODO: Avoid needless opts.pretty == 'html' checking?
    s = ""
    if not single and opts.pretty == 'html':
        s += "<li>"
    elif not single and opts.pretty != 'html':
        s += "  - "
    d = {}
    s += make_summary_str(opts, comparison['baseline_summary_tuple'], summary_fields)
    s += " -> " + make_summary_str(opts, comparison['summary_tuple'], summary_fields)
    if 'minus_baseline_summary_tuple' in comparison:
        assert 'minus_summary_tuple' in comparison
        s += " minus "
        s += make_summary_str(opts, comparison['minus_baseline_summary_tuple'], summary_fields)
        s += " -> " + make_summary_str(opts, comparison['minus_summary_tuple'], summary_fields)
    if not single and opts.pretty == 'html': s += "</li>"
    return s

# XXX for pretty-printing
def show_combination(opts, out, n_regressions, combination, summary_fields):
    # TODO: Avoid needless opts.pretty == 'html' checking?
    s = "Found {} regressions for".format(n_regressions)
    single = len(combination) <= 1
    if not single:
        out.message(s+":")
        s = ""
    else:
        s += " "
    if opts.pretty == 'html' and not single:
        out.message("<ul>", raw=True)
    for comparison in combination:
        s += make_comparison_str(opts, comparison, summary_fields, single=single)
        out.message(s, raw=not single)
        s = ""
    if opts.pretty == 'html' and not single:
        out.message("</ul>", raw=True)

# XXX hack for consistent indexing
def make_combination_key(combination):
    combination_strs = []
    for comp in combination:
        combination_strs.append(str(comp))
    return str(sorted(combination_strs))

# XXX for consistent indexing
def get_tc_key(tc):
    key = ''
    key += tc['name'] + '+'
    key += ('null' if 'subtest' not in tc else tc['subtest']) + '+'
    key += tc['outcome'] + '+'
    key += ('null' if tc['baseline_outcome'] is None else tc['baseline_outcome'])
    return key

# XXX only the fields for get_tc_key
def strip_tc(tc):
    tc2 = {}
    if 'name' in tc: tc2['name'] = tc['name']
    if 'subtest' in tc: tc2['subtest'] = tc['subtest']
    if 'outcome' in tc: tc2['outcome'] = tc['outcome']
    if 'baseline_outcome' in tc: tc2['baseline_outcome'] = tc['baseline_outcome']
    return tc2
    
# TODO: Modify Bunsen Testrun class to support this directly and to
# use customizations from the testcase's original Testrun.
def testcase_to_json(tc):
    dummy_testrun = bunsen.Testrun()
    return dummy_testrun.testcase_to_json(tc)

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, usage=usage,
                          required_args=['baseline','latest'],
                          optional_args=['source_repo'],
                          defaults=default_args)
    out = get_formatter(b, opts)
    repo = Repo(opts.source_repo)

    exclusions = opts.get_list('exclude', default=['new','unresolved'])
    filter_new = 'new' in exclusions
    filter_unresolved = 'unresolved' in exclusions
    filter_f2f = 'f2f' in exclusions

    # (1a) find all testruns for specified commits
    baseline_runs = find_testruns(b, opts.baseline,
        msg='Finding testruns for baseline {}'.format(opts.baseline))
    latest_runs = find_testruns(b, opts.latest,
        msg='Finding testruns for latest {}'.format(opts.latest))

    # (1b) find summary fields present in all testruns
    summary_fields = set()
    summary_vals = {}
    for testrun in baseline_runs:
        find_summary_fields(testrun, summary_fields, summary_vals)
    for testrun in latest_runs:
        find_summary_fields(testrun, summary_fields, summary_vals)

    # for displaying testruns:
    header_fields = list(summary_fields - {'source_branch', 'version'})

    # (1c) trim summary fields identical in all testruns
    for field in set(summary_fields):
        if summary_vals[field] is not None:
            summary_fields.discard(field)

    if True:
        out.message(baseline=opts.baseline, latest=opts.latest)
        for testrun in baseline_runs:
            out.show_testrun(testrun, header_fields=header_fields, kind='baseline',
                             show_all_details=False)
        out.section(minor=True)
        for testrun in latest_runs:
            out.show_testrun(testrun, header_fields=header_fields, kind='latest',
                             show_all_details=False)

    # (2a) build maps of metadata->testrun to match testruns with similar configurations
    # summary_key := (summary_tuple minus source_commit, version)
    baseline_map = {} # summary_key -> testrun
    for testrun in baseline_runs:
        t = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
        assert t not in baseline_map # XXX would be kind of unforeseen
        baseline_map[t] = testrun
    latest_map = {} # summary_key -> testrun
    for testrun in latest_runs:
        t = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
        assert t not in latest_map # XXX would be kind of unforeseen
        latest_map[t] = testrun

    # (2b) identify baseline testrun for baseline_commit
    # Everything will be compared relative to this single baseline.
    #
    # Reasoning
    # - prefer largest number of pass
    # - prefer tuple present in both baseline_runs and latest_runs
    #   (minus source_commit, version data)
    best_overall = None
    best_with_latest = None
    for testrun in baseline_runs:
        t = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
        if t in latest_map:
            if best_with_latest is None \
               or int(testrun['pass_count']) > int(best_with_latest['pass_count']):
                best_with_latest = testrun
        if best_overall is None \
           or int(testrun['pass_count']) > int(best_overall['pass_count']):
            best_overall = testrun

    if best_with_latest is not None:
        best_overall = best_with_latest

    out.section()
    out.message("Found {} baseline, {} latest runs, preferred baseline {}:" \
                .format(len(baseline_runs), len(latest_runs),
                        best_overall.bunsen_commit_id))
    out.show_testrun(best_overall, header_fields=header_fields, kind='baseline',
                     show_all_details=False)

    # (3) Compare relevant baseline & latest logs relative to baseline:
    version_diffs = []    # regressions in latest wrt baseline
    regression_diffs = [] # between latest targets which don't appear in baseline
    t1 = summary_tuple(best_overall, summary_fields)
    t1_exclude = summary_tuple(best_overall, summary_fields, exclude={'source_commit','version'})
    for testrun in latest_runs:
        t2 = summary_tuple(testrun, summary_fields)
        t2_exclude = summary_tuple(testrun, summary_fields, exclude={'source_commit','version'})
        # XXX Try to identify a baseline run matching t2_exclude.
        # This ensures that version_diffs and regression_diffs will not overlap.
        # TODO: Ideally, we would compare both baseline *and* best_overall.
        baseline, preferred_t1 = best_overall, t1
        if t2_exclude in baseline_map:
            baseline = baseline_map[t2_exclude]
            preferred_t1 = summary_tuple(baseline, summary_fields)
        diff = diff_testruns(baseline, testrun)
        diff.diff_order = 1
        diff.baseline_summary_tuple = list(preferred_t1)
        diff.summary_tuple = list(t2)
        if len(diff.testcases) > 0:
            version_diffs.append(diff)
        #print("DEBUG COMPARED", str(preferred_t1)+"->"+str(t2))
        #print(diff.to_json(pretty=True))
    for baseline_testrun in baseline_runs:
        t1_new = summary_tuple(baseline_testrun, summary_fields)
        t1_new_exclude = summary_tuple(baseline_testrun, summary_fields, exclude={'source_commit','version'})
        if t1_exclude not in latest_map or t1_new_exclude not in latest_map:
            continue # did not find a matching comparison in latest_runs
        latest_baseline, latest_testrun = latest_map[t1_exclude], latest_map[t1_new_exclude]
        t2 = summary_tuple(latest_baseline, summary_fields)
        t2_new = summary_tuple(latest_testrun, summary_fields)
        diff_baseline = diff_testruns(best_overall, baseline_testrun)
        diff_latest = diff_testruns(latest_baseline, latest_testrun)
        diff2 = diff_2or(diff_baseline, diff_latest)
        diff2.diff_order = 2
        diff2.baseline_comparison = [list(t1), list(t1_new)]
        diff2.comparison = [list(t2), list(t2_new)]
        if len(diff.testcases) > 0:
            regression_diffs.append(diff2)
        #print("DEBUG COMPARED", str(t2)+"->"+str(t2_new),
        #      "MINUS", str(t1)+"->"+str(t1_new))
        #print(diff2.to_json(pretty=True))

    # (4) Determine which comparisons each regression appears in.
    # Do this by preparing a merged regression report.

    # tc_key := name+subtest+outcome+baseline_outcome
    version_tcs_map = {} # tc_key -> (tc, comparison)
    regression_tcs_map = {} # tc_key -> (tc, comparison)
    for diff in version_diffs:
        comparison = get_comparison(diff)
        for tc in diff.testcases:
            # XXX: skip clutter
            if filter_new and tc['baseline_outcome'] is None:
                continue
            if filter_unresolved and (tc['outcome'] == 'UNRESOLVED' \
               or tc['baseline_outcome'] == 'UNRESOLVED'):
                continue
            if filter_f2f and tc['outcome'] in fail_type_outcomes \
               and tc['baseline_outcome'] in fail_type_outcomes:
                continue

            tc_key = get_tc_key(tc)
            append_map(version_tcs_map, tc_key, (tc, comparison))
    for diff in regression_diffs:
        comparison = get_comparison(diff)
        for tc in diff.testcases:
            # XXX: skip clutter
            if filter_new and tc['baseline_outcome'] is None:
                continue
            if filter_unresolved and ('UNRESOLVED' in tc['outcome'] \
               or 'UNRESOLVED' in tc['baseline_outcome']):
                continue
            if filter_f2f and tc['outcome'] in f2f_type_outcomes \
               and tc['baseline_outcome'] in f2f_type_outcomes:
                continue

            tc_key = get_tc_key(tc)
            append_map(regression_tcs_map, tc_key, (tc, comparison))

    # comparison := dict with keys {'summary_tuple', 'baseline_summary_tuple',
    #                'minus_summary_tuple', 'minus_baseline_summary_tuple'}
    # combination := lst of comparison
    # combination_key := sorted lst of comparison -> json
    version_combos = {} # combination_key -> combination
    version_testcases = {} # combination_key -> lst of testcase
    regression_combos = {} # combination_key -> combination
    regression_testcases = {} # combination_key -> lst of testcase

    for tc_key, tc_combos in version_tcs_map.items():
        if len(tc_combos) == 0: continue
        base_tc = None
        combination = []
        for tc, comparison in tc_combos:
            if base_tc is None:
                # TODO: this discards metadata such as cursors -- figure out how to keep?
                base_tc = strip_tc(tc)
            if comparison in combination:
                continue # don't add the same one twice
            combination.append(comparison)
        combination_key = make_combination_key(combination)
        if combination_key not in version_combos:
            version_combos[combination_key] = combination
        append_map(version_testcases, combination_key, base_tc)
    for tc_key, tc_combos in regression_tcs_map.items():
        if len(tc_combos) == 0: continue
        base_tc = None
        combination = []
        for tc, comparison in tc_combos:
            if base_tc is None:
                # TODO: this discards metadata such as cursors -- figure out how to keep?
                base_tc = strip_tc(tc)
            if comparison in combination:
                continue # don't add the same one twice
            combination.append(comparison)
        combination_key = make_combination_key(combination)
        if combination_key not in regression_combos:
            regression_combos[combination_key] = combination
        append_map(regression_testcases, combination_key, base_tc)

    # (5) For each combination of comparisons, print the regressions:
    out.section()
    out.message("Regressions by version")
    # TODO: Add 'compact' option?
    for combination_key, combination in version_combos.items():
        n_regressions = len(version_testcases[combination_key])
        if n_regressions == 0: continue
        out.section(minor=True)
        show_combination(opts, out, n_regressions, combination, summary_fields)
        for tc in version_testcases[combination_key]:
            out.show_testcase(None, tc) # XXX no testrun
    out.section()
    out.message("Regressions by configuration")
    # TODO: Add 'compact' option?
    for combination_key, combination in regression_combos.items():
        n_regressions = len(version_testcases[combination_key])
        if n_regressions == 0: continue
        out.section(minor=True)
        show_combination(opts, out, n_regressions, combination, summary_fields)
        for tc in version_testcases[combination_key]:
            out.show_testcase(None, tc) # XXX no testrun

    out.finish()
