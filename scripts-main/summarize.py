#!/usr/bin/env python3

# Display a DejaGNU-like test summary, given the bunsen commit
# of the desired test run. Optionally also takes a comma-separated
# list of glob expressions to limit results.

info = "summarize.py <bunsen_commit> [tests=tests] [verbose=True/False]"
cmdline_args = [
    ('commit', None, '<bunsen_commit>',
     "commit to fetch results for"),
    ('tests', None, '<test_globs>',
     "comma-separated list of glob expressions of tests to summarize"),
    ('verbose', False, '<verbose>',
     'output verbose test results')
]

import sys
import bunsen
from collections import Counter
from pathlib import PurePath

# A list of test outcomes in output order.
outcome_labels = {
    'PASS' : 'expected passes',
    'FAIL' : 'unexpected failures',
    'XPASS' : 'unexpected successes',
    'XFAIL' : 'expected failures',
    'KPASS' : 'unknown successes',
    'KFAIL' : 'known failures',
    'UNTESTED' : 'untested testcases',
    'UNRESOLVED' : 'unresolved testcases',
    'UNSUPPORTED' : 'unsupported tests',
    'PATH' : "paths in test names",
    'DUPLICATE' : "duplicate test names",
    'ERROR' : 'errors',
    'WARNING' : 'warnings'
}

if __name__ == '__main__':
    b = bunsen.Bunsen()
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['commit'], optional_args=['tests'])

    testrun = b.testrun(opts.commit)
    all_tests = testrun.testcases
    found_tests = []
    if opts.tests is not None:
        for glob in opts.tests.split(','):
            found_tests.extend([t for t in all_tests if PurePath(t['name']).match(glob)])
    else:
        found_tests = all_tests

    if found_tests:
        info = testrun.get_info_strings()

        project = b.tags[0] if len(b.tags) == 1 else '<multiple projects>'
        print(f'Summary for commit {opts.commit} of {project} version {info["version"]}')
        print(f'from branch {info["branch"]} on {info["architecture"]} using {info["target_board"]}')
        if opts.tests is not None:
            print(f'limiting results to tests matching: {opts.tests}')
        print()

        # Collate results for outcomes
        c = Counter(t['outcome'] for t in found_tests)

        if opts.verbose:
            for t in found_tests:
                print(f'{t.outcome}: {t.name}: {t.subtest}')
            print()
            print(f'\t\t=== {project} Summary ===')
            print()

        # We could simply loop over the keys of the Counter, but that would not necessarily give
        # us the same output order as DejaGNU itself.
        for l in outcome_labels:
            if c[l] != 0:
                print('# of %-26s %d' % (outcome_labels[l], c[l]), "*" if l == 'DUPLICATE' else "")

        # Output the footnote explaining that reported DUPLICATE numbers are different than
        # what GDB reports.
        if c['DUPLICATE'] != 0:
            print("\n* This number is the total number of tests with duplicate names, not")
            print("  the number of unique duplicate names seen.")
    else:
        print(f'found no tests matching \"{opts.tests}\"')
