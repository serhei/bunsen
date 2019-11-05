#! /usr/bin/env python3
# List the testruns in the Bunsen repo for each commit in a specified
# branch (default master) of the Git repo source_repo.
usage = "list_commits.py [[source_repo=]<path>] [branch=<name>] [project=<tag>]\n" \
        "                       [verbose=yes|no] [compact=yes|no] [pretty=yes|no|html]\n" \
        "                       [sort=[least]_recent] [restrict=<num>]\n" \
        "                       [header_fields=<field1>,<field2>,...]"
default_args = {'source_repo':None,   # scan commits from source_repo
                'branch':'master',    # scan commits in branch <name>
                'project':None,       # restrict to testruns under <tag>
                'verbose':False,      # show info for each testrun
                'compact':False,      # for pretty=html only -- show one row per commit
                'pretty':True,        # pretty-print info instead of showing JSON
                'sort':'recent',      # sort by date of commit
                'restrict':-1,        # restrict output to N commits
                'header_fields':None, # fields to use for testrun header / compact view config
               }

import sys
import bunsen
from git import Repo

from common.format_output import get_formatter, html_field_summary

import re

commit_regex = re.compile(r"commit .*-g(?P<hexsha>[0-9A-Fa-f]+)")

def append_map(m, key, val):
    if key not in m: m[key] = []
    m[key].append(val)

def find_testruns(full_hexsha, testruns_map, hexsha_lens):
    for k in hexsha_lens:
        if full_hexsha[:k] in testruns_map:
            return testruns_map[full_hexsha[:k]]
    return None

def get_source_commit(testrun):
    if 'source_commit' not in testrun or testrun.source_commit is None:
        m = commit_regex.search(testrun.version)
        if m is None: return None
        return m.group('hexsha')
    else:
        return testrun.source_commit

def index_source_commits(b, tags):
    '''Collect the source git commit history for a tag or tags.
    Returns (testruns_map, hexsha_lens,) where
    - testruns_map maps hexsha(truncated) -> list of commit object.
    - hexsha_lens lists possible lengths of hexsha keys in testruns_map.
    See find_testruns() to understand how lookups are done.'''
    testruns_map = {}
    hexsha_lens = set()
    for tag in b.tags:
        for testrun in b.testruns(tag):
            hexsha = get_source_commit(testrun)
            if hexsha is None:
                print("WARNING: could not find a source commit for testrun:\n{}" \
                      .format(testrun.to_json(summary=True)), file=sys.stderr)
                continue
            hexsha_lens.add(len(hexsha)) # add for subsequent lookup
            append_map(testruns_map, hexsha, testrun)    
    return testruns_map, hexsha_lens

def iter_history(b, repo, testruns_map=None, hexsha_lens=None,
                 reverse=False, include_empty_commits=False,
                 branch='master'):
    for commit in repo.iter_commits(branch, reverse=reverse):
        testruns = find_testruns(commit.hexsha, testruns_map, hexsha_lens)
        if testruns is None:
            if include_empty_commits:
                yield commit, []
            continue
        yield commit, testruns

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, usage=usage, required_args=['source_repo'],
                          optional_args=['project'], defaults=default_args)
    out = get_formatter(b, opts)

    tags = b.tags if opts.project is None else [opts.project]
    repo = Repo(opts.source_repo)
    reverse = True if opts.sort == 'least_recent' else False
    header_fields = opts.get_list('header_fields', default=['arch', 'osver'])

    testruns_map, hexsha_lens = index_source_commits(b, tags)
    n_commits, n_testruns = 0, 0
    for commit, testruns in iter_history(b, repo, testruns_map, hexsha_lens, reverse,
                                         branch=opts.branch):
        if opts.restrict >= 0 and n_commits >= opts.restrict:
            out.message("... restricted to {} commits, {} testruns ..." \
                        .format(n_commits, n_testruns))
            break

        info = dict()
        # TODOXXX Shorten commit_id automatically, rename to source_commit
        info['commit_id'] = commit.hexsha[:7]+'...'
        info['summary'] = commit.summary

        # compact output (HTML only) -- one line per commit
        if opts.compact and opts.pretty == 'html':
            out.table_row(info, order=['commit_id','summary'], merge_header=True)
            for testrun in testruns:
                config = html_field_summary(testrun, header_fields, separator="<br/>")
                out.testrun_cell(config, testrun)
                n_testruns += 1
            n_commits += 1
            continue

        # regular output -- one line per testrun, one section per commit
        out.section(minor=True)
        out.message(commit_id=info['commit_id'],
                    summary=info['summary'])
        # XXX: Note commit.summary was observed to get weird near the
        # start of SystemTap history many years ago. Maybe a bug, but
        # not relevant because we never tested that far back in time.
        for testrun in testruns:
            out.show_testrun(testrun, header_fields=header_fields,
                             show_all_details=opts.verbose)
            n_testruns += 1
        n_commits += 1
    if opts.restrict < 0 or n_commits < opts.restrict:
        out.section()
        out.message(n_commits, "commits,", n_testruns,
                    "testruns for branch", opts.branch)
    out.finish()
