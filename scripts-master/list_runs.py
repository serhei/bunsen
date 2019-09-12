#! /usr/bin/env python3
# List all testruns (or all testruns under <project>).
usage = "list_runs.py [<project>]"

# TODO: Suggested options:
# - increase/decrease verbosity, pretty-print or show JSON
# - sort testruns by most-recent/least-recent first
# - restrict to N most recent testruns

import sys
import bunsen

b = bunsen.Bunsen()
if __name__=='__main__':
    # TODO: tag could take a default value from b.config
    tag = b.cmdline_args(sys.argv, 1, usage=usage,
                         defaults=[None])
    tags = b.tags if tag is None else [tag]
    first = True
    if len(tags) == 0:
        print("no tags")
    for tag in tags:
        if not first: print()
        print("tag="+tag)
        n_testruns = 0
        for testrun in b.testruns(tag):
            print("* {} {} {} pass {} fail" \
                  .format(testrun.year_month, testrun.bunsen_commit_id,
                          testrun.pass_count, testrun.fail_count))
            print(testrun.to_json())
            n_testruns += 1
        print(n_testruns, "testruns")
        first = False # XXX print newline
