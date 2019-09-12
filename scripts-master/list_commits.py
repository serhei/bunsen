#! /usr/bin/env python3
# List the testruns under <project> for each commit in the master
# branch of the Git repo <source_repo>.
usage = "list_commits.py <source_repo> [<project>]"

# TODO: Suggested options:
# - increase/decrease verbosity, pretty-print or show JSON
# - sort commits by most-recent/least-recent first
# - restrict to N most recent commits
# - list commits for a different branch

import sys
import bunsen
from git import Repo

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
                print("WARNING: could not find a source commit for testrun:\n{}".format(testrun.to_json(summary=True)), file=sys.stderr)
                continue
            hexsha_lens.add(len(hexsha)) # add for subsequent lookup
            append_map(testruns_map, hexsha, testrun)    
    return testruns_map, hexsha_lens

def iter_history(b, repo, testruns_map=None, hexsha_lens=None,
                 include_empty_commits=False):
    for commit in repo.iter_commits('master'):
        testruns = find_testruns(commit.hexsha, testruns_map, hexsha_lens)
        if testruns is None:
            if include_empty_commits:
                yield commit, []
            continue
        yield commit, testruns

b = bunsen.Bunsen()
if __name__=='__main__':
    # TODO: source_repo_path, tag could take default values from b.config
    source_repo_path, tag = b.cmdline_args(sys.argv, 2, usage=usage,
                                           defaults=[None])
    tags = b.tags if tag is None else [tag]
    repo = Repo(source_repo_path)

    testruns_map, hexsha_lens = index_source_commits(b, tags)
    n_commits, n_testruns = 0, 0
    for commit, testruns in iter_history(b, repo, testruns_map, hexsha_lens):
        print(commit.hexsha[:7], commit.summary)
        # XXX: Note commit.summary gets weird near the start of
        # SystemTap history many years ago. Maybe a bug, but not
        # relevant because we never tested that far back in time.
        for testrun in testruns:
            print("* {} {} {} pass {} fail" \
                  .format(testrun.year_month, testrun.bunsen_commit_id,
                          testrun.pass_count, testrun.fail_count))
            print(testrun.to_json())
            n_testruns += 1
        print()
        n_commits += 1

    print(n_commits, "commits,", n_testruns, "testruns for branch master")
