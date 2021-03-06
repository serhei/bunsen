#! /usr/bin/env python3
# TODO from common.cmdline_args import default_args
info='''Show logfiles from the Bunsen repo.'''
cmdline_args = [
    ('testrun', None, '<bunsen_commit>', "testrun"),
    ('key', '*', '<glob>', "show logfiles matching <glob>"),
    ('exact_match', False, None, "require exact filename match instead of glob"),
    ('pretty', False, None, "include filename annotations"),
]

import sys
import bunsen

from fnmatch import fnmatchcase
from common.format_output import get_formatter

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['testrun'], optional_args=['key'])
    out = get_formatter(b, opts)

    # TODO: Turn this into a method of Bunsen?
    commit = b.git_repo.commit(opts.testrun)
    found = 0
    for blob in commit.tree.blobs:
        # TODO: Need to support subdirectories.
        if blob.name == '.gitignore':
            continue
        if fnmatchcase(blob.name, opts.key):
            found += 1
            if opts.pretty == True:
                print("FOUND", blob.name)
                print("===")
            elif opts.pretty == 'html':
                print("<h1>{}</h1><pre>".format(blob.name))
            print(blob.data_stream.read().decode('utf-8'))
            if opts.pretty == 'html':
                print("</pre><hr>")
            print()
    if opts.pretty == 'html':
        print("<p>{} files found</p>".format(found))
