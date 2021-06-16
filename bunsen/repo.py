# Bunsen repo access
# Copyright (C) 2019-2021 Red Hat Inc.
#
# This file is part of Bunsen, and is free software. You can
# redistribute it and/or modify it under the terms of the GNU Lesser General
# Public License (LGPL); either version 3, or (at your option) any
# later version.
"""Bunsen repo access.

Provides classes used to access or modify a Bunsen repo
and to configure analysis scripts.
"""

import os
import re
import glob
from pathlib import Path
from configparser import ConfigParser
import tarfile
import shutil
import subprocess
import git

from bunsen.model import *
from bunsen.utils import *

# XXX: Ideally, all nasty Git trickery will be confined to this class.
class Workdir(git.Repo):
    """Temporary clone of a Bunsen git repo.

    Extends the GitPython Repo class with additional methods for safely working
    with Bunsen data. Complex manipulations of the repo data should be done
    in a clone of the main repo to reduce the risk of corrupting its index
    (from experience, the types of manipulations Bunsen is doing are not
    well-tested if applied many times to the same Git working directory
    without a periodic git push and fresh checkout).
    """

    def __init__(self, bunsen, path_or_repo):
        """Create a Workdir instance for an already-checked-out directory.

        Args:
            bunsen (Bunsen): The Bunsen repo this working directory
                was checked out from.
            path_or_repo: Path to a working directory
                or a git.Repo object whose path will be used to create
                this new Workdir object.
        """
        self._bunsen = bunsen
        if isinstance(path_or_repo, git.Repo):
            path_or_repo = path_or_repo.working_tree_dir
        super().__init__(str(path_or_repo))

    def push_all(self, refspec=None):
        """Push all modified branches (or specified list of branches) to origin.

        Args:
            refspec (str or list, optional): Refspec or list of branch names
                to push to the Bunsen repo.
        """
        # <TODO: Configure the Workdir checkout to denyDeletes to avoid 'push :importantBranch'>
        if refspec is None:
            branch_names = self.branches
            refspec = '*:*'
        else:
            # XXX refspec can be a list
            branch_names = refspec
        log_print("Pushing {}...".format(branch_names),
            prefix="bunsen.Workdir:") # <TODO: verbosity level>
        try:
            # <TODO: Need to show progress for large updates;
            # may need to use subprocess instead of git.Repo
            # as GitPython only offers GIT_PYTHON_TRACE for all commands>
            self.git.push('origin', refspec) # <TODO: self.remotes.origin.push?>
        except exception as e:
            err_print(e, prefix="")
            err_print("Could not push branches {}!".format(branch_names),
                prefix="bunsen.Workdir ERROR:")
    # TODOXXX: Doublecheck old implementation:
    # def push_all(self, branch_names=None):
    #     '''
    #     Push all (or specified) branches in the working directory.
    #     '''
    #     if branch_names is None:
    #         # XXX Could also use self.branches
    #         branch_names = [b.name for b in self._bunsen.git_repo.branches]
    #     # TODOXXX: Doing separate push operations at the end of a long
    #     # parsing run is risky as it may result in incomplete data
    #     # when interrupted. Figure out something better. Is a git
    #     # push --all with multiple branches even atomic? Or perhaps
    #     # index branches should be updated last of all.
    #     #
    #     # TODO: For now, perhaps implement the following suggestion:
    #     # - delete the .bunsen_workdir file so the workdir data isn't lost
    #     # - print a warning about how to recover if the operation is interrupted
    #     # - push */testlogs-* in any order
    #     # - push */testruns-* in any order (refers to already pushed testlogs)
    #     # - push index (refers to already pushed testlogs+testruns)
    #     # Alternatively, if branch_names is None: git push --all origin.
    #     for candidate_branch in branch_names:
    #         try:
    #             self.push_branch(candidate_branch)
    #         except Exception: # XXX except git.exc.GitCommandError ??
    #             # XXX This is most typically the result of a partial commit.
    #             # May want to assemble branch names from self.branches.
    #             warn_print("could not push branch {}".format(candidate_branch))

    def push_branch(self, refspec=None):
        """Push the current branch (or specified branch or refspec) to origin.

        Args:
            refspec (str or list, optional): Refspec, branch name, or
                list of branch names to push to the Bunsen repo.
        """
        if refspec is None:
            refspec = self.head.reference.name
        self.push_all(refspec)

    def checkout_branch(self, branch_name, skip_redundant_checkout=False):
        """Check out a branch in this working directory.

        Will destroy any uncommitted changes in the previously checked out
        branch (unless branch_name is already checked out and
        skip_redundant_checkout is enabled).

        Args:
            branch_name (str): Name of branch to check out.
            skip_redundant_checkout (bool, optional): Avoid a redundant
                checkout operation if branch_name is already checked out.
        """

        branch = None
        # XXX: Linear search is ugly, but it works:
        for candidate_ref in self._bunsen.git_repo.branches:
            if candidate_ref.name == branch_name:
                branch = candidate_ref
                break

        # If necessary, create a new branch based on master:
        # <TODO: Handle both old version of Git using 'master' and
        # new version of Git using 'main'/'trunk'/'elephant'/???.>
        if branch is None:
            log_print("Creating new branch {}...".format(branch_name),
                prefix="bunsen.Workdir:") # <TODO: verbosity level>
            branch = self.create_head(branch_name)
            branch.commit = 'master'
            self.git.push('--set-upstream', 'origin', branch_name)

        if skip_redundant_checkout and self.head.reference == branch:
            # For an existing wd already on the correct branch, we may not want
            # to check out at this point. For example, we could want to chain
            # the outputs of one script into inputs for another one and only
            # commit the final result.
            return

        self.head.reference = branch
        self.head.reset(index=True, working_tree=True) # XXX destroys changes
        #branch.checkout() # XXX alternative, preserves uncommitted changes
        self.git.pull('origin', branch_name) # <TODO: handle non-fast-forward pulls?>

    def clear_files(self):
        """Remove almost all files in the working directory.

        Keeps only .gitignore and the .bunsen_workdir lockfile.

        This is used to commit log files from unrelated testsuite runs
        as successive commits into a testlogs branch.
        """
        # <TODO: Ensure this also works for nested directory structures>
        keep_files = ['.git', '.gitignore', '.bunsen_workdir'] # <TODO: Store this list in a standard location. Make into a customizable parameter?>
        if len(self.index.entries) > 0:
            remove_files = [path
                for path, _v in self.index.entries
                if path not in keep_files]
            log_print("Removing files {} from index...", remove_files,
                prefix="bunsen.Workdir:") # <TODO: HIGH verbosity level>
            self.index.remove(remove_files)

        # Also remove any non-index files:
        remove_files = [path
            for path in os.listdir(self.working_tree_dir)
            if path not in keep_files]
        log_print("Removing non-index files {}...", remove_files,
            prefix="bunsen.Workdir:") # <TODO: verbosity level, Will probably include the previously removed index files>
        for path in remove_files:
            path = os.path.join(self.working_tree_dir, path)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

        # Check the result:
        for path in os.listdir(self.working_tree_dir):
            if path not in keep_files:
                raise BunsenError("BUG: file {} was not removed from working directory".format(path))

    # <TODO: Add option skip_empty=False>.
    def commit_all(self, commit_msg, allow_duplicates=False):
        """Commit almost all files in the working directory.

        Excludes only the .bunsen_workdir lockfile.

        This is used to commit log files from unrelated testsuite runs
        as successive commits into a testlogs branch.

        Args:
            commit_msg (str): The commit message to use.
            allow_duplicates (bool, optional): Create a new commit even if
                a similar commit is already present. If allow_duplicates
                is False, try to find a previous commit in the same branch
                with the same files and return its commit hexsha instead
                of committing the same files again.
            <TODO> skip_empty (bool, optional): Don't attempt to create the
                commit if it would be empty, but instead return None.

        Returns:
            str: The hexsha of the new or already existing commit.
        """

        paths = []
        for path in os.listdir(self.working_tree_dir):
            # <TODO: This may not be necessary if clear_files() was called.>
            # if str(path) == '.bunsen_initial':
            #     # Remove the dummy placeholder file from master branch.
            #     self.index.remove(['.bunsen_initial'])
            #     os.remove(os.path.join(self.working_tree_dir,path))
            if path != '.git' and path != '.bunsen_workdir':
                paths.append(path)
        log_print("Adding {} to index...".format(paths),
            prefix="bunsen.Workdir:") # <TODO: HIGH verbosity level>
        self.index.add(paths)
        # <TODOXXX: Handle skip_empty here.>

        if not allow_duplicates:
            index_tree = self.index.write_tree() # compute the tree's hexsha

            # XXX When committing many testlogs, this amounts to a quadratic
            # scan through the branch. However, the branch size is limited
            # to approximately one month of logs (and further split with
            # extra tags for particularly large repos).
            # <TODO: Consider memoization again?>
            #if index_tree.hexsha in _bunsen._known_hexshas:
            #    # XXX If this takes too much memory, store just the hexsha
            #    commit = _bunsen._known_hexshas[index_tree.hexsha]
            #    warn_print("proposed commit {}\n.. is a duplicate of already existing commit {} ({})".format(commit_msg, commit.summary, commit.hexsha))
            #    return commit.hexsha
            for commit in self.iter_commits():
                if commit.tree.hexsha == index_tree.hexsha:
                    log_print("Proposed commit tree {} duplicates " \
                        "tree {} for existing commit:\n{} {}" \
                        .format(index_tree.hexsha, commit.tree.hexsha,
                            commit.hexsha, commit.summary),
                        prefix="bunsen.Workdir:") # <TODO: HIGH verbosity level>
                    log_print("Will reuse existing commit {}" \
                        .format(commit.hexsha),
                        prefix="bunsen.Workdir:") # <TODO: verbosity level>
                    return commit.hexsha

        commit = self.index.commit(commit_msg)
        return commit.hexsha

    # <TODO: bunsen should have a --keep option to suppress automatic workdir removal>
    def destroy(self, require_workdir=True):
        """Delete the working directory.

        Args:
            require_workdir (bool, optional): If True, require a
                '.bunsen_workdir' file to be present in the working directory
                to avoid inadvertently destroying a Git working tree that
                wasn't checked out by Bunsen. Defaults to True.
        """
        # Additional safety check (don't destroy a non-Bunsen Git checkout):
        files = os.listdir(self.working_tree_dir)
        if '.git' in files \
            and ('.bunsen_workdir' in files or not require_workdir):
            shutil.rmtree(self.working_tree_dir)
        else:
            warn_print("{} doesn't look like a Bunsen working directory (no .bunsen_workdir), skip deleting it".format(self.working_tree_dir),
                prefix="bunsen.Workdir WARNING:")

# Location of bundled analysis scripts.
# - When running from Git checkout, use '$__file__.parent/..'.
# - TODO: When running from installed location, use '$share/bunsen/scripts'.
BUNSEN_SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# class Bunsen:
#     """Represents a Bunsen repo.

#     Provides methods to query and manage Testruns and Testlogs within the repo
#     and to run analysis scripts.

#     Attributes:
#         base_dir (Path): Path to the top level directory of the Bunsen repo.
#         git_repo_path (Path): Path to the Bunsen git repo.
#             Defaults to 'bunsen.git' within base_dir.
#         git_repo (git.Repo): A git.Repo object representing the Bunsen git repo.
#         cache_dir (Path): Path to the Bunsen analysis cache.
#             Defaults to 'cache' within base_dir.
#     """
#     pass

#################################################
# TODOXXX REWRITE BELOW TO MATCH module-design
#################################################

# TODOXXX For now, hardcode Bunsen data to live in the git checkout directory:
bunsen_repo_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
bunsen_default_dir = os.path.join(bunsen_repo_dir, ".bunsen")
# OR bunsen_default_dir = os.path.join(bunsen_default_dir, "bunsen-data")

class Bunsen:
    def __init__(self, bunsen_dir=None, repo=None, alternate_cookie=None):
        '''
        Create a Bunsen object from a Bunsen repo, which is a directory
        that must contain the following:
        - bunsen.git/ -- a bare git repo with the following branching scheme:
          * index
            - <commit> '<tag>/testruns-<year>-<month>-<optional extra>: ...'
              - <tag>-<year>-<month>.json (append to existing data)
          * <tag>/testruns-<year>-<month>-<optional extra>
            - <commit> '<tag>/testruns-<year>-<month>-<optional extra>: ...'
              - <tag>-<id>.json (<id> references a commit in testlogs branch)
          * <tag>/testlogs-<year>-<month>
            - <commit> '<tag>/testlogs-<year>-<month>: ...'
              - testlogs from one test run (must remove previous commit's testlogs)
        - cache/ -- TODO will contain scratch data for Bunsen scripts, see e.g. +find_regressions
        - config -- git style INI file, with sections:
          - [core]            -- applies always
          - [<script_name>]   -- applies to script +<script_name> only
          - [project "<tag>"] -- applies to any script running on project <tag>
          - TODOXXX [bunsen-push]
          - TODOXXX [bunsen-push "<tag>"]
        - scripts/ -- a folder for user-contributed scripts
        '''
        self.script_name = '<unknown>' # for commandline args & config section
        if 'BUNSEN_SCRIPT_NAME' in os.environ:
            self.script_name = os.environ['BUNSEN_SCRIPT_NAME']

        self.base_dir = bunsen_dir
        if self.base_dir is None:
            if 'BUNSEN_DIR' in os.environ:
                self.base_dir = os.environ['BUNSEN_DIR']
            else:
                self.base_dir = bunsen_default_dir

        self.git_repo_path = repo
        if repo is None:
            if 'BUNSEN_REPO' in os.environ:
                self.git_repo_path = os.environ['BUNSEN_REPO']
            else:
                self.git_repo_path = os.path.join(self.base_dir, "bunsen.git")
        # TODO: Also configure git_repo_path via a config option?

        if os.path.isdir(self.git_repo_path):
            self.git_repo = git.Repo(self.git_repo_path)

        self.cache_dir = os.path.join(self.base_dir, "cache")

        # TODO: Remove common config options from script cmdlines, document separately.
        self._config_path = os.path.join(self.base_dir, "config")
        self.config = ConfigParser()
        if os.path.isfile(self._config_path):
            self.config.read(self._config_path)

        # XXX Not necessary except for init_repo():
        self._scripts_path = os.path.join(self.base_dir, "scripts")

        # Used as defaults for initializing a workdir:
        self.default_work_dir = None
        if 'BUNSEN_WORK_DIR' in os.environ:
            self.default_work_dir = os.environ['BUNSEN_WORK_DIR']
        self.default_branch_name = None
        if 'BUNSEN_BRANCH' in os.environ:
            self.default_branch_name = os.environ['BUNSEN_BRANCH']
        if 'BUNSEN_COOKIE' in os.environ:
            # Append to BUNSEN_WORK_DIR.
            wd_cookie = alternate_cookie
            if wd_cookie is None:
                wd_cookie = os.environ['BUNSEN_COOKIE']
            if wd_cookie == '':
                wd_cookie = str(os.getpid())
            self.default_work_dir = self.default_work_dir + '-' + wd_cookie

        # Used for staging git commits to the repo:
        self._staging_testlogs = []
        self._staging_testruns = []

        # XXX Use for (optional) duplicate detection when making commits?
        # Experiments so far show no benefit over linear scan of branches.
        #self._known_hexshas = {}

        # XXX Use to avoid reading testlogs over and over again:
        self._testlog_lines = {}

        # XXX Search scripts/, scripts-*/ in these directories:
        self.scripts_search_path = [self.base_dir, bunsen_repo_dir]

        self.default_pythonpath = [bunsen_repo_dir]
        # XXX Search recursively for 'scripts-' directories,
        # e.g. .bunsen/scripts-internal/scripts-main
        search_path = self.scripts_search_path
        while len(search_path) > 0:
            next_search_path = []
            for parent_dir in search_path:
                if not os.path.isdir(parent_dir):
                    continue
                for candidate_dir in os.listdir(parent_dir):
                    candidate_path = os.path.join(parent_dir, candidate_dir)
                    if candidate_dir == 'scripts' \
                       or candidate_dir == 'modules' \
                       or candidate_dir.startswith('scripts-') \
                       or candidate_dir.startswith('scripts_') \
                       or candidate_dir.startswith('modules-') \
                       or candidate_dir.startswith('modules_'):
                        if not os.path.isdir(candidate_path):
                            continue
                        self.default_pythonpath.append(candidate_path)
                        next_search_path.append(candidate_path)
            search_path = next_search_path
        # TODO: Also allow invoking Python scripts from shell scripts via $PATH.

        # XXX Add the following environment variables to a running script:
        self.default_script_env = {'BUNSEN_DIR': self.base_dir,
                                   'BUNSEN_REPO': self.git_repo_path,
                                   'BUNSEN_CACHE': self.cache_dir}
        # XXX BUNSEN_SCRIPT_NAME, BUNSEN_WORK_DIR, etc. set per individual run.

    # Methods for querying testlogs and testruns:

    @property
    def tags(self):
        '''
        Find the list of log categories in the repo.
        '''
        found_testruns = {} # found <tag>/testruns-<yyyy>-<mm>-<extra>
        found_testlogs = {} # found <tag>/testlogs-<yyyy>-<mm>-<extra>
        for candidate_branch in self.git_repo.branches:
            m = branch_regex.fullmatch(candidate_branch.name)
            if m is not None:
                tag = m.group('project')
                if m.group('kind') == 'runs':
                    found_testruns[tag] = True
                if m.group('kind') == 'logs':
                    found_testlogs[tag] = True

        found_tags = []
        warned_indexfiles = False
        for tag in found_testruns.keys():
            if tag in found_testlogs:
                # Check for a master index file in index branch:
                commit = self.git_repo.commit('index')
                #dbug_print("found index commit", commit.hexsha, commit.summary) # check for HEAD in index
                found_index = False
                for blob in commit.tree:
                    m = indexfile_regex.fullmatch(blob.path)
                    if m is not None and m.group('project') == tag:
                        #dbug_print("found indexfile", blob.path)
                        found_index = True
                if found_index:
                    found_tags.append(tag)
                elif not warned_indexfiles:
                    warn_print(("found tag {} but no indexfiles "
                                "in branch index").format(tag))
                    warned_indexfiles = True

        return found_tags

    def commit_tag(self, commit_id=None, commit=None):
        '''
        Find the (tag, year_month) pair for a commit in the repo.
        '''
        if commit is None:
            assert commit_id is not None
            commit = self.git_repo.commit(commit_id)
            #dbug_print("found commit_tag commit", commit.hexsha, commit.summary)
        m = commitmsg_regex.fullmatch(commit.summary)
        tag = m.group('project')
        year_month = m.group('year_month')
        return tag, year_month

    def testruns(self, tag, key_function=None, reverse=False):
        '''
        Create an Index object for a log category in the repo.
        '''
        return Index(self, tag, key_function=key_function, reverse=reverse)

    def full_testrun(self, testrun_or_commit_id, tag=None, summary=False):
        return self.testrun(testrun_or_commit_id, tag, summary)

    def testrun(self, testrun_or_commit_id, tag=None, summary=False):
        '''
        Create a Testrun object from a json file in the repo.
        '''
        bunsen_testruns_branch = None
        if isinstance(testrun_or_commit_id, Testrun) \
           and 'bunsen_testruns_branch' in testrun_or_commit_id:
            bunsen_testruns_branch = testrun_or_commit_id.bunsen_testruns_branch
        commit_id = testrun_or_commit_id.bunsen_commit_id \
            if isinstance(testrun_or_commit_id, Testrun) else testrun_or_commit_id

        commit = self.git_repo.commit(commit_id)
        testlog_hexsha = commit.hexsha
        #dbug_print("found testlog commit", testlog_hexsha, commit.summary)
        alt_tag, year_month = self.commit_tag(commit=commit)
        tag = tag or alt_tag

        # XXX Search branches with -<extra>, prefer without -<extra>:
        if bunsen_testruns_branch is not None:
            possible_branch_names = [bunsen_testruns_branch]
        else:
            # TODOXXX: If bunsen_testruns_branch is not specified, should try to read json from the commit's commit_msg.
            # XXX If bunsen_testruns_branch is not specified and the commit's commit_msg has no json, the final (slow) fallback will search *all* branches with -<extra>
            # while preferring the branch without -<extra>.
            # This creates visible latency in analysis scripts.
            default_branch_name = tag + '/testruns-' + year_month
            possible_branch_names = [default_branch_name]
            for branch in self.git_repo.branches:
                if branch.name != default_branch_name \
                   and branch.name.startswith(default_branch_name):
                    possible_branch_names.append(branch.name)
        for branch_name in possible_branch_names:
            try:
                commit = self.git_repo.commit(branch_name)
            except Exception: # XXX except gitdb.exc.BadName
                continue
            #dbug_print("found testrun commit", commit.hexsha, commit.summary) # check for HEAD in branch_name
            try:
                blob = commit.tree[tag + '-' + testlog_hexsha + '.json']
                break
            except KeyError:
                continue
        return Testrun(self, from_json=blob.data_stream.read(), summary=summary)

    def testlog(self, testlog_id, commit_id=None, parse_commit_id=True):
        '''
        Create a Testlog object from a log file. Supports the following:
        - testlog_id='<path>', commit_id=None -- <path> outside repo;
        - testlog_id='<path>', commit_id='<commit>' -- <path> in <commit>;
        - testlog_id='<commit>:<path>', commit_id=None -- <path> in <commit>,
          only if parse_commit_id is enabled.
        '''
        if parse_commit_id and ':' in testlog_id and commit_id is None:
            commit_id, _sep, testlog_path = testlog_id.partition(':')
        else:
            testlog_path = testlog_id

        if commit_id is None:
            return Testlog(self, path=testlog_id)

        commit = self.git_repo.commit(commit_id)
        #dbug_print("found testlog commit", commit.hexsha, commit.summary)
        blob = commit.tree[testlog_path]
        return Testlog(self, path=testlog_path, commit_id=commit_id, blob=blob)

    def _testlog_readlines(self, testlog_path, commit_id):
        if (testlog_path, commit_id) not in self._testlog_lines:
            commit = self.git_repo.commit(commit_id)
            blob = commit.tree[testlog_path]
            lines = blob.data_stream.read().decode('utf8').split('\n')
            #lines = blob.data_stream.readlines()
            self._testlog_lines[(testlog_path, commit_id)] = lines
        return self._testlog_lines[(testlog_path, commit_id)]

    # Methods for adding testlogs and testruns:

    @property
    def staging(self):
        '''
        List of Testlog and Testrun objects to commit to the Bunsen repo.
        '''
        return (self._staging_testlogs, self._staging_testruns)

    def add_testlog(self, testlog_or_path, testlog_name=None):
        '''
        Stage a Testlog to commit to the repo.
        '''
        if isinstance(testlog_or_path, Testlog):
            testlog = testlog_or_path
            assert testlog.path is not None
        elif isinstance(testlog_or_path, tarfile.ExFileObject):
            assert testlog_name is not None
            testlog = Testlog(self, testlog_name, input_stream=testlog_or_path)
        elif isinstance(testlog_or_path, str):
            if testlog_name is None:
                testlog_name = testlog_or_path
            testlog = Testlog(self, testlog_name, input_path=testlog_or_path)
        else:
            # TODO: Doublecheck correctness of path.
            testlog = Testlog(self, testlog_or_path, commit_id=None)
        self._staging_testlogs.append(testlog)

    def add_testrun(self, testrun):
        '''
        Stage a Testrun to commit to the repo.
        '''
        self._staging_testruns.append(testrun)

    def reset_all(self):
        '''
        Remove staged Testlog and Testrun objects.
        '''
        self._staging_testlogs = []
        self._staging_testruns = []

    def commit(self, tag, wd=None, push=True, allow_duplicates=False,
               wd_index=None, wd_testruns=None, branch_extra=None):
        '''
        Commit the staged Testlog and Testrun objects to the Bunsen repo.
        Adds Testlog commit metadata to the staged Testrun objects.

        Optional: wd_index and wd_testruns can be provided to use separate working
        directories for committing to index and testruns branches.
        '''
        # Validate that all Testrun objects have the same year_month:
        year_month = None
        for testrun in self._staging_testruns:
            if year_month is None:
                year_month = testrun.year_month
            elif testrun.year_month is not None \
                 and testrun.year_month != year_month:
                raise BunsenError('conflicting testrun year_months in one commit')
        if year_month is None:
            raise BunsenError('missing year_month in new commit')

        temporary_wd = wd is None
        if temporary_wd:
            wd = self.checkout_wd()
        refspec = [] # -- list of modified branches.

        testruns_branch_postfix = '/testruns-' + year_month
        # XXX For large Bunsen repos, we may need to split testruns
        # among a larger number of branches:
        if branch_extra is not None:
            assert ':' not in branch_extra
            testruns_branch_postfix += '-' + branch_extra
        testruns_branch_name = tag + testruns_branch_postfix
        testlogs_branch_name = tag + '/testlogs-' + year_month

        for testrun in self._staging_testruns:
            # TODOXXX (1): Some earlier repos named
            # bunsen_testlogs_branch as bunsen_branch_name -- may need
            # to temporarily check for both field names in analysis
            # scripts until those repos are rebuilt.
            testrun.bunsen_testlogs_branch = testlogs_branch_name

            # XXX: Record the branches where we stored this testrun.
            # This info will also be added to the index allowing
            # fast lookup of a full Testrun object and its logs.
            if 'alternate_project' in testrun:
                # When committing many testruns for the same set of
                # testlogs, they should be able to override the tag.
                testrun.bunsen_testruns_branch = \
                    testrun.alternate_project + testruns_branch_postfix
                del testrun['alternate_project'] # XXX remove from JSON
            else:
                testrun.bunsen_testruns_branch = testruns_branch_name

        if True:
            branch_name = testlogs_branch_name
            wd.checkout_branch(branch_name, skip_redundant_checkout=True)
            wd.clear_files()
            for testlog in self._staging_testlogs:
                testlog.copy_to(wd.working_tree_dir)
            commit_msg = branch_name # XXX Ensures commit msg contains year_month.
            commit_msg += ": testrun with {} testlogs".format(len(self._staging_testlogs))
            # XXX append testcase summary json to commit msg for
            # testruns_branch lookup.
            #
            # TODOXXX (2): In other respects, this summary will be
            # outdated once the testrun JSON is updated by a
            # subsequent commit. Need to make sure that
            # testruns_branch+year_month is not changed or that the
            # change is handled correctly.
            commit_msg += INDEX_SEPARATOR
            commit_msg += testrun.to_json(summary=True)
            commit_id = wd.commit_all(commit_msg, allow_duplicates=allow_duplicates)
            refspec.append(branch_name)

            # Metadata that is only known after commit is made:
            for testrun in self._staging_testruns:
                testrun.bunsen_commit_id = commit_id

        added_testruns = {} # maps testrun_commit_id -> testrun
        updated_testruns = {} # maps testrun_commit_id -> testrun

        # XXX: Duplicate testruns will overwrite previous json with a
        # freshly parsed one to allow updates in response to parser changes.
        if wd_testruns is None: wd_testruns = wd
        for testrun in self._staging_testruns:
            testrun_tag = testrun.tag if 'tag' in testrun else tag
            branch_name = testrun.bunsen_testruns_branch

            # TODO: Use GitPython to make the commit without checking out?
            wd_testruns.checkout_branch(branch_name, skip_redundant_checkout=True)

            json_name = testrun_tag + "-" + commit_id + ".json"
            json_path = os.path.join(wd_testruns.working_tree_dir, json_name)
            updating_testrun = os.path.isfile(json_path)
            with open(json_path, 'w') as out:
                out.write(testrun.to_json())

            # TODOXXX (3): Check if the index file is unchanged.
            commit_msg = branch_name
            updating_testrun_str = "updating " if updating_testrun else ""
            commit_msg += ": {}index files for commit {}" \
                .format(updating_testrun_str, testrun.bunsen_commit_id)
            wd_testruns.commit_all(commit_msg)
            if branch_name not in refspec:
                refspec.append(branch_name)

            added_testruns[testrun.bunsen_commit_id] = testrun
            updated_testruns[testrun.bunsen_commit_id] = updating_testrun

        if wd_index is None: wd_index = wd
        wd_index.checkout_branch('index', skip_redundant_checkout=True)
        updating_index = False
        for testrun_commit_id, testrun in added_testruns.items():
            testrun_tag = testrun.tag if 'tag' in testrun else tag

            json_name = testrun_tag + "-" + year_month + ".json"
            json_path = os.path.join(wd_index.working_tree_dir, json_name)

            # XXX Delete old data from existing json + set updating_index:
            json_path2 = json_path + "_REPLACE"
            with open(json_path2, 'w') as outfile:
                infile = None
                try:
                    infile = open(json_path, 'r')
                except OSError:
                    pass # index file will be newly created
                if infile is None:
                    data = ''
                else:
                    data = infile.read()
                    if isinstance(data, bytes):
                        data = data.decode('utf-8')
                    infile.close()
                # TODO: Merge this logic with Index._iter_basic():
                for json_str in data.split(INDEX_SEPARATOR):
                    json_str = json_str.strip()
                    if json_str == '':
                        # XXX extra trailing INDEX_SEPARATOR
                        continue
                    other_run = Testrun(self, from_json=json_str, summary=True)
                    if other_run.bunsen_commit_id == commit_id:
                        updating_index = True
                        # TODOXXX (3): Check if testrun is unchanged.
                        # XXX don't add this run to outfile
                        continue

                    outfile.write(other_run.to_json(summary=True))
                    outfile.write(INDEX_SEPARATOR)

                outfile.write(testrun.to_json(summary=True))
                outfile.write(INDEX_SEPARATOR)
            os.rename(json_path2, json_path)
        updating_index_str = "updating " if updating_index else ""
        commit_msg = "summary index for commit {}".format(commit_id)
        wd_index.commit_all(commit_msg)
        refspec.append('index')

        if push:
            wd.push_all(refspec)
            if wd_testruns is not wd: wd_testruns.push_all(refspec)
            if wd_index is not wd: wd_index.push_all(refspec)
        if temporary_wd:
            wd.destroy_wd()

        self.reset_all()
        return commit_id

    # Methods to manage the Bunsen repo:

    def _init_git_repo(self):
        self.git_repo = git.Repo.init(self.git_repo_path, bare=True)

        # create initial commit to allow branching
        cloned_repo = self.checkout_wd('master', \
                                       checkout_name="wd-bunsen-init") # XXX no cookie
        initial_file = os.path.join(cloned_repo.working_tree_dir, \
                                    '.bunsen_initial')
        gitignore_file = os.path.join(cloned_repo.working_tree_dir, \
                                      '.gitignore')
        open(initial_file, mode='w').close() # XXX empty file
        with open(gitignore_file, mode='w') as f: f.write('.bunsen_workdir\n')
        cloned_repo.index.add([initial_file, gitignore_file])
        # TODOXXX: Set other configuration, such as username/email -- perhaps read Config for this?
        cloned_repo.index.commit("bunsen_init: " + \
                                 "initial commit to allow branching")
        cloned_repo.remotes.origin.push()

        # XXX Required for workdir to be deleted:
        workdir_file = os.path.join(cloned_repo.working_tree_dir, \
                                    '.bunsen_workdir')
        with open(workdir_file, mode='w') as f:
            f.write(str(os.getpid()))
        cloned_repo.destroy()

    def init_repo(self):
        '''
        Create an empty Bunsen repo at self.base_dir.
        '''
        found_existing = False

        # imitate what Git does rather closely
        if not os.path.isdir(self.base_dir):
            os.mkdir(self.base_dir)
        else:
            found_existing = True
        if not os.path.isdir(self.git_repo_path):
            self._init_git_repo()
        else:
            # TODO Verify that git repo has correct structure.
            found_existing = True
        if not os.path.isdir(self.cache_dir):
            os.mkdir(self.cache_dir)
        else:
            found_existing = True
        if not os.path.isfile(self._config_path):
            open(self._config_path, mode="a").close() # XXX touch
            # TODO Write any default config values here.
        else:
            found_existing = True
        if not os.path.isdir(self._scripts_path):
            os.mkdir(self._scripts_path)
        else:
            found_existing = True

        return found_existing

    # XXX We could use a linked worktree instead of a git clone.
    # But my prior experiments with direct commit to/from a repo
    # without cloning suggest that could allow accidental corruption
    # (e.g. HEAD file deleted, directory no longer recognized as a git repo).
    def checkout_wd(self, branch_name=None, \
                    checkout_name=None, checkout_dir=None,
                    postfix=None):
        if branch_name is None and self.default_branch_name:
            branch_name = self.default_branch_name
        elif branch_name is None:
            raise BunsenError('no branch name specified for checkout (check BUNSEN_BRANCH environment variable)')

        if checkout_name is None and self.default_work_dir:
            checkout_name = os.path.basename(self.default_work_dir)
        elif checkout_name is None:
            # sanitize the branch name
            checkout_name = "wd-"+branch_name.replace('/','-')
        if checkout_dir is None and self.default_work_dir:
            checkout_dir = os.path.dirname(self.default_work_dir)
        elif checkout_dir is None:
            checkout_dir = self.base_dir

        if postfix is not None:
            checkout_name = checkout_name + '-' + postfix

        wd_path = os.path.join(checkout_dir, checkout_name)

        if os.path.isdir(wd_path):
            # Handle re-checkout of an already existing wd.
            wd = Workdir(self, wd_path)

            # TODOXXX Verify that wd is a Bunsen workdir, update PID file, etc.
        else:
            wd = Workdir(self, self.git_repo.clone(wd_path))

            # Mark this as a workdir, for later certainty with destroy_wd:
            wd_file = os.path.join(wd.working_tree_dir, '.bunsen_workdir')
            with open(wd_file, 'w') as f:
                f.write(str(os.getpid())) # TODOXXX Need to write the PPID in some cases!

        # XXX Special case for an empty repo with no branches to checkout:
        if not wd.heads:
            assert branch_name == 'master'
            return wd

        # Make sure the correct branch is checked out:
        wd.checkout_branch(branch_name)

        return wd

    # TODOXXX cleanup_wds -- destroy wd's without matching running PIDs.
    # Unfortunately this cannot be done in __del__ since in
    # future we can/will fork long-running scripts to run in the
    # background, independently of the bunsen command invocation.
    # For now, scan .bunsen/wd-* and check which PIDs are gone.

    # Methods to find and run Bunsen scripts:

    def find_script(self, script_name, preferred_host=None):
        if len(script_name) > 0 and script_name[0] in ['.','/']:
            # Scripts are unambiguously specified by absolute or relative path:
            return os.path.abspath(script_name)

        # TODO Perform this search procedure in advance to find triggers?
        scripts_found = []
        search_dirs = self.scripts_search_path
        all_search_dirs = []; all_search_dirs += search_dirs
        while len(search_dirs) > 0:
            next_search_dirs = []
            for parent_dir in search_dirs:
                for candidate_dir in os.listdir(parent_dir):
                    candidate_path = os.path.join(parent_dir, candidate_dir)
                    if not os.path.isdir(candidate_path):
                        continue

                    # TODO Prefer 'scripts-main' over others.
                    if candidate_dir == 'scripts' \
                       or candidate_dir.startswith('scripts-'):
                        # XXX Search recursively e.g. in
                        # .bunsen/scripts-internal/scripts-main
                        next_search_dirs.append(candidate_path)

                        # XXX Allow script_name to be a relative path
                        # e.g. scripts-host/examples/hello-shell.sh
                        # invoked as +examples/hello-shell.
                        script_path = os.path.join(candidate_path, script_name)
                        candidate_paths = [script_path,
                                           script_path + '.sh',
                                           script_path + '.py']
                        # PR25090: Allow e.g. +commit-logs instead of +commit_logs:
                        script_name2 = script_name.replace('-','_')
                        script_path2 = os.path.join(candidate_path, script_name2)
                        candidate_paths += [script_path2,
                                            script_path2 + '.sh',
                                            script_path2 + '.py']
                        for candidate_path in candidate_paths:
                            if os.path.isfile(candidate_path):
                                scripts_found.append(candidate_path)
            search_dirs = next_search_dirs
            all_search_dirs += next_search_dirs
        if len(scripts_found) == 0:
            raise BunsenError("Could not find script +{}\nSearch paths: {}" \
                              .format(script_name, all_search_dirs))

        # Prioritize among scripts_found:
        fallback_script_path = scripts_found[0]
        preferred_script_path = None
        for script_path in scripts_found:
            script_dir = basedirname(script_path)

            # These preferences activate when preferred_host is not None:
            if script_dir == 'scripts-main' \
               and preferred_host != 'localhost':
                continue # XXX Script only suited for localhost.
            # XXX 'scripts-host' should not trigger a preference --
            # if a hostname is specified for a scripts-host/ script,
            # it's probably because the user knows it's a VM host.
            if script_dir == 'scripts-guest' \
               and preferred_host == 'localhost':
                # TODO We might be a lot more strict about this,
                # to the point of changing fallback_script_path.
                # The scripts-guest/ scripts might be destructive
                # operations that the user really doesn't want to
                # run outside a scratch VM.
                continue # XXX Script not suited for localhost.

            # Otherwise, we prefer scripts earlier in the search path
            # i.e. (1) user's custom scripts override bunsen default scripts
            # and TODO (2) scripts-main/ overrides scripts-host/,scripts-guest/
            #
            # Preference (1) lets the user cleanly customize a script
            # by copying files from self.base-dir/scripts-whatever to
            # .bunsen/scripts-whatever and editing them.
            #
            # Preference (2) lets a guest script
            # e.g. scripts-guest/my-testsuite.sh be 'wrapped' by a
            # host script which does additional prep on the main Bunsen server
            # e.g. scripts-host/my-testsuite.py --with-patch=local-changes.patch
            preferred_script_path = script_path
            break

        return preferred_script_path if preferred_script_path \
            else fallback_script_path

    def run_script(self, hostname, script_path, script_args,
                   wd_path=None, wd_branch_name=None, wd_cookie=None,
                   script_name=None):
        script_env = self.default_script_env

        if script_name:
            script_env['BUNSEN_SCRIPT_NAME'] = script_name
        if wd_path:
            script_env['BUNSEN_WORK_DIR'] = wd_path
        if wd_branch_name:
            script_env['BUNSEN_BRANCH'] = wd_branch_name
        if wd_cookie is not None:
            script_env['BUNSEN_COOKIE'] = wd_cookie

        # Add the ability to invoke bunsen commands:
        # TODO: Configure only when script is running on the Bunsen main server.
        script_env['PATH'] = bunsen_repo_dir + ":" + os.environ['PATH']

        # TODO: Configure only when script is written in Python?
        script_env['PYTHONPATH'] = ':'.join(self.default_pythonpath)

        if hostname == 'localhost':
            # TODO Need to add some job control to handle long-running
            # tasks and queueing without requiring the user to keep a
            # terminal / screen session open the entire time.
            rc = subprocess.run([script_path] + script_args, env=script_env)
            # TODO Check rc and signal errors properly.
        else:
            # TODO Start by hardcoding some hosts in config?
            warn_print("Support remote script execution.", prefix="TODO:")
            assert False

    def opts(self, defaults={}):
        if isinstance(defaults, list):
            # XXX Handle new cmdline_args format:
            args = defaults; defaults = {}
            for t in args:
                name, default_val, cookie, description = t
                defaults[name] = default_val
        assert(isinstance(defaults, dict))
        return BunsenOpts(self, defaults)

    def _print_usage(self, info, args, usage=None,
                     required_args=[], optional_args=[]):
        if usage is not None:
            warn_print("USAGE:", usage, prefix="")
            return

        LINE_WIDTH = 80
        usage, arg_info, offset = "", "", 0
        usage += "USAGE: "
        offset += len("USAGE: ")
        usage += "+" + self.script_name
        offset += len("+" + self.script_name)
        indent = " " * (offset+1) # TODO: Needs tweaking for long script names.

        arginfo_map = {}
        required_arginfo = []
        optional_arginfo = []
        other_arginfo = []
        for t in args:
            # TODO: Later args should override earlier args with same name.
            name, default_val, cookie, description = t
            arginfo_map[name] = t
            if name not in required_args and name not in optional_args:
                other_arginfo.append(t)
        for name in required_args:
            required_arginfo.append(arginfo_map[name])
        for name in optional_args:
            optional_arginfo.append(arginfo_map[name])
        all_args = required_arginfo + optional_arginfo + other_arginfo
        for t in all_args:
            name, default_val, cookie, description = t
            if cookie is None and isinstance(default_val, int):
                cookie = '<num>'
            elif cookie is None and name == 'pretty' \
                 and isinstance(default_val, bool):
                cookie = 'yes|no|html' if default_val else 'no|yes|html'
            elif cookie is None and isinstance(default_val, bool):
                cookie = 'yes|no' if default_val else 'no|yes'
            # TODO: Other cases where cookie==None? e.g. sort=[least_]recent

            basic_arg_desc = "{}={}".format(name, cookie)
            if name in required_args:
                arg_desc = "[{}=]{}".format(name, cookie)
            elif name in optional_args:
                arg_desc = "[[{}=]{}]".format(name, cookie)
            else:
                arg_desc = "[{}={}]".format(name, cookie)
            arg_width = 1 + len(arg_desc) # XXX includes a space before
            if offset + arg_width >= LINE_WIDTH:
                usage += "\n" + indent
                offset = len(indent) + arg_width
            else:
                usage += " "
                offset += arg_width
            usage += arg_desc

            # TODO: adjust \t to width of arg names?
            arg_info += "- {}\t{}\n".format(basic_arg_desc, description)
        usage += "\n\n"
        usage += info
        usage += "\n\nArguments:\n"
        usage += arg_info
        warn_print(usage, prefix="")

    # TODO: Document recognized default and cookie types.
    def cmdline_args(self, argv, usage=None, info=None, args=None,
                     required_args=[], optional_args=[],
                     defaults={}, use_config=True):
        '''Used by analysis scripts to pass command line options.

        Supports two formats:
        - usage=str defaults={'name':default_value, ...}
        - info=str args=list of tuples ('name', default_value, 'cookie', 'Detailed description')

        The second format automatically generates a usage message
        which includes argument descriptions in the form
        "name=cookie \t Detailed description."'''

        # Handle +script_name --help. XXX argv is assumed to be sys.argv
        if len(argv) > 1 and (argv[1] == '-h' or argv[1] == '--help'):
            self._print_usage(info, args, usage,
                              required_args, optional_args)
            exit(1)

        assert(usage is None or args is None) # XXX usage built from args
        assert(args is None or len(defaults) == 0)

        # generate information about defaults:
        if args is not None:
            for t in args:
                name, default_val, cookie, description = t
                defaults[name] = default_val
        else:
            args = []
            for name, default_val in defaults.items():
                args.append((name, default_val, None, None))

        opts = self.opts(defaults)

        # Iterate through argv, XXX assumed to be sys.argv.
        if len(argv) > 0:
            argv = argv[1:] # Removes sys.argv[0].
        unnamed_args = [] # matched against required_args+optional_args
        check_required = False # need to find required_args not in unnamed_args
        found_unknown = False # warn about unknown option
        for i in range(len(argv)):
            m = cmdline_regex.fullmatch(argv[i])
            key = m.group('keyword')
            if key is None:
                unnamed_args.append(m.group('arg'))
                continue
            # PR25090: Allow e.g. +source-repo= instead of +source_repo=
            key = key.replace('-','_')
            if key not in defaults:
                warn_print("Unknown keyword argument '{}'".format(key))
                found_unknown = True
                continue
            opts.add_opt(key, m.group('arg'))
        if found_unknown:
            self._print_usage(info, args, usage,
                              required_args, optional_args)
            exit(1)

        # match unnamed_args against required_args+optional_args
        j = 0 # index into unnamed_args
        for i in range(len(required_args)):
            if j >= len(unnamed_args):
                check_required = True
                break
            if required_args[i] in opts.__dict__:
                continue # added by keyword already
            opts.add_opt(required_args[i], unnamed_args[j])
            j += 1
        for i in range(len(optional_args)):
            if j >= len(unnamed_args):
                break
            if optional_args[i] in opts.__dict__:
                continue # added by keyword already
            opts.add_opt(optional_args[i], unnamed_args[j])
            j += 1
        if j < len(unnamed_args):
            warn_print("Unexpected extra (unnamed) argument '{}'" \
                       .format(unnamed_args[j]), prefix="")
            self._print_usage(info, args, usage,
                              required_args, optional_args)
            exit(1)

        # set options from self.config
        if use_config:
            # section [core], [<script_name>]:
            opts.add_config('core')
            opts.add_config(self.script_name)

            # section [project "<tag>"]
            tags = opts.get_list('project')
            if tags is not None and len(tags) == 1:
                # XXX Only load config when project is unambiguous.
                # If specifying multiple projects, use command line args.
                opts.add_config(tags[0], is_project=True) # section [project "<tag>"]

        # check if missing required arguments were provided
        # (either from config or as named arguments):
        if check_required:
            for i in range(len(required_args)):
                if required_args[i] not in opts.__dict__:
                    warn_print("Missing required argument '{}'" \
                           .format(required_args[i]), prefix="")
                    self._print_usage(info, args, usage,
                                      required_args, optional_args)
                    exit(1)

        # normalize types and set defaults:
        for t in args:
            key, default_val, cookie, description = t
            if key not in opts.__dict__:
                opts.__dict__[key] = default_val
                continue
            if isinstance(default_val, bool):
                val = opts.__dict__[key]
                if val in {'True','true','yes'}:
                    val = True
                elif val in {'False','false','no'}:
                    val = False
                elif key == 'pretty' and val == 'html':
                    pass # XXX special case
                else:
                    warn_print("Unknown boolean argument '{}={}'".format(key, val))
                    val = False
                opts.__dict__[key] = val
            elif isinstance(default_val, int):
                val = opts.__dict__[key]
                if val == 'infinity' or val == 'unlimited':
                    val = -1
                opts.__dict__[key] = int(val)

        return opts

cmdline_regex = re.compile(r"(?:(?P<keyword>[0-9A-Za-z_-]+)=)?(?P<arg>.*)")
# XXX Format for compact command line args.

# Returned from cmdline_args():
class BunsenOpts:
    def __init__(self, bunsen, defaults):
        self._bunsen = bunsen
        self._defaults = defaults

    def add_opt(self, key, val, warn_duplicates=True):
        # TODO: Check for conflict with previous added options if warn_duplicates is enabled.
        self.__dict__[key] = val

    def add_config(self, config_section, is_project=False):
        if is_project:
            config_section = 'project "{}"'.format(config_section)
        if config_section not in self._bunsen.config:
            return
        for key, val in self._bunsen.config[config_section].items():
            if key in self._defaults \
               or key == 'project': # XXX always used, for config sections
                if key not in self.__dict__: # XXX if not added from cmdline
                    self.__dict__[key] = val

    def get_list(self, key, default=None):
        '''Parse a comma-separated list.'''
        if key not in self.__dict__ or self.__dict__[key] is None:
            return default
        if isinstance(self.__dict__[key], list): # XXX already parsed
            return self.__dict__[key]
        items = []
        for val in self.__dict__[key].split(","):
            if val == "": continue
            items.append(val.strip())
        return items
