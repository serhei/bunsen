#! /usr/bin/env python3
# TODO this script should replace list_commits throughout the other scripts as a library
# TODO from common.cmdline_args import default_args
info='''List the testruns in the Bunsen repo for each project version given
by package_nvr or a commit from a specified branch (by default, one of
master/main/trunk) of the Git repo source_repo.'''
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

def pick_default_branch(repo):
    for default_cand in {'master','main','trunk'}:
        if default_cand in repo.heads:
            return default_cand
    return 'master' # XXX assume 'master' and let it produce an error

commit_regex = re.compile(r"commit .*-g(?P<hexsha>[0-9A-Fa-f]+)")

def append_map(m, key, val):
    if key not in m: m[key] = []
    m[key].append(val)

class TestrunVer:
    # TODO Document fields source_commit, package_nvr, version
    # TODO Add constructor

    pass

class TestrunVerIndex:
    # TODO Document fields testruns_map, hexsha_lens, package_nvr_map

    def find_testrun_ver(self, testrun_ver):
        if testrun_ver.source_commit is not None:
            return self.find_commit(testrun_ver.source_commit)
        if testrun_ver.package_nvr is not None:
            if testrun_ver.package_nvr in self.package_nvr_map:
                return self.package_nvr_map[testrun_ver.package_nvr]
        # TODO: Anything we can do with testrun_ver.version?
        return None

    def find_commit(self, full_hexsha):
        for k in self.hexsha_lens:
            hexsha = full_hexsha[:k]
            if hexsha in self.testruns_map:
                return self.testruns_map[hexsha]
        return None

def get_version(testrun):
    '''Return a TestrunVer object for this testrun. For deciding on
    the version, use source_commit, version (+commit_regex),
    package_nvr, version (raw) in order.'''
    tver = TestrunVer()
    tver.source_commit = None
    if 'source_commit' not in testrun or testrun.source_commit is None:
        m = commit_regex.search(testrun.version)
        if m is not None:
            tver.source_commit = m.group('hexsha')
    elif 'source_commit' in testrun:
        tver.source_commit = testrun.source_commit
    tver.package_nvr = None
    # TODO: package_nvr could also be derived from source_commit + the tags in source_repo
    if 'package_nvr' in testrun:
        tver.package_nvr = testrun.package_nvr
    tver.version = testrun.version
    return tver

def index_testrun_versions(b, projects=None, warn_skipped=True):
    '''Collect the version and source git commit history for a project or
    projects. Returns a TestrunVerIndex object.'''
    if projects is None:
        projects = b.projects
    tvix = TestrunVerIndex()
    tvix.testruns_map = {}
    tvix.hexsha_lens = set()
    tvix.package_nvr_map = {}
    for project in projects:
        for testrun in b.testruns(project):
            tver = get_version(testrun)
            if tver.source_commit is not None:
                tvix.hexsha_lens.add(len(tver.source_commit)) # add for subsequent lookup
                append_map(tvix.testruns_map, tver.source_commit, testrun)
            elif tver.package_nvr is not None:
                append_map(tvix.package_nvr_map, tver.package_nvr, testrun)
            elif warn_skipped:
                warn_print("could not find a source commit or package_nvr for testrun:\n{}" \
                           .format(testrun.to_json(summary=True)))
    return tvix

def iter_tested_commits(b, repo, testrun_version_index=None,
                         projects=None, forward=False, branch=None):
    '''Iterate the commits in repo starting from / ending at the first tested commit.

    Use for displaying historical data where we aren't interested in
    commits from before we started testing a project.'''

    if testrun_version_index is None:
        # XXX Redundant with previous index_testrun_versions calls,
        # but doing this again is ok for e.g. a final report.
        testrun_version_index = index_testrun_versions(b, projects, warn_skipped=False)
    if branch is None:
        branch = pick_default_branch(repo)

    last_pre_testing = None
    for commit in repo.iter_commits(branch, reverse=True):
        c = testrun_version_index.find_commit(commit.hexsha)
        if c is not None:
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

# TODO def iter_tested_versions(b, repo, testrun_version_index=None, projects=None, forward=False, branch='master'/'main'/'trunk')?

def _iter_package_nvrs(b, repo, testrun_version_index, forward=False):
    package_nvr_map = testrun_version_index.package_nvr_map
    package_nvrs = package_nvr_map.keys()
    if forward:
        for package_nvr in sorted(package_nvrs):
            tver = TestrunVer()
            tver.source_commit = None
            tver.package_nvr = package_nvr
            yield tver
    else:
        for package_nvr in reversed(sorted(package_nvrs)):
            tver = TestrunVer()
            tver.source_commit = None
            tver.package_nvr = package_nvr
            yield tver

def iter_testruns(b, repo, testrun_version_index,
                  forward=False, branch=None):
    for version_id, _commit, testruns in iter_history(b, repo, testrun_version_index,
                                                      forward=forward, branch=branch):
        if testruns is None:
            continue
        for testrun in testruns:
            yield testrun

def iter_history(b, repo, testrun_version_index,
                 projects=None, forward=False,
                 include_empty_versions=False,
                 include_downstream_versions=True,
                 include_early_history=False,
                 branch=None):
    '''Yields tuples (version_id (package_nvr or hexsha), commit (or None), testruns).'''

    if branch is None:
        branch = pick_default_branch(repo)

    # TODO: Need to integrate package_nvr commits with tagged commits, based on tags.
    # For now, we are putting the package_nvr versions at the front in either direction:
    if include_downstream_versions:
        for tver in _iter_package_nvrs(b, repo, testrun_version_index=testrun_version_index,
                                       forward=forward):
            testruns = testrun_version_index.find_testrun_ver(tver)
            if testruns is None:
                if include_empty_versions:
                    yield commit, []
                continue
            yield tver.package_nvr, None, testruns
    for commit in repo.iter_commits(branch, reverse=forward) if include_early_history \
        else iter_tested_commits(b, repo, testrun_version_index=testrun_version_index,
                                 projects=projects, forward=forward, branch=branch):
        testruns = testrun_version_index.find_commit(commit.hexsha)
        if testruns is None:
            if include_empty_versions:
                yield commit.hexsha, commit, []
            continue
        yield commit.hexsha, commit, testruns

def iter_adjacent_commits(b, repo, testrun_version_index=None,
                          forward=False, include_empty_commits=False,
                          branch=None):
    '''For adjacent commits in branch, yield tuples
    (older_commit, older_testruns, newer_commit, newer_testruns).
    Does not include testruns that don't have an associated commit in repo.'''

    if branch is None:
        branch = pick_default_branch(repo)

    # XXX IDEA FOR LATER: We could also generalize this function to
    # include testruns based on package_nvr, with a somewhat tricky
    # definition of 'adjacency'. (Adjacent package_nvr in the
    # fully-sorted list of package_nvr values, plus package_nvrs
    # adjacent to tags in the commit history).
    #
    # We would have to yield some testruns twice as they appear in
    # different 'adjacent' comparisons.

    commit, testruns = None, None
    for commit2, testruns2 in \
        iter_history(b, repo, testrun_version_index,
                     forward=forward, branch=branch,
                     include_downstream=False):
        if not include_empty_commits and len(testruns2) <= 0:
            continue
        if commit is not None:
            if forward: # commit2 is newer
                yield (commit, testruns, commit2, testruns2)
            else: # commit is newer
                yield (commit2, testruns2, commit, testruns)
        commit, testruns = commit2, testruns2

# TODO merge with list_versions, list_commits, etc.
def format_version_header(out, opts, version_id, testruns, commit=None, as_table_row=False):
    info = dict()
    if commit is not None:
        #info['commit_id'] = commit.hexsha[:7]+'...' # for compact=True
        info['commit_id'] = out.sanitize(commit.hexsha)
        info['summary'] = out.sanitize(commit.summary)
    else:
        info['version'] = out.sanitize(version_id)
    if commit is not None and opts.pretty == 'html' and opts.gitweb_url is not None:
        commit_url = opts.gitweb_url + ";a=commit;h={}" \
                         .format(commit.hexsha)
        commitdiff_url = opts.gitweb_url + ";a=commitdiff;h={}" \
                             .format(commit.hexsha)
        gitweb_info = "<a href=\"{}\">commit</a>, ".format(commit_url) + \
            "<a href=\"{}\">commitdiff</a>".format(commitdiff_url)
        info['gitweb_info'] = gitweb_info

    # compact output (HTML only) -- one line per version
    # TODOXXX Create a version of this for PrettyPrinter
    if as_table_row:
        if 'commit_id' not in info:
            pass # TODO merge commit_id and version
        out.table_row(info, order=['commit_id','summary'], merge_header=True)
        return # XXX caller can add out.testrun_cell()s

    # regular output -- one section per version
    out.section(minor=True)
    # TODO: implement out.sanitize() in other scripts, default to sanitize=True
    out.message(compact=False, sanitize=False, **info)
    # XXX: Note commit.summary was observed to get weird near the
    # start of SystemTap history many years ago. Maybe a bug, but
    # not relevant because we never tested that far back in time
    # with the buildbots.

b = bunsen.Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['source_repo'],
                          optional_args=['project'])
    out = get_formatter(b, opts)

    projects = opts.get_list('project', default=b.projects)
    repo = Repo(opts.source_repo)
    forward = True if opts.sort == 'least_recent' else False
    header_fields = opts.get_list('header_fields', default=['arch', 'osver'])

    testrun_version_index = index_testrun_versions(b, projects)
    n_versions, n_testruns = 0, 0
    for version_id, commit, testruns in iter_history(b, repo, testrun_version_index,
                                                     forward=forward, branch=opts.branch,
                                                     include_empty_versions=opts.show_all):
        if opts.restrict >= 0 and n_versions >= opts.restrict:
            out.message("... restricted to {} versions, {} testruns ..." \
                        .format(n_versions, n_testruns))
            break

        # TODOXXX Improve commit_header formatting boilerplate here, and in +when_failed, +find_regressions, +overview -- create some common code?
        as_table_row = opts.compact and opts.pretty == 'html'
        format_version_header(out, opts, version_id, commit, as_table_row=as_table_row)
        if as_table_row:
            for testrun in testruns:
                config = html_field_summary(testrun, header_fields, separator="<br/>")
                out.testrun_cell(config, testrun)
                n_testruns += 1
            n_commits += 1
            continue
        for testrun in testruns:
            #if testrun.project not in projects:
            #    continue
            out.show_testrun(testrun, header_fields=header_fields,
                             show_all_details=opts.verbose)
            n_testruns += 1
        n_versions += 1

    if opts.restrict < 0 or n_versions < opts.restrict:
        out.section()
        out.message(n_versions, "versions,", n_testruns,
                    "testruns for branch", opts.branch) # TODO 'branch' is irrelevant if all the testruns are listed by package_nvr
        # TODO for html linkification, add show_more+skip link for pagination
        # TODO likewise for show_logs, add pagination for the giant .log file? or restrict some lines then link to the raw version
    out.finish()

