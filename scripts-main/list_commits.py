#! /usr/bin/env python3
# TODO from common.cmdline_args import default_args
info='''List the testruns in the Bunsen repo for each commit in a specified
branch (default master) of the Git repo source_repo.'''
cmdline_args = [
    ('source_repo', None, '<path>',
     "scan commits from source repo <path>"),
    ('branch', 'master', '<name>',
     "scan commits in branch <name>"),
    ('show_all', False, None,
     "show all commits in branch"),
    ('project', None, '<tags>',
     "restrict to testruns under <tags>"),
    ('gitweb_url', None, '<url>',
     "for pretty=html only -- link to gitweb at <url>"),
    ('verbose', False, None,
     "show details for each testrun"),
    ('compact', False, None,
     "for pretty=html only -- show one row per commit"),
    ('pretty', True, None,
     "pretty-print info instead of showing JSON"),
    ('sort', 'recent', '[least_]recent',
     "sort by date of commit"),
    ('restrict', -1, None,
     "restrict output to <num> commits"),
    ('header_fields', None, '<field1>,<field2>,...',
     "list of fields to use for testrun header or compact=yes rows"),
]

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
    - testruns_map maps hexsha(truncated) OR package_nvr -> list of commit object.
    - hexsha_lens lists possible lengths of hexsha keys in testruns_map.
    See find_testruns() to understand how lookups are done.'''
    testruns_map = {}
    hexsha_lens = set()
    for tag in b.tags:
        for testrun in b.testruns(tag):
            hexsha = get_source_commit(testrun)
            if hexsha is None and 'package_nvr' not in testrun:
                print("WARNING: could not find a source commit or package_nvr for testrun:\n{}" \
                      .format(testrun.to_json(summary=True)), file=sys.stderr)
                continue
            if hexsha is None:
                append_map(testruns_map, testrun.package_nvr, testrun)
                continue
            hexsha_lens.add(len(hexsha)) # add for subsequent lookup
            append_map(testruns_map, hexsha, testrun)
    return testruns_map, hexsha_lens

def iter_tested_commits(b, repo, testruns_map=None, hexsha_lens=None,
                        tags=None, forward=False, branch='master'):
    '''Iterate the commits in repo starting from / ending at the first tested commit.

    Use for displaying historical data where we aren't interested in commits
    from before we started testing a project.'''
    if testruns_map is None or hexsha_lens is None:
        # XXX Redundant with index_source_commits, but one extra time is ok for final report.
        testruns_map = set()
        hexsha_lens = set()
        for tag in tags:
            for testrun in b.testruns(tag):
                hexsha = get_source_commit(testrun)
                if hexsha is None:
                    continue # XXX no warning at this point
                testruns_map.add(str(hexsha))
                hexsha_lens.add(len(hexsha))
    last_pre_testing = None
    found_tested_commit = False
    for commit in repo.iter_commits(branch, reverse=True):
        for k in hexsha_lens:
            if commit.hexsha[:k] in testruns_map:
                found_tested_commit = True
        if found_tested_commit:
            break
        last_pre_testing = commit.hexsha
    found_start_of_testing = last_pre_testing is None
    for commit in repo.iter_commits(branch, reverse=forward):
        if forward and commit.hexsha == last_pre_testing:
            found_start_of_testing = True
            continue
        if forward and not found_start_of_testing:
            continue
        if not forward and commit.hexsha == last_pre_testing:
            break
        yield commit

def iter_testruns(b, repo, testruns_map=None, hexsha_lens=None,
                  forward=False, branch='master'):
    for commit in repo.iter_commits(branch, reverse=forward):
        testruns = find_testruns(commit.hexsha, testruns_map, hexsha_lens)
        if testruns is None:
            continue
        for testrun in testruns:
            yield testrun

def iter_history(b, repo, testruns_map=None, hexsha_lens=None,
                 forward=False, include_empty_commits=False,
                 include_early_history=False,
                 branch=None):
    # XXX detect default branch
    for default_cand in {'master','main','trunk'}:
        if branch is None and default_cand in repo.heads:
            branch = default_cand
            break
    for commit in repo.iter_commits(branch, reverse=forward) if include_early_history \
        else iter_tested_commits(b, repo, testruns_map=testruns_map, hexsha_lens=hexsha_lens,
                                 forward=forward, branch=branch):
        testruns = find_testruns(commit.hexsha, testruns_map, hexsha_lens)
        if testruns is None:
            if include_empty_commits:
                yield commit, []
            continue
        yield commit, testruns

def iter_adjacent(b, repo, testruns_map=None, hexsha_lens=None,
                  forward=False, include_empty_commits=False,
                  branch='master'):
    '''For adjacent commits, yield
    (older_commit, older_testruns, newer_commit, newer_testruns).'''
    commit, testruns = None, None
    for commit2, testruns2 in \
        iter_history(b, repo, testruns_map, hexsha_lens,
                     forward=forward, branch=branch):
        if not include_empty_commits and len(testruns2) <= 0:
            continue
        if commit is not None:
            if forward: # commit2 is newer
                yield (commit, testruns, commit2, testruns2)
            else: # commit is newer
                yield (commit2, testruns2, commit, testruns)
        commit, testruns = commit2, testruns2

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['source_repo'],
                          optional_args=['project'])
    out = get_formatter(b, opts)

    tags = opts.get_list('project', default=b.tags)
    repo = Repo(opts.source_repo)
    forward = True if opts.sort == 'least_recent' else False
    header_fields = opts.get_list('header_fields', default=['arch', 'osver'])

    testruns_map, hexsha_lens = index_source_commits(b, tags)
    n_commits, n_testruns = 0, 0
    for commit, testruns in iter_history(b, repo, testruns_map, hexsha_lens,
                                         forward=forward, branch=opts.branch,
                                         include_empty_commits=opts.show_all):
        if opts.restrict >= 0 and n_commits >= opts.restrict:
            out.message("... restricted to {} commits, {} testruns ..." \
                        .format(n_commits, n_testruns))
            break

        # TODOXXX Improve commit_header formatting boilerplate here, and in +when_failed, +find_regressions, +overview -- create some common code?
        info = dict()
        #info['commit_id'] = commit.hexsha[:7]+'...' # for compact=True
        info['commit_id'] = out.sanitize(commit.hexsha)
        info['summary'] = out.sanitize(commit.summary)
        if opts.pretty == 'html' and opts.gitweb_url is not None:
            commit_url = opts.gitweb_url + ";a=commit;h={}" \
                             .format(commit.hexsha)
            commitdiff_url = opts.gitweb_url + ";a=commitdiff;h={}" \
                                 .format(commit.hexsha)
            gitweb_info = "<a href=\"{}\">commit</a>, ".format(commit_url) + \
                "<a href=\"{}\">commitdiff</a>".format(commitdiff_url)
            info['gitweb_info'] = gitweb_info

        # compact output (HTML only) -- one line per commit
        # TODOXXX Create a version of this for PrettyPrinter
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
        # TODO: implement out.sanitize() in other scripts, default to sanitize=True
        out.message(compact=False, sanitize=False, **info)
        # XXX: Note commit.summary was observed to get weird near the
        # start of SystemTap history many years ago. Maybe a bug, but
        # not relevant because we never tested that far back in time
        # with the buildbots.
        for testrun in testruns:
            if testrun.project not in tags:
                continue
            out.show_testrun(testrun, header_fields=header_fields,
                             show_all_details=opts.verbose)
            n_testruns += 1
        n_commits += 1
    if opts.restrict < 0 or n_commits < opts.restrict:
        out.section()
        out.message(n_commits, "commits,", n_testruns,
                    "testruns for branch", opts.branch)
        # TODO for html linkification, add show_more+skip link for pagination
        # TODO likewise for show_logs, add pagination for the giant .log file? or restrict some lines then link to the raw version
    out.finish()
