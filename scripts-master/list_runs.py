#! /usr/bin/env python3
# List testruns in the Bunsen repo.
usage = "list_runs [[project=]<tag>] [verbose=yes|no] [pretty=yes|no|html]\n" \
        "                 [restrict=<num>]"
#        "                 [sort=[least_]recent] [restrict=<num>]"
default_args = {'project':None,  # restrict to testruns under <tag>
                'verbose':False, # show all fields in pretty-print view
                'pretty':True,   # pretty-print instead of showing JSON
                # TODO 'sort':None,     # sort by date added to Bunsen repo
                'restrict':-1,   # restrict output to N testruns
               }

import sys
import bunsen

from common.format_output import get_formatter

b = bunsen.Bunsen()
if __name__=='__main__':
    # TODO: Replace with cmdline_args()
    opts = b.cmdline_args2(sys.argv, usage=usage, optional_args=['project'],
                           defaults=default_args)
    out = get_formatter(b, opts)

    # TODO: Could take a default value from b.config.
    tags = b.tags if opts.project is None else [opts.project]

    if len(tags) == 0:
        out.message("no projects")
    for tag in tags:
        out.section()
        out.message(project=tag)
        n_testruns = 0
        for testrun in b.testruns(tag):
            if opts.restrict >= 0 and n_testruns >= opts.restrict:
                out.message("... restricted to {} testruns per project ..." \
                            .format(n_testruns))
                break
            out.show_testrun(testrun)
            n_testruns += 1
        if opts.restrict < 0 or n_testruns < opts.restrict:
            out.message("total {} testruns".format(n_testruns))
    out.finish()
