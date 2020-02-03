#! /usr/bin/env python3
# TODO from common.cmdline_args import default_args
info='''List testruns in the Bunsen repo.'''
cmdline_args = [
    ('project', None, '<tags>',
     "restrict to testruns under <tags>"),
    ('source_repo', None, '<path>',
     "add commit messages from source repo <path>"),
    ('verbose', False, None,
     "show all fields in pretty-print view"),
    ('pretty', True, None,
     "pretty-print instead of showing JSON"),
    ('sort', None, '[least_]recent',
     "sort by chronological order"),
    ('restrict', -1, None,
     "restrict output to <num> testruns"),
]

import sys
import bunsen
from git import Repo

from common.sorting import chronological_order
from common.format_output import get_formatter

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          optional_args=['project','source_repo'])
    out = get_formatter(b, opts)

    tags = opts.get_list('project', default=b.tags)
    upstream_repo = None if opts.source_repo is None \
        else Repo(opts.source_repo)

    # XXX 'least_recent' is (forward) chronological order
    reverse = False if opts.sort == 'least_recent' else True
    key_function = None if opts.sort is None \
        else chronological_order(b, upstream_repo, reverse=reverse)

    if len(tags) == 0:
        out.message("no projects")
    for tag in tags:
        out.section()
        out.message(project=tag)
        n_testruns = 0
        for testrun in b.testruns(tag, key_function=key_function):
            if opts.restrict >= 0 and n_testruns >= opts.restrict:
                out.message("... restricted to {} testruns per project ..." \
                            .format(n_testruns))
                break
            extra = {}
            if upstream_repo is not None:
                # TODOXXX Shorten source_commit automatically.
                extra['source_commit'] = testrun.source_commit[:7] + '...'
                try:
                    commit = upstream_repo.commit(testrun.source_commit)
                    extra['summary'] = commit.summary
                except ValueError:
                    pass
            out.show_testrun(testrun, **extra)
            n_testruns += 1
        if opts.restrict < 0 or n_testruns < opts.restrict:
            out.message("total {} testruns".format(n_testruns),
                        "for project", tag)
    out.finish()
