# Library for pretty-printing and {TODO} HTML-formatting Bunsen test result data.

uninteresting_fields = {'year_month',
                        'bunsen_testruns_branch',
                        'bunsen_testlogs_branch'}

def suppress_fields(testrun, suppress=set()):
    testrun = dict(testrun)
    for f in suppress:
        if f in testrun:
            del testrun[f]
    return testrun

def pretty_print_testrun(testrun, pretty=True, suppress=set()):
    if pretty:
        short_commit_id = testrun.bunsen_commit_id
        if len(short_commit_id) > 7: short_commit_id = short_commit_id[:7] + '...'
        print("* {} {} pass_count={} fail_count={}" \
              .format(testrun.year_month, short_commit_id,
                      testrun.pass_count, testrun.fail_count))
        if suppress is not None:
            suppress = suppress.union({'year_month', 'bunsen_commit_id',
                                       'pass_count', 'fail_count'})
        else:
            suppress = set()
    if not pretty:
        print(testrun.to_json())
        return
    if len(suppress) > 0:
        testrun = suppress_fields(testrun, suppress)
    for k, v in testrun.items():
        print('  - {}: {}'.format(k, v))
