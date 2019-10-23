#! /usr/bin/env python3
# List testruns in the Bunsen repo.
usage = "list_runs [[project=]<tag>] [source_repo=<path>] [verbose=yes|no] [pretty=yes|no|html]\n" \
        "                 [restrict=<num>]"
#        "                 [sort=[least_]recent] [restrict=<num>]"
default_args = {'project':None,     # restrict to testruns under <tag>
                'source_repo':None, # add commit messages from source_repo
                'verbose':False,    # show all fields in pretty-print view
                'pretty':True,      # pretty-print instead of showing JSON
                # TODO 'sort':None,     # sort by date added to Bunsen repo
                'restrict':-1,      # restrict output to N testruns
               }

import sys
import bunsen
from git import Repo

from common.format_output import get_formatter

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, usage=usage, optional_args=['project'],
                          defaults=default_args)
    out = get_formatter(b, opts)

    tags = b.tags if opts.project is None else [opts.project]
    repo = None if opts.source_repo is None else Repo(opts.source_repo)

    if len(tags) == 0:
        out.message("no projects")
    for tag in tags:
        out.section()
        out.message(project=tag)
        n_testruns = 0
        for testrun in b.testruns(tag): # TODO: Add sorting option here, including group-by-commit for HTML table.
            if opts.restrict >= 0 and n_testruns >= opts.restrict:
                out.message("... restricted to {} testruns per project ..." \
                            .format(n_testruns))
                break
            extra = {}
            if repo is not None:
                extra['source_commit'] = testrun.source_commit[:7] + '...'
                extra['summary'] = repo.commit(testrun.source_commit).summary
            out.show_testrun(testrun, **extra)
            n_testruns += 1
        if opts.restrict < 0 or n_testruns < opts.restrict:
            out.message("total {} testruns".format(n_testruns))
    out.finish()
