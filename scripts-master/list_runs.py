#! /usr/bin/env python3
# List testruns in the Bunsen repo.
usage = "list_runs [[project=]<tag>] [verbose=yes|no] [pretty=yes|no]\n" \
        "                 [restrict=<num>]"
#        "                 [sort=[least_]recent] [restrict=<num>]"
default_args = {'project':None,  # restrict to testruns under <tag>
                'pretty':True,   # pretty-print instead of showing JSON
                'verbose':False, # show all fields in pretty-print view
                # TODO 'sort':None,     # sort by date added to Bunsen repo
                'restrict':-1,   # restrict output to N testruns
               }

import sys
import bunsen

from common.format_output import uninteresting_fields, pretty_print_testrun

b = bunsen.Bunsen()
if __name__=='__main__':
    # TODO: Replace with cmdline_args()
    opts = b.cmdline_args2(sys.argv, usage=usage, optional_args=['project'],
                           defaults=default_args)

    # TODO: Could take a default value from b.config.
    tags = b.tags if opts.project is None else [opts.project]

    suppress = uninteresting_fields if not opts.verbose else None

    first = True
    if len(tags) == 0:
        print("no tags")
    for tag in tags:
        if not first: print()
        print("tag="+tag)
        n_testruns = 0
        for testrun in b.testruns(tag):
            if opts.restrict >= 0 and n_testruns >= opts.restrict:
                print(" ... restrict to {} testruns per tag".format(n_testruns))
                break
            pretty_print_testrun(testrun, pretty=opts.pretty, suppress=suppress)
            n_testruns += 1
        if opts.restrict < 0 or n_testruns < opts.restrict:
            print(n_testruns, "testruns")
        first = False # XXX print newline
