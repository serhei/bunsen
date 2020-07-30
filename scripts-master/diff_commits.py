#!/usr/bin/env python3
# TODO from common.cmdline_args import default_args
info='''Compare all testruns for two commits in the Git repo source_repo
and summarize regressions.'''
cmdline_args = [
    ('source_repo', None, '<path>',
     "obtain commits from source repo <path>"),
    ('baseline', None, '<source_commit>', "baseline commit to compare against"),
    ('latest', None, '<source_commit>', "commit to compare"),
    ('exclude', None, '{new,unresolved,f2f}',
     "list of exclusions (see source code of script)"),
    ('pretty', True, None,
     "pretty-print instead of showing JSON"),
    # TODO ('diff_baseline', True, None, "diff against probable baseline if same configuration is missing"),
]

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
    '''Find all testruns which test source commit <source_hexsha>.'''
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

def _find_summary_fields(testrun, summary_fields, summary_vals):
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

# XXX return pair (header_fields, summary_fields)
def index_summary_fields(iterable):
    '''Select the fields used to summarize the configuration for a testrun.'''
    summary_fields = set()
    summary_vals = {}
    for testrun in iterable:
        _find_summary_fields(testrun, summary_fields, summary_vals)

    # for displaying testruns:
    header_fields = list(summary_fields - {'source_branch','version'})

    # trim summary fields identical in all testruns
    for field in set(summary_fields):
        if summary_vals[field] is not None:
            summary_fields.discard(field)

    return header_fields, list(summary_fields) # XXX important to preserve order

def get_summary(testrun, summary_fields, exclude_version=True):
    '''Summarize the configuration for a testrun.

    Used to match testruns with similar configurations across
    different source commits.
    '''
    excluded = {'source_commit', 'version'}
    d = {}
    for field in summary_fields:
        if exclude_version and field in excluded:
            continue
        if field not in testrun:
            d[field] = '<unknown>'
            continue
        d[field] = testrun[field]
    return d

# TODO: change the 'comparison'/'baseline_comparison' format in 2or diffs
# to match the output of get_comparison() for 1or diffs
def get_comparison(diff):
    '''Describe which configurations were being compared in a diff.'''
    if 'latest_summary' in diff and 'baseline_summary' in diff: # 1or diff
        comp = {'summary': diff['latest_summary'],
                'baseline_summary': diff['baseline_summary']}
    elif 'latest_comparison' in diff and 'baseline_comparison' in diff: # 2or diff
        comp = {'summary': diff['latest_comparison'][1],
                'baseline_summary': diff['latest_comparison'][0],
                'minus_summary': diff['baseline_comparison'][1],
                'minus_baseline_summary': diff['baseline_comparison'][0]}
    else:
        assert False # XXX unknown diff format
    return comp

def make_summary_str(summary, exclude_version=True, html=False):
    excluded = {'source_commit', 'version'}
    for k in excluded:
        if exclude_version and k in summary:
            del summary[k]
    if html:
        return "(" + html_field_summary(summary) + ")"
    else:
        return "(" + field_summary(summary) + ")"

def make_comparison_str(comparison, single=False, html=False):
    s = ""
    if not single and html:
        s += "<li>"
    elif not single and html:
        s += "  - "
    s += make_summary_str(comparison['baseline_summary'], html=html)
    s += " -> " + make_summary_str(comparison['summary'], html=html)
    if 'minus_baseline_summary' in comparison:
        assert 'minus_summary' in comparison
        s += " MINUS "
        s += make_summary_str(comparison['minus_baseline_summary'], html=html)
        s += " -> " + make_summary_str(comparison['summary'], html=html)
    if not single and html:
        s += "</li>"
    return s

# XXX for pretty-printing
def show_combination(opts, out, n_regressions, combination):
    s = "Found {} regressions for".format(n_regressions)
    single = len(combination) <= 1
    to_html = opts.pretty == 'html'
    if not single:
        out.message(s+":")
        s = ""
    else:
        s += " "
    if not single and html:
        out.message("<ul>", raw=True)
    for comparison in combination:
        s += make_comparison_str(comparison, single=single, html=to_html)
        out.message(s, raw=not single)
        s = ""
    if not single and html:
        out.message("</ul>", raw=True)

# XXX for consistent indexing
def get_tc_key(tc, strip_outcome=False):
    '''Create a key name+subtest+outcome+baseline_outcome for consistent indexing.'''
    key = ''
    key += tc['name'] + '+'
    key += ('null' if 'subtest' not in tc else tc['subtest']) + '+'
    if not strip_outcome:
        key += ('null' if tc['outcome'] is None else tc['outcome']) + '+'
        key += ('null' if tc['baseline_outcome'] is None else tc['baseline_outcome'])
    return key

def get_summary_key(summary):
    '''Create a key listing all summary elements for consistent indexing.'''
    # XXX Hack to avoid worrying about stable dict iteration order.
    return str(sorted(summary.items()))

def get_comparison_key(comparison):
    '''Create a key listing all comparison elements for consistent indexing.'''
    # TODO: Perhaps just look at the specific (known-to-exist) keys?
    comparison_strs = []
    for k, v in comparison.items():
        s = get_summary_key(v) if isinstance(v,dict) else str(v)
        comparison_strs.append(str((k,s)))
    # XXX Hack to avoid worrying about stable dict iteration order.
    return str(sorted(comparison_strs))

def get_combination_key(combination):
    '''Create a key listing all combinations for consistent indexing.'''
    combination_strs = []
    for comparison in combination:
        combination_strs.append(get_comparison_key(comparison))
    # XXX Hack to avoid worrying about stable dict iteration order.
    return str(sorted(combination_strs))

# XXX only the fields for get_tc_key
def strip_tc(tc, keep=set()):
    '''Remove all fields except those present in set keep or used by get_tc_key().'''
    tc2 = {}
    if 'name' in tc: tc2['name'] = tc['name']
    if 'subtest' in tc: tc2['subtest'] = tc['subtest']
    if 'outcome' in tc: tc2['outcome'] = tc['outcome']
    if 'baseline_outcome' in tc: tc2['baseline_outcome'] = tc['baseline_outcome']
    for k in keep:
        if k in tc: tc2[k] = tc[k]
    return tc2

def diff_all_testruns(baseline_runs, latest_runs,
                      summary_fields, diff_baseline=True,
                      diff_previous=None, # summary_key -> testrun
                      diff_same=False, key=None,
                      out=None):
    # (2a) build maps of metadata->testrun to match testruns with similar configurations
    # summary_key := (summary minus source_commit, version)
    baseline_map = {} # summary_key -> testrun
    for testrun in baseline_runs:
        t = get_summary_key(get_summary(testrun, summary_fields))
        # XXX a sign of duplicate runs in the repo :(
        #assert t not in baseline_map # XXX would be kind of unforeseen
        baseline_map[t] = testrun
    previous_map = {} # summary_key -> testrun
    if diff_previous is not None:
        previous_map = diff_previous
    latest_map = {} # summary_key -> testrun
    for testrun in latest_runs:
        t = get_summary_key(get_summary(testrun, summary_fields))
        # XXX a sign of duplicate runs in the repo :(
        #assert t not in latest_map # XXX would be kind of unforeseen
        latest_map[t] = testrun

    # (2b) identify baseline testrun from baseline_runs
    # If diff_baseline is enabled, everything will be
    # compared relative to this single baseline.
    #
    # Reasoning
    # - prefer largest number of pass
    # - prefer tuple present in both baseline_runs and latest_runs
    #   (minus source_commit, version data)
    best_overall = None
    best_with_latest = None
    for testrun in baseline_runs:
        t = get_summary_key(get_summary(testrun, summary_fields))
        if t in latest_map:
            if best_with_latest is None \
               or int(testrun['pass_count']) > int(best_with_latest['pass_count']):
                best_with_latest = testrun
        if best_overall is None \
           or int(testrun['pass_count']) > int(best_overall['pass_count']):
            best_overall = testrun

    #if best_with_latest is not None:
    #    best_overall = best_with_latest

    # TODO: Consider splitting at this point into pick_comparisons(),
    # diff_comparisons() to avoid having to pass out into this function.
    if out is not None:
        out.section()
        out.message("Found {} baseline, {} latest runs, preferred baseline {}:" \
                    .format(len(baseline_runs), len(latest_runs),
                            best_overall.bunsen_commit_id))
        out.show_testrun(best_overall, header_fields=header_fields, kind='baseline',
                         show_all_details=False)

    # (3) Compare relevant baseline & latest logs relative to baseline:
    version_diffs = []    # regressions in latest wrt baseline
    regression_diffs = [] # between latest targets which don't appear in baseline
    t1 = get_summary(best_overall, summary_fields, exclude_version=False)
    t1_exclude = get_summary(best_overall, summary_fields)
    t1_key = get_summary_key(t1_exclude)
    for testrun in latest_runs:
        t2 = get_summary(testrun, summary_fields, exclude_version=False)
        t2_exclude = get_summary(testrun, summary_fields)
        t2_key = get_summary_key(t2_exclude)
        # XXX Try to identify a baseline run matching t2_exclude.
        # This ensures that version_diffs and regression_diffs will not overlap.
        # TODO: Ideally, we would compare both baseline *and* best_overall.
        baseline, preferred_t1 = None, None
        if diff_baseline:
            baseline = best_overall
            preferred_t1 = t1
        if t2_key in previous_map:
            baseline = previous_map[t2_key]
            preferred_t1 = get_summary(baseline, summary_fields,
                                       exclude_version=False)
        if t2_key in baseline_map:
            baseline = baseline_map[t2_key]
            preferred_t1 = get_summary(baseline, summary_fields,
                                       exclude_version=False)
        if baseline is None:
            continue
        diff = diff_testruns(baseline, testrun, key=key)
        diff.diff_order = 1
        diff.baseline_summary = preferred_t1
        diff.latest_summary = t2
        if len(diff.testcases) > 0:
            version_diffs.append(diff)
        #print("DEBUG COMPARED", str(preferred_t1)+"->"+str(t2))
        #print(diff.to_json(pretty=True))
    for baseline_testrun in baseline_runs:
        # XXX This calculation always requires baseline, so
        # diff_previous, diff_baseline flags are ignored.
        t1_new = get_summary(baseline_testrun, summary_fields, exclude_version=False)
        t1_new_exclude = get_summary(baseline_testrun, summary_fields)
        t1_new_key = get_summary_key(t1_new_exclude)
        if t1_key not in latest_map or t1_new_key not in latest_map:
            # TODO: perhaps report all differences as 'new' in this case?
            continue # did not find a matching comparison in latest_runs
        latest_baseline, latest_testrun = \
            latest_map[get_summary_key(t1_exclude)], \
            latest_map[get_summary_key(t1_new_exclude)]
        t2 = get_summary(baseline_testrun, summary_fields, exclude_version=False)
        t2_new = get_summary(baseline_testrun, summary_fields, exclude_version=False)
        diff_baseline = diff_testruns(best_overall, baseline_testrun)
        diff_latest = diff_testruns(latest_baseline, latest_testrun)
        diff2 = diff_2or(diff_baseline, diff_latest, key=key)
        diff2.diff_order = 2
        diff2.baseline_comparison = [t1, t1_new]
        diff2.latest_comparison = [t2, t2_new]
        if len(diff.testcases) > 0:
            regression_diffs.append(diff2)
        #print("DEBUG COMPARED", str(t2)+"->"+str(t2_new),
        #      "MINUS", str(t1)+"->"+str(t1_new))
        #print(diff2.to_json(pretty=True))

    # TODO: Only compute regression_diffs when necessary.
    if diff_same:
        return version_diffs, regression_diffs
    return version_diffs

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['baseline','latest'],
                          optional_args=['source_repo'])
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
    header_fields, summary_fields = index_summary_fields(baseline_runs+latest_runs)

    if True:
        out.message(baseline=opts.baseline, latest=opts.latest)
        for testrun in baseline_runs:
            out.show_testrun(testrun, header_fields=header_fields, kind='baseline',
                             show_all_details=False)
        out.section(minor=True)
        for testrun in latest_runs:
            out.show_testrun(testrun, header_fields=header_fields, kind='latest',
                             show_all_details=False)

    # (2,3) compare relevant baseline & latest logs
    version_diffs, regression_diffs = \
        diff_all_testruns(baseline_runs, latest_runs, summary_fields,
                          diff_same=True, # XXX compute 2or diff
                          out=out)

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

    # comparison := dict with keys {'summary', 'baseline_summary',
    #                'minus_summary', 'minus_baseline_summary'}
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
        combination_key = get_combination_key(combination)
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
        combination_key = get_combination_key(combination)
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
        show_combination(opts, out, n_regressions, combination)
        for tc in version_testcases[combination_key]:
            out.show_testcase(None, tc) # XXX no testrun
    out.section()
    out.message("Regressions by configuration")
    # TODO: Add 'compact' option?
    for combination_key, combination in regression_combos.items():
        n_regressions = len(version_testcases[combination_key])
        if n_regressions == 0: continue
        out.section(minor=True)
        show_combination(opts, out, n_regressions, combination)
        for tc in version_testcases[combination_key]:
            out.show_testcase(None, tc) # XXX no testrun

    out.finish()
