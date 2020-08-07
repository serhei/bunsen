# Bunsen repo access
# Copyright (C) 2019-2020 Red Hat Inc.
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
from pathlib import Path
from configparser import ConfigParser
import shutil
import git

from bunsen.utils import *

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
        keep_files = ['.git', '.gitignore', '.bunsen_workdir'] # <TODO: Store this list in a standard location.>
        if len(self.index.entries) > 0:
            remove_files = [path
                for path,_v in self.index.entries
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

    def commit_all(self, commit_msg, allow_duplicates=False,
            skip_empty=False):
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
            skip_empty (bool, optional): <TODO> Don't attempt to create the
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
        # <TODOXXXX: Handle skip_empty.>

        if not allow_duplicates:
            index_tree = self.index.write_tree() # compute the tree's hexsha

            # XXX When committing many testlogs, this amounts to a quadratic
            # scan through the branch. However, the branch size is limited
            # to approximately one month of logs (and further split with
            # extra tags for particularly large repos).
            # <TODO: Consider memoization again?>
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

class Bunsen:
    """Represents a Bunsen repo.

    Provides methods to query and manage Testruns and Testlogs within the repo and
    to run analysis scripts.

    Attributes:
        base_dir (Path): Path to the top level directory of the Bunsen repo.
        git_repo_path (Path): Path to the Bunsen git repo.
            Defaults to 'bunsen.git' within base_dir.
        git_repo (git.Repo): A git.Repo object representing the Bunsen git repo.
        cache_dir (Path): Path to the Bunsen analysis cache. 
            Defaults to 'cache' within base_dir.
    """

    def __init__(self, base_dir=None, args=None, script_name=None,
                 options=None):
        """Initialize an object representing a Bunsen repo.

        The initializer can take arguments for a Bunsen command
        invocation since these arguments may be used to specify the location
        of the repo and configure how it should be accessed.

        In addition to these arguments, by default the initializer will
        check for configuration options provided in config files
        (either in the bunsen repo itself or in '$HOME/.bunsenconfig')
        as well as in environment variables.

        Args:
            base_dir (str or Path, optional): Path to the top level directory
                of the Bunsen repo. If not specified (either in this argument,
                in the environment variable BUNSEN_DIR, or in other arguments),
                will search for Bunsen repos in plausible locations
                such as './.bunsen', './.bunsen-data',
                '.bunsen' or '.bunsen-data'
                in the top level of the Git checkout containing '.', or for
                repos in these locations with another directory name according
                to the 'bunsen_dir_name' option in '~/.bunsenconfig'.
            args (list, optional): Arguments to the command or analysis script.
                Passed to the Bunsen object constructor since they may specify
                the location of the repo.
            script_name (str, optional): Name of command or analysis script
                that will be invoked on this repo. Used to initialize internal
                BunsenOptions.
            options (BunsenOptions, optional): BunsenOptions object that
                represents options for the command or analysis that will be
                invoked on this repo.
                If specified, the initializer will not check for config
                files or environment variables.
                Configuration values from the  base_dir, args, and script_name
                arguments will override any values in the options object.
                Note that the original options object will be modified
                and extended with any missing default values.
        """

        # Read configuration in several stages.

        # (0) Initialize new or pre-existing BunsenOptions object:
        if options is None:
            self._opts = BunsenOptions(bunsen=self, script_name=script_name)
        else:
            self._opts = options
            self._opts.bunsen = self
            if script_name is not None:
                self._opts.script_name = script_name
        initializing = self._opts.script_name == 'init'

        # (1) Parse environment variables:
        if options is None:
            self._opts.parse_environment(os.environ)
        else:
            # XXX: Skip if pre-existing BunsenOptions are provided.
            pass

        # (2) Parse global config file:
        _global_config_path = Path.home() / ".bunsenconfig"
        if options is None and _global_config_path.is_file():
             self._opts.parse_config(_global_config_path, global_config=True)
        else:
            # XXX: Skip if pre-existing BunsenOptions are provided
            # or if the global config file is missing.
            pass

        # (3) Parse command line arguments:
        if args is not None:
            self._opts.parse_cmdline(args)

        # (4) Identify Bunsen repo location:
        if base_dir is not None:
            self._opts.bunsen_dir = str(self.base_dir)
        self.base_dir = None
        if self._opts.bunsen_dir is not None:
            self.base_dir = Path(self._opts.bunsen_dir).resolve()
        if self.base_dir is None:
            self._locate_repo(initializing)
        if self.base_dir is None:
            raise BunsenError("no Bunsen repo found or configured")
        self._opts.set_option('bunsen_dir', self.base_dir, 'default')

        # (5) Parse local config file in Bunsen repo
        # (or local config file provided by --config option):
        if self._opts.config_path is None:
            self._opts.config_path = str(self.base_dir / "config")
        if options is None and Path(self._opts.config_path).is_file():
            _config_path = Path(self._opts.config_path)
            self._opts.parse_config(_config_path)
        elif options is not None and self._opts.config_path is not None:
            # XXX: Parse a config file if pre-existing BunsenOptions
            # include a config_path option:
            _config_path = Path(self._opts.config_path)
            self._opts.parse_config(_config_path)
        elif options is None and not initializing:
            # XXX: Not a fatal error, but the repo really should have a config.
            warn_print("Bunsen repo {} doesn't contain a config file.\n" \
                "(Suggest re-trying 'bunsen init'.)".format(self.base_dir))
        else:
            # XXX: Skip if pre-existing BunsenOptions are provided
            # or if we are initializing a new repository.
            pass

        # TODOXXX Handle when invoking script:
        if self._opts.should_print_help:
            self._opts.print_help()
            exit()

        # (*) Finished parsing config.
        # Now initialize the remaining instance variables:

        if self._opts.bunsen_git_repo is None:
            self._opts.bunsen_git_repo = str(self.base_dir / "bunsen.git")
        self.git_repo_path = Path(self._opts.bunsen_git_repo)

        self.git_repo = None
        if self.git_repo_path.is_dir():
            self.git_repo = git.Repo(str(self.git_repo_path))

        self.cache_dir = self.base_dir / "cache"

        # XXX Only needed for init_repo().
        # Other directories starting with 'scripts-' are also valid.
        self._scripts_path = self.base_dir / "scripts"

        # Global cache of Testlog contents.
        # Safe to cache since log files are not modified after being added.
        self._testlog_lines = {} # maps (commit_id, path) -> list of str/bytes

        # Collect logs which self.commit_all() will commit to the repo.
        self._staging_testlogs = [] # <- list of Testlog
        self._staging_testruns = [] # <- list of Testrun

    def _locate_repo(self, initializing=False):
        if self.base_dir is not None:
            return

        if self._opts.use_bunsen_default_repo \
            and self._opts.source['use_bunsen_default_repo'] != 'config':
            self.base_dir = Path(self._opts.bunsen_default_repo).resolve()
            return

        if self._opts.script_name == 'init' \
            and self._opts.bunsen_dir_name is not None:
            self.base_dir = (Path() / self._opts.bunsen_dir_name).resolve()
            return

        if self._opts.script_name == 'init':
            self.base_dir = (Path() / ".bunsen").resolve()
            return

        base_dirs = []
        base_dirs.append(Path()) # current working directory
        base_dirs.append(git_toplevel()) # top level of current git repo
        # <TODO> top level of Bunsen source checkout -- if not installed?

        dir_names = ['.bunsen', 'bunsen-data']
        if self._opts.bunsen_dir_name is not None:
            dir_names = [self._opts.bunsen_dir_name] + dir_names

        for base_dir in base_dirs:
            for dir_name in dir_names:
                cand_path = base_dir / dir_name
                if cand_path.is_dir():
                    self.base_dir = cand_path.resolve()
                    return

        # Final fallback using bunsen_default_repo:
        if self._opts.use_bunsen_default_repo:
            # <TODO> Print a warning.
            self.base_dir = Path(self._opts.bunsen_default_repo).resolve()
            return

    @classmethod
    def from_cmdline(cls, args=None, script_name=None):
        """Initialize objects representing a Bunsen repo and command invocation.

        This method takes a set of command line arguments.

        Args:
            args (list of str): command line options for the command
                or analysis script being invoked.
            script_name (str, optional): name of the command
                or analysis script being invoked.

        Returns:
            Bunsen, BunsenOptions
        """
        b = Bunsen(args=args, script_name=script_name)
        return b, b._opts

    @classmethod
    def from_cgi_query(cls, form):
        """Initialize objects representing a Bunsen repo and command invocation.

        This method takes a set of arguments from a CGI query.

        Args:
            form (cgi.FieldStorage): CGI form arguments for the command
                or analysis script being invoked.
        """
        b = Bunsen()
        b._opts.parse_cgi_query(form)
        return b, b._opts

    ##############################################
    # Methods for querying testlogs and testruns #
    ##############################################

    @property
    def projects(self):
        """List of names of projects in the repo."""

        # XXX: We could cache this data at the risk of it becoming out-of-date
        # when the repository is modified. Recomputing is tolerably quick.

        found_testruns = set() # found <tag>/testruns-<yyyy>-<mm>(-<extra>)?
        found_testlogs = set() # found <tag>/testruns-<yyyy>-<mm>(-<extra>)?
        for candidate_branch in self.git_repo.branches:
            m = branch_regex.fullmatch(candidate_branch.name)
            if m is None:
                continue
            project = m.group('project')
            if m.group['kind'] == 'runs':
                found_testruns.add(project)
            if m.group['kind'] == 'logs':
                found_testlogs.add(project)

        found_projects = []
        warned_indexfiles = False
        for project in found_testruns:
            if project not in found_testlogs:
                continue

            # Check for a master index file in the index branch:
            commit = self.git_repo.commit('index')
            found_index = False
            for blob in commit.tree:
                m = indexfile_regex.fullmatch(blob.path)
                if m is not None and m.group('project') == project:
                    found_index = True
                    break
            if found_index:
                found_projects.append(projects)
            elif not warned_indexfiles:
                warn_print("found tag '{}' but no indexfiles in branch index" \
                    .format(project))
                warned_indexfiles = True

        return found_projects

    @property
    def tags(self):
        """<TODO: Deprecated. Go through the scripts and change tags() to projects().>"""
        return self.projects

    def commit_tag(self, commit_id=None, commit=None):
        """Return (project, year_month, extra_label) for a testlogs commit.

        Each testlogs commit is tagged with a project, a year_month, and
        an optional extra_label which are used to select the branch
        where it should be stored.

        Args:
            commit_id (str, optional): Hexsha of the testlogs commit.
            commit (git.objects.commit.Commit, optional): GitPython Commit
                object representing the testlogs commit. Can be provided
                instead of commit_id, in which case the commit_id argument
                is ignored.
        """
        if commit is None:
            assert commit_id is not None
            commit = self.git_repo.commit(commit_id)

        # The testlogs branch does not include testrun metadata,
        # (and should not, since the metadata can be updated separately)
        # so we use one of two strategies to find the tag.

        # (1) Use git branch --contains to find the branch of this commit:
        branches = self.git_repo.git \
            .branch('--contains', commit.hexsha) \
            .split('\n')
        if len(branches) > 0:
            warn_print("Testlogs commit {} is present in multiple branches {}" \
                .format(commit.hexsha, branches))
        for branch in branches:
            m = branch_regex.search(branch)
            if m is None:
                continue
            project = m.group('project')
            year_month = m.group('year_month')
            extra_label = m.group('extra_label')
            return project, year_month, extra_label

        # (2) Fallback: Parse commit message, which usually includes the tag:
        m = commitmsg_regex.fullmatch(commit.summary)
        if m is None:
            raise BunsenError("could not find branch name for commit {}" \
                .format(commit.hexsha))
        project = m.group('project')
        year_month = m.group('year_month')
        extra_label = m.group('extra_label')
        return project, year_month, extra_label

    def testruns(self, project, key_function=None, reverse=False):
        """Return an Index object for the specified project in this Bunsen repo.

        More complex queries are supported by BunsenCommand.

        Args:
            project: The name of the project for which to return an Index.
            key_function (optional): Sort the index according to
                key_function applied to the Testrun objects.
            reverse (bool, optional): Iterate in reverse of the usual order.
        """
        return Index(self, tag, key_function=key_function, reverse=reverse)

    def testrun(self, testrun_or_commit_id, project=None,
                summary=False, raise_error=True):
        """Retrieve a Testrun from the repo.

        More complex queries are supported by BunsenCommand.

        Args:
            testrun_or_commit_id (Testrun or str): The bunsen_commit_id
                or Testrun object (presumably a summary Testrun)
                corresponding to the Testrun that should be retrieved.
            project (str, optional): The name of the project the retrieved
                Testrun should belong to. Necessary if retrieving by commit id
                and the same bunsen_commit_id has several associated testruns
                in different projects. Will override any project value
                specified by testrun_or_commit_id.
            summary (bool, optional): If True, strip the 'testcases' field
                from the Testrun and return a summary Testrun only.
                <TODOXXX: Testrun() should strip other fields if other fields are of testcases type.>
            raise_error (bool, optional): If True, raise a BunsenError if
                the Testrun was not found in the repo.
                If False, return None in that case.
                Defaults to True.
        """
        testrun_summary = None
        bunsen_commit_id = None
        candidate_branches = []
        year_month = None
        extra_label = None
        # have {testrun_or_commit_id, maybe project}

        if isinstance(testrun_or_commit_id, Testrun):
            testrun_summary = testrun_or_commit_id
        else:
            bunsen_commit_id = testrun_or_commit_id
        if testrun_summary is not None and 'bunsen_commit_id' in testrun_summary:
            bunsen_commit_id = testrun_summary.bunsen_commit_id
        if bunsen_commit_id is None:
            raise BunsenError("no bunsen_commit_id provided in Testrun lookup")
        # have {maybe testrun_summary, bunsen_commit_id, maybe project}

        # Use one of several strategies to find project, candidate_branches:
        candidate_branches = []

        # Option 1a: get project,bunsen_testruns_branch from testrun_summary.
        if testrun_summary is not None \
            and 'bunsen_testruns_branch' in testrun_summary:
            testrun_project, testrun_year_month, testrun_extra_label = \
                testrun_summary.commit_tag()
            if project is None: project = testrun_project
            if project == testrun_project:
                candidate_branches.append(testrun_summary.bunsen_testruns_branch)
            else:
                year_month, extra_label = \
                    testrun_year_month, testrun_extra_label
                # XXX compute candidate_branches manually below

        # Option 1b: get project,year_month from testrun_summary.
        elif project is None and testrun_summary is not None:
            testrun_project, testrun_year_month, testrun_extra_label = \
                testrun_summary.commit_tag()
            if project is None: project = testrun_project
            year_month = testrun_year_month
            # XXX ignore extra_label -- as a field, it would refer to testlogs
            # XXX compute candidate_branches manually below

        # Option 2: get project,bunsen_testruns_branch from commit message JSON
        extra_info = None
        if project is None:
            commit = self.git_repo.commit(bunsen_commit_id)
            msg = commit.message
            t1 = msg.rfind(INDEX_SEPARATOR) + len(INDEX_SEPARATOR)
            msg = msg[t1:]
            extra_info = Testrun(self, from_json=msg, summary=summary)
        if project is None and extra_info is not None \
            and 'bunsen_testruns_branch' in extra_info:
            testrun_project, testrun_year_month, testrun_extra_label = \
                extra_info.commit_tag()
            project = testrun_project
            candidate_branches.append(testrun_summary.bunsen_testruns_branch)

        # Option 3: get project,year_month from commit message header.
        if project is None:
            commit_project, commit_year_month, commit_extra_label = \
                self.commit_tag(bunsen_commit_id)
            project = commit_project
            year_month = commit_year_month
            # XXX ignore extra_label
            # XXX compute candidate_branches manually below

        if project is None:
            raise BunsenError("no project provided in Testrun lookup")

        # Fallback: a value for candidate_branches was not specified explicitly,
        # or the project was overridden by the project argument;
        # we need to construct the branch name manually from the commit_tag:
        if len(candidate_branches) == 0 and year_month is not None:
            default_branch_name = '{}/testruns-{}'.format(project, year_month)
            if extra_label is not None:
                default_branch_name += '-' + extra_label
            candidate_branches.append(default_branch_name)
            # In rare cases we may want to store testrun data in separate
            # branches with an extra_label. In this case our fallback must
            # search through all branch names prefixed by default_branch_name.
            if extra_label is None:
                for branch in self.git_repo.branches:
                    if branch_name != default_branch_name \
                        and branch.name.startswith(default_branch_name):
                        possible_branch_names.append(branch_name)

        # need {bunsen_commit_id, project, candidate_branches matching project}
        blob = None
        for branch_name in candidate_branches:
            try:
                commit = self.git_repo.commit(branch_name)
            except Exception as ex: # XXX except gitdb.exc.BadName
                if ex.__module__ != 'gitdb.exc' \
                    or ex.__name__ != 'BadName':
                    warn_print("unexpected GitPython exception '{}'" \
                        .format(ex))
                # otherwise, skip any nonexistent branch
                continue
            try:
                json_path = '{}-{}.json'.format(project, bunsen_commit_id)
                blob = commit.tree[json_path]
                break
            except KeyError:
                continue
        if blob is None:
            if raise_error:
                raise BunsenError("no Testrun with project '{}', " \
                    "bunsen_commit_id '{}'" \
                    .format(project, bunsen_commit_id))
            return None

        return Testrun(self, from_json=blob.data_stream.read(), summary=summary)

    def full_testrun(self, testrun_or_commit_id, project=None, summary=False):
        """Given a summary Testrun, retrieve the corresponding full Testrun.

        (This method is an alias of testrun(), provided for readability.)

        <TODO: Go through the scripts and change
            testrun = ...
            testrun = b.full_testrun(testrun)
        to
            testrun_summary = ...
            testrun = b.full_testrun(testrun_summary)
        for further readability.>
        """
        return self.testrun(self, testrun_or_commit_id, project, summary)

    def testlog(self, testlog_path, commit_id=None):
        """Retrieve Testlog from repo or create Testlog for an external log file.

        More complex queries are supported by BunsenCommand.

        <TODO> BunsenCommand query should suppoert
        testlog_id='<commit>:<path>', commit_id=None -- <path> in <commit>.

        Args:
            testlog_path (str or Path or PurePath): Path of the log file
                within the Bunsen git tree,
                or path to an external log file.
            commit_id (str, optional): Commit which stores the log file
                within a testlogs branch of the Bunsen git repo,
                or None for an external log file.
        """
        if commit_id is None:
            return Testlog(self, path=testlog_path)
        commit = self.git_repo.commit(commit_id)
        blob = commit.tree[testlog_path]
        return Testlog(self, path=testlog_path, commit_id=commit_id, blob=blob)

    # Provides a way for separate Testlogs referencing the same log file
    # to avoid redundant reads of that log file:
    def _testlog_readlines(self, commit_id, path):
        if (commit_id, path) in self._testlog_lines:
            return self._testlog_lines[(commit_id, path)]
        commit = self.git_repo.commit(commit_id)
        blob = commit.tree[path]
        lines = read_decode_lines(blob.data_stream)
        # XXX prefer to decode utf-8 later in Testlog.line()
        self._testlog_lines[(commit_id, path)] = lines
        return self._testlog_lines[(commit_id, path)]

    ############################################
    # Methods for adding testlogs and testruns #
    ############################################

    @property
    def staging(self):
        """Lists of Testlog and Testrun objects to commit to the Bunsen repo."""
        return self._staging_testlogs, self._staging_testruns

    @property
    def staging_testlogs(self):
        """List of Testlog objects to commit to the Bunsen repo."""
        return self._staging_testlogs

    @property
    def staging_testruns(self):
        """List of Testrun objects to commit to the Bunsen repo.

        All staged testruns will refer to the same testlog commit.
        This is useful for data sources such as the GCC Jenkins server,
        which includes testsuites from several projects in the same set of test
        logs.
        """
        return self._staging_testruns

    def add_testlog(self, source, path=None):
        """Stage a Testlog or external log file to commit to the Bunsen repo.

        Args:
            source: Testlog, external path, or tarfile.ExFileObject
                specifying the log file to stage.
            path (str or PurePath, optional): Intended path of this log file
                within a Bunsen git tree. Should not be an absolute path.
                Will override an existing path specified by source.
        """
        testlog = Testlog.from_source(testlog, path)
        self._staging_testlogs.append(testlog)

    def add_testrun(self, testrun):
        """Stage a Testrun to commit to the Bunsen repo."""
        self._staging_testruns.append(testrun)

    def reset_all(self):
        """Remove all staged Testlog and Testrun objects."""
        self._staging_testlogs = []
        self._staging_testruns = []

    # Save a full representation of the testrun into path.
    # Overwrite any existing testrun summary at that path.
    # Return True if an existing summary was overwritten.
    def _serialize_testrun(self, testrun, json_path):
        updated_testrun = Path(json_path).is_file()
        with open(str(json_path), 'w') as out:
            out.write(testrun.to_json())
        return updated_testrun

    # Insert a summary of the testrun into an indexfile at index_path.
    # Overwrite any existing testrun summary with the same bunsen_commit_id.
    # Return True if an existing summary was overwritten.
    def _serialize_testrun_summary(self, testrun, index_path):
        updated_testrun, need_update_index = False, False
        update_path = index_path + "_UPDATING"

        try:
            index = Index(self, project, index_source=index_path)
            index_iter = index.iter_raw()
        except OSError as err: # index does not exist yet
            if Path(index_path).is_file():
                warn_print("unexpected error when opening {}: {}" \
                    .format(index_path, err))
            index_iter = []

        updated_testruns = []
        found_matching = False
        for json_str in index_iter:
            other_run = Testrun(self, from_json=json_str, summary=True)
            next_run_str = json_str
            if other_run.bunsen_commit_id == commit_id and found_matching:
                warn_print("duplicate/multiple testrun summaries" \
                    "found in {} (bunsen_commit_id={})" \
                    .format(Path(index_path).name,
                        other_run.bunsen_commit_id))
            elif other_run.bunsen_commit_id == commit_id:
                next_run_str = testrun.to_json(summary=True)
                found_matching = True
                # Avoid modifying an unchanged testrun:
                if next_run_str != json_str:
                    updated_testrun = True # will replace other_run
                    need_update_index = True
            updated_testruns.append(next_run_str)
        if not found_matching:
            next_run_str = testrun.to_json(summary=True)
            updated_testruns.append(next_run_str)
            need_update_index = True

        if need_update_index:
            with open(str(update_path), 'w') as updated_file:
                for json_str in updated_testruns:
                    updated_file.write(json_str)
                    updated_file.write(INDEX_SEPARATOR)
            os.rename(str(update_path), str(index_path))

        return updated_testrun

    def commit(self, project=None, year_month=None, extra_label=None,
               wd=None, push=True, allow_duplicates=False,
               testruns_wd=None, index_wd=None,
               primary_testrun=None, testlogs_commit_id=None):
        """Commit all staged Testlog and Testrun objects to the Bunsen repo.

        One of the Testrun objects (by default, the first Testrun staged
        for this commit) is used as the primary testrun. If
        (project, year_month, extra_label) arguments are not specified,
        the primary testrun's (project, year_month, extra_label) commit_tag
        values (or already specified bunsen_testlogs_branch field) will be used
        to select the branch where the Testlog objects are stored.

        Any Testrun objects for which commit_tag value cannot be derived
        (i.e. by parsing an already-specified bunsen_testruns_branch field)
        will be modified to use the same fields as the primary Testrun.

        All Testrun objects in the same commit must have the same year_month.

        If the primary testrun does not provide commit_tag values, commit_tag
        values must be specified as arguments to the commit() invocation.

        Raise BunsenError if it was not possible to commit the Testlog and
        Testrun objects to the repo.

        Args:
            project (str, optional): The project under which to store the
                Testlog objects and any Testruns whose project was not
                specified.
            year_month (str, optional): The year_month for the Testlog and
                Testrun objects. If a Testrun with a different year_month was
                staged, the commit() invocation results in an error.
            extra_label (str, optional): Optional extra_label to append to the
                branch name for the Testlog objects.
            wd (Workdir, optional): Workdir to use for committing to the Bunsen
                Git repo. If not provided, will create a temporary wd.
            push (bool, optional): Whether to push the contents of the Workdir
                after making the commits. If False, the Workdir must be
                provided in the wd argument and the caller must push its
                contents later in order to add the newly committed Testlog
                and Testrun objects to the actual repo. This is useful when
                adding a large number of testruns to the Bunsen repo at once.
            allow_duplicates (bool, optional): Create a new commit in the
                testlogs branch even if a similar commit is already present.
                If allow_duplicates is False, try to find a previous commit
                in the same branch with the same files and return its commit
                hexsha instead of committing the same files again.
            testruns_wd (Workdir, optional): Separate Workdir to use for
                committing to the testruns branches. Defaults to using wd.
            index_wd (Workdir, optional): Separate Workdir to use for
                committing to the index branch. Defaults to using wd.
            primary_testrun (Testrun, optional): Testrun object to use
                as the testrun providing (project, year_month, extra_label)
                for testlogs branch selection.
                Should be one of the Testrun objects staged for this commit.
                Defaults to the first Testrun staged for this commit.
            testlogs_commit_id (str, optional): If no test logs are staged,
                this argument must provide the bunsen_commit_id of an
                existing commit in a testlogs branch. Note that, when writing
                a testrun, any existing testruns with the same commit_tag and
                bunsen_commit_id will be overwritten. This may be useful for
                updating the contents of a parsed testrun when it is known
                that the associated log files do not need to be replaced.

        Returns:
            The bunsen_commit_id for the committed Testlog and Testrun objects.
        """

        # Identify the primary testrun:
        if self._staging_testruns.empty():
            raise BunsenError('no testruns in commit')
        if primary_testrun is None:
            primary_testrun = self._staging_testruns[0]

        # Identify project, year_month, extra_label from primary testrun:
        testrun_project, testrun_year_month, testrun_extra_label = \
            primary_testrun.commit_tag()
        if project is None and testrun_project is not None:
            project = testrun_project
        if year_month is None and testrun_year_month is not None:
            year_month = testrun_year_month
        if extra_label is None and testrun_extra_label is not None:
            extra_label = testrun_extra_label

        if project is None:
            raise BunsenError('missing project for Bunsen commit')
        if year_month is None:
            raise BunsenError('missing year_month for Bunsen commit')

        # Generate bunsen_testlogs_branch for primary testrun:
        if 'bunsen_testlogs_branch' not in primary_testrun:
            testlogs_branch_name = '{}/testlogs-{}' \
                .format(project, year_month)
            if extra_label is not None:
                testlogs_branch_name += '-' + extra_label
            # XXX will validate bunsen_testlogs_branch in the next step
            primary_testrun.bunsen_testlogs_branch = testlogs_branch_name

        found_primary_testrun = False
        related_testrun_refs = []
        for testrun in self._staging_testruns:
            # Validate/populate metadata; commit even if there are problems,
            # unless we are not able to fill in mandatory metadata:
            testrun.validate(project, year_month, extra_label,
                cleanup_metadata=True)
            # XXX This clears extra_label, but we don't care since we
            # only use the extra_label from the primary testrun.

            # Collect list of references to related testruns:
            bunsen_commit_id = None
            if 'bunsen_commit_id' in testrun:
                bunsen_commit_id = testrun.bunsen_commit_id
            if testrun is primary_testrun:
                found_primary_testrun = True
            if testrun is not primary_testrun:
                related_testrun_refs = \
                    (testrun.bunsen_testruns_branch, bunsen_commit_id)

            # In addition, all testruns should have the same year_month:
            testrun_project, testrun_year_month, testrun_extra_label = \
                testrun.commit_tag()
            if testrun.year_month != year_month:
                raise BunsenError("conflicting testrun year_months in commit")
        if not found_primary_testrun:
            raise BunsenError("primary_testrun was not staged for commit")

        # Obtain a working directory:
        temporary_wd = None
        if temporary_wd:
            wd = self.checkout_wd()
            temporary_wd = wd
            assert push # must push if wd is temporary
        testlogs_wd = wd
        if testruns_wd is None:
            testruns_wd = wd
        if index_wd is None:
            index_wd = wd

        # Create git commit from _staging_testlogs:
        wd = testlogs_wd
        testlogs_branch_name = None
        if not self._staging_testlogs.empty():
            assert testlogs_commit_id is None # staged testlogs -> must not pass as arg
            testruns_branch_name = primary_testrun.bunsen_testruns_branch
            testlogs_branch_name = primary_testrun.bunsen_testlogs_branch
            wd.checkout_branch(testlogs_branch_name, skip_redundant_checkout=True)
            wd.clear_files() # remove log files from previous commit
            for testlog in self._staging_testlogs:
                testlog.copy_to(wd.working_tree_dir)
            commit_msg = testlogs_branch_name
            commit_msg += ": testsuite run with '{}' testlogs" \
                .format(len(self._staging_testlogs))
            # If the full testrun summary is included here, it may end up
            # being out of date. So we only include its bunsen_testruns_branch
            # field, which is sufficient for finding the rest of the summary:
            extra_info = {'bunsen_testruns_branch': testruns_branch_name}
            # TODO: Maybe also add related_testrun_refs to extra_info?
            extra_info = Testrun(self, from_json=extra_info, summary=True)
            commit_msg += INDEX_SEPARATOR
            commit_msg += extra_info.to_json(summary=True) # don't validate
            testlogs_commit_id = wd.commit_all(commit_msg,
                # reuse existing log files if possible:
                allow_duplicates=allow_duplicates)
        assert testlogs_commit_id is not None # no staged testlogs -> must pass as arg
        if testlogs_branch_name is None:
            testlogs_branch_name = primary_testrun.bunsen_testlogs_branch

        # Add bunsen_testlogs_branch, bunsen_commit_id to testrun metadata:
        for testrun in self._staging_testruns:
            if 'bunsen_commit_id' not in testrun:
                testrun.bunsen_commit_id = testlogs_commit_id
            if 'bunsen_testlogs_branch' not in testrun:
                testrun.bunsen_testlogs_branch = testlogs_branch_name

        # Also add references to all related testruns to the primary testrun:
        if len(related_testrun_refs) > 0:
            primary_testrun.related_testruns = []
            for branchname, bunsen_commit_id in related_testrun_refs:
                if bunsen_commit_id is None:
                    bunsen_commit_id = testlogs_commit_id
                related_testrun_ref = '{}:{}' \
                    .format(branchname, bunsen_commit_id)
                primary_testrun.related_testruns.append(related_testrun_ref)

        # Create git commits from _staging_testruns:
        wd = testruns_wd
        for testrun in self._staging_testruns:
            testrun_project, testrun_year_month, testrun_extra_label = \
                testrun.commit_tag()
            testrun_branch_name = testrun.bunsen_testruns_branch
            wd.checkout_branch(testrun_branch_name, skip_redundant_checkout=True)
            json_name = "{}-{}.json".format(testrun_project, testrun.bunsen_commit_id)
            updated_testrun = _serialize_testrun(testrun, \
                Path(wd.working_tree_dir) / json_name)
            commit_msg = testrun_branch_name
            updating_testrun_str += "updating " if updated_testrun else ""
            commit_msg += ": {}index file for commit {}" \
                .format(updating_testrun_str, testrun.bunsen_commit_id)
            wd.commit_all(commit_msg)

        # Create git commit for index branch:
        wd = index_wd
        wd.checkout_branch('index', skip_redundant_checkout=True)
        for testrun in self._staging_testruns:
            testrun_project, testrun_year_month, testrun_extra_label = \
                testrun.commit_tag()
            testrun_branch_name = testrun.bunsen_testruns_branch
            json_name = "{}-{}.json".format(testrun_project, testrun_year_month)
            updated_index = _serialize_testrun_summary(testrun, \
                Path(wd.working_tree_dir) / json_name)
            commit_msg = testrun_branch_name
            updating_testrun_str += "updating " if updating_index else ""
            commit_msg += ": {}summary index for commit {}" \
                .format(updating_index_str, testrun.bunsen_commit_id)
            # Don't make a commit if nothing was changed:
            wd.commit_all(commit_msg, skip_empty=True)

        if push:
            testlogs_wd.push_all()
            if wd_testruns is not testlogs_wd: wd_testruns.push_all()
            if wd_index is not testlogs_wd: wd_index.push_all()

        if temporary_wd is not None:
            temporary_wd.destroy()

        self.reset_all()
        return testlogs_commit_id

    # TODO def copy_testrun - copy a testrun from another Bunsen repo

    ##############################################
    # Methods for removing testlogs and testruns #
    ##############################################

    # TODO def delete_testrun - remove a testrun from all indices
    # TODO def delete_commit - remove all testruns for a testlogs commit
    # <- <TODO: need to be sure this interacts properly with deduplication>
    # TODO def _cleanup_testlogs_branch - delete orphaned testlog commits
    # TODO def _cleanup_testruns_branch - delete old commit history
    # TODO def gc_repo - cleanup deleted content from all branches; invalidates existing clones/workdirs

    ########################################
    # Methods for managing the Bunsen repo #
    ########################################

    def _init_git_repo(self):
        self.git_repo = git.Repo.init(str(self.git_repo_path), bare=True)

        # Create initial commit to allow branch creation:
        cloned_repo = self.checkout_wd('master', checkout_name="wd-init-repo")
        initial_file = os.path.join(cloned_repo.working_tree_dir, '.bunsen_initial')
        gitignore_file = os.path.join(cloned_repo.working_tree_dir, '.gitignore')
        open(initial_file, mode='w').close() # XXX empty file
        with open(gitignore_file, mode='w') as f: f.write('.bunsen_workdir\n')
        cloned_repo.index.add([initial_file, gitignore_file])
        commit_msg = "bunsen_init: initial commit to allow branch creation"
        cloned_repo.index.commit(commit_msg)
        cloned_repo.remotes.origin.push()
        cloned_repo.destroy(require_workdir=False)

    def init_repo(self):
        """Create an empty Bunsen repo at self.base_dir.

        If a Bunsen repo already exists at that location, initialize
        missing elements but don't delete anything.

        Returns:
            bool: True if an already existing Bunsen repo was found.
        """
        found_existing = False

        # Imitate what 'git init' does:
        if not self.base_dir.is_dir():
            self.base_dir.mkdir()
        else:
            found_existing = True
        if not self.git_repo_path.is_dir():
            self._init_git_repo()
        else:
            # <TODO>: Validate that the git repo has correct structure e.g. branches, initial_commit?
            found_existing = True
        if not self.cache_dir.is_dir():
            self.cache_dir.mkdir()
        else:
            found_existing = True
        if not self._scripts_path.is_dir():
            self._scripts_path.mkdir()
        else:
            found_existing = True

        return found_existing

    # XXX We could use a linked worktree instead of doing a git clone.
    # However, the cost of a git clone to the same filesystem is very cheap
    # and my prior experiments suggest that an intense workload of
    # automated commits to a bare repo without cloning can eventually result
    # in minor corruption (e.g. HEAD file missing, directory no longer
    # recognized as a git repo).
    def checkout_wd(self, branch_name, checkout_name=None,
                    checkout_path=None, postfix=None):
        """Clone the Bunsen git repo into a working directory.

        Args:
            branch_name (str): Name of branch to check out.
            checkout_name (str, optional): Name to use
                for the working directory.
                Defaults to branch_name, sanitized and prefixed with "wd-".
            checkout_path (str or Path, optional): Directory
                within which to create the working directory.
                Defaults to the Bunsen repo top level directory.
            postfix (str, optional): Additional postfix to append
                to the name of the working directory.

        Returns:
            Workdir
        """
        if checkout_name is None:
            # sanitize the branch name
            checkout_name = "wd-" + branch_name.replace('/','-')
        if checkout_path is None:
            checkout_path = self.base_dir
        if postfix is not None:
            checkout_name = checkout_name + "-" + postfix

        wd_path = Path(checkout_path) / checkout_name
        if wd_path.is_dir():
            # Handle re-checkout of an already existing wd.
            wd = Workdir(self, wd_path)
            # <TODOXXX>: Verify that wd is a Bunsen workdir, update PID file, etc.
        else:
            wd = Workdir(self, self.git_repo.clone(wd_path))

            # Mark the cloned repo as a workdir, for later certainty
            # when calling destroy_wd and cleanup_wds:
            wd_file = Path(wd.working_tree_dir) / ".bunsen_workdir"
            with open(str(wd_file), 'w') as pidfile:
                pidfile.write(str(os.getpid()))

        # Git config for the cloned repo:
        with wd.config_writer() as cw:
            if not cw.has_section('user'): cw.add_section('user')
            cw.set('user', 'name', self._opts.git_user_name)
            cw.set('user', 'email', self._opts.git_user_email)

        # XXX Special case for an empty repo with no branches to checkout:
        if not wd.heads and branch_name != 'master':
            raise BunsenError("trying to checkout branch '{}' of empty repo" \
                .format(branch_name))
        if not wd.heads:
            return wd

        wd.checkout_branch(branch_name)
        return wd

    # TODOXXX cleanup_wds -- destroy wd's without matching running PIDs.
    # Unfortunately this cannot be done in __del__ since in
    # future we can/will fork long-running scripts to run in the
    # background, independently of the bunsen command invocation.
    # For now, scan .bunsen/wd-* and check which PIDs are gone.
    # TODOXXX ...

    # TODO def clone_repo
    # TODO def pull_repo; 'bunsen pull' should also pull log sources

    #####################################################
    # Methods for locating and running analysis scripts #
    #####################################################

    # TODO def find_script
    # TODO def run_script -> run_command (using BunsenOptions)
    def run_command(self):
        pass # TODOXXX
    # TODO def show_command - show cached results from a command (invoke from bunsen-cgi?)

    # TODO additional Bunsen commands that are handled by analysis scripts:
    # - 'bunsen add': import tarball, log file, or set of log files
    # - 'bunsen list' / 'bunsen ls': list testruns or log files
    # - 'bunsen show': display testrun, log file, or set of log files
    # - 'bunsen rebuild': regenerate repo to parse (all or subset) of testruns

    pass # TODOXXX

# BunsenOptions fields that should not be overwritten by actual options:
_options_base_fields = {
    '_bunsen',
    'script_name',
    'required_groups',
    'source',
    '_delayed_config',
    'unknown_args',
}

cmdline_arg_regex = re.compile(r"(?P<prefix>--)?(?:(?P<keyword>[0-9A-Za-z_-]+)=)?(?P<arg>.*)")
# XXX Format for compact command line args.

class BunsenOptions:
    """Collects options for a Bunsen analysis script invocation.

    Each option has an internal name and possibly some external names
    used to refer to that option in different configuration sources
    (command line arguments, config files, environment variables, and
    CGI invocations).

    Attributes:
        script_name (str): Name of the analysis script or command.
        required_groups (set): Option groups required by this script.
            Used to generate a usage message in print_help().
        source (map): Identifies the source of each option's value.
    """

    # TODO: Wishlist:
    # - Support for chaining commands (one command produces JSON as output to next command).
    # - Support for generating shell autocompletions.
    # - Support for providing options as JSON (parse_json_query e.g. for REST API).

    default_values = {}
    """Maps internal names to default values."""

    help_strings = {}
    """Maps internal names to help strings."""

    option_groups = {}
    """Maps group names to groups of options.

    Used to track which groups of options are accepted by each command.
    """

    long_options = {}
    """Maps long command line options to internal names."""

    short_options = {}
    """Maps short command line options to internal names."""

    env_options = {}
    """Maps names of environment variables to internal names."""

    cgi_options = {}
    """Maps names of CGI options to internal names."""

    config_options = {}
    """Maps names of config file options to internal names.

    Within a config file, internal names can also be used directly.
    """

    nonconfig_options = set()
    """Set of options that may not be specified in a config file."""

    boolean_flags = {}
    """Long command line options representing boolean flags.

    Maps a command line option to True if the option sets the flag to True,
    to False if the option sets the flag to False.
    """

    boolean_options = set()
    """Set of options that are boolean flags."""

    @classmethod
    def _add_option_name(cls, opts_map, name, internal_name, override=False):
        if name in opts_map and not override:
            warn_print("option '{}' will override name '{}' used by option '{}'" \
                .format(internal_name, name, opts_map[name]))
        opts_map[name] = internal_name

    @classmethod
    def add_option(cls, internal_name, group=None,
                   cmdline=None, cmdline_short=None,
                   env=None, cgi=None, config=None,
                   nonconfig=False, boolean=False,
                   default=None, help=None,
                   override=False):
        """Define an option.

        Args:
            internal_name (str): Internal name of the option.
            group (str or set of str, optional): Group or set of groups this
                option belongs to. Can be None (in which case the option is
                assumed to always be accepted).
            cmdline (str, optional): Long command-line flag for the option.
            cmdline_short (str, optional): Short command-line flag for the option.
            env (str, optional): Environment variable name for the option.
            cgi (str, optional): CGI argument name for the option.
            config (str, optional): Configuration item name for the option.
                Within a config file, internal names can also be used directly.
            nonconfig (bool, optional): This option may not be set
                by internal name from a configuration file. Defaults to False.
            boolean (bool, optional): Option is a boolean flag.
                If true and cmdline is defined, will also define a negated
                version of the flag (e.g. '--default-repo'
                and '--no-default-repo').
            default (optional): Default value for the option.
            help (str, optional): A description of the option.
            override (bool, optional): This option is known to override
                a prior option. If False, will print a warning when overriding.
        """

        groups = []
        if isinstance(group, str):
            groups.append(group)
        elif isinstance(group, set):
            groups += list(group)
        elif group is None:
            groups.append('this_command')
        for group_name in groups:
            if group_name not in cls.option_groups:
                cls.option_groups[group_name] = set()
            cls.option_groups[group_name].add(internal_name)

        if internal_name in cls.default_values and not override:
            warn_print("overriding default value for option '{}'" \
                .format(internal_name))
        cls.default_values[internal_name] = default
        if boolean and default is None:
            cls.default_values[internal_name] = False

        if cmdline is not None:
            cls._add_option_name(cls.long_options, cmdline, internal_name, override)
        if cmdline is not None and boolean:
            cls.boolean_flags[cmdline] = True
            cls.boolean_flags["no-"+cmdline] = False
            cls._add_option_name(cls.long_options, "no-"+cmdline, internal_name, override)
        if cmdline_short is not None:
            cls._add_option_name(cls.short_options, cmdline_short, internal_name, override)
        if env is not None:
            cls._add_option_name(cls.env_options, env, internal_name, override)
        if cgi is not None:
            cls._add_option_name(cls.cgi_options, cgi, internal_name, override)
        if config is not None:
            cls._add_option_name(cls.config_options, config, internal_name, overrid)

        if nonconfig:
            cls.nonconfig_options.add(internal_name)
        if boolean:
            cls.boolean_options.add(internal_name)

        if help is not None:
            cls.help_strings[internal_name] = help

    def __init__(self, bunsen, script_name=None, required_groups=set()):
        """Initialize a BunsenOptions object representing a command.

        Args:
            bunsen (Bunsen): Bunsen repo this command will run against.
            script_name (str, optional): Name of command or analysis script.
        """
        self._bunsen = bunsen
        self.script_name = script_name
        self.required_groups = required_groups
        self.required_groups.update({'bunsen', 'output'})

        # Set defaults:
        self.source = {}
        for key, value in self.default_values.items():
            self.set_option(key, value, 'default')

        # Set additional computed defaults:
        self.set_option('bunsen_default_repo', str(Path.home() / ".bunsen"), 'default')

        # Save configuration files & args in case later sections are activated:
        self._delayed_config = []
        self.unknown_args = []

    source_priorities = ['args', 'env', 'local', 'global', 'default']
    """Possible sources of options in order of decreasing priority.

    Here:
    - 'args' represents command line or CGI arguments
    - 'environment' represents environment variables
    - 'local' represents a local configuration file (in a Bunsen repo)
    - 'global' represents a global configuration file (in user's home directory)
    """

    @classmethod
    def source_overrides(cls, source1, source2):
        """Return True if source1 takes priority over source2.

        If source1 is not present in source_priorities and source2 is present,
        the answer is assumed to be False.
        """
        if source1 in cls.source_priorities:
            source1_ix = cls.source_priorities.index(source1)
        else:
            source1_ix = len(cls.source_priorities)
        if source2 in cls.source_priorities:
            source2_ix = cls.source_priorities.index(source2)
        else:
            source2_ix = len(cls.source_priorities)
        return source1_ix <= source2_ix

    # XXX: Could use in Bunsen.__init__ but the existing (natural) code
    # there will correctly lead to values marked as being from
    # the lowest-priority (unknown) source.
    def set_option(self, key, value, source):
        """Set an option if it wasn't set from any higher-priority source.

        Args:
            key (str): Internal name of the option.
            value (str): New value for the option.
            source (str): The source of the new value.
                Should be an element of BunsenOptions.source_priorities."""
        # <TODO> Warn about duplicate options added from the same source?
        if key in _options_base_fields:
            warn_print("attempt to set nonexistent (reserved) option '{}'" \
                .format(key))
            return
        if key in self.__dict__ and key in self.source \
            and not BunsenOptions.source_overrides(source, self.source[key]):
            return # XXX A value exists with higher priority.
        if key not in self.default_values:
            # TODOXXX: Several options:
            # if source is config file, just warn and move on; identify which config file we are reading from?
            # if source is cmdline option, and unknown cmdline not permitted, print usage
            # otherwise, keep quiet.
            warn_print("unknown option '{}={}'".format(key, value))
            return
        if key in self.boolean_options or \
            isinstance(self.default_values[key], bool):
            if value in {'True','true','yes'}:
                value = True
            elif value in {'False', 'false', 'no'}:
                value = False
            elif value in self.boolean_options:
                warn_print("unknown boolean option '{}={}'".format(key, value))
                value = False
        elif isinstance(self.default_values[key], int):
            if value in {'infinity', 'unlimited'}:
                value = -1
            value = int(value)
        self.__dict__[key] = value
        self.source[key] = source
        # TODOXXX: Check for any _delayed_config sections that were activated? Or define another method for that.

    def _add_config(self, config, section, is_global=False, is_project=False):
        if is_project:
            section = 'project "{}"'.format(section)
        if section not in config:
            return
        for key, value in config[section].items():
            if key in self.config_options:
                self.set_option(self.config_options[key], value,
                    'global' if is_global else 'local')
            elif key not in self.nonconfig_options:
                self.set_option(key, value,
                    'global' if is_global else 'local')
            else:
                warn_print("attempt to set non-config option '{}' from config" \
                    .format(key))
                pass # don't set anything

    def parse_config(self, config_path, global_config=False):
        """Parse a config file in INI format.

        Args:
            config_path (Path or str): Path to config file.
            global_config (bool, optional): Config file is global
                (and should have lower priority than local config files).

        Returns:
            self
        """
        config = ConfigParser()
        if Path(config_path).is_file():
            config.read(str(config_path))
        else:
            raise BunsenError("config file {} not found".format(config_path))

        # section [core], [<script_name>]
        self._add_config(config, 'core', global_config)
        if self.script_name is not None:
            self._add_config(config, self.script_name, global_config)

        # section [project "<project>"]
        # XXX Only when the project is unambiguous:
        projects = self.get_list('project')
        if projects is not None and len(projects) == 1:
            self._add_config(config, projects[0], global_config, is_project=True)

        # Save config object,
        # in case <script_name> or <project> is specified later.
        self._delayed_config.append(config)

        return self

    def parse_environment(self, env):
        """Parse a set of environment variables.

        Args:
            env (map or environ): Environment variables.

        Returns:
            self
        """
        for key, internal_name in self.env_options.items():
            if key not in env:
                continue
            self.set_option(internal_name, env[key], 'env')
        return self

    def _cmdline_err(self, msg):
        err_print(msg)
        print()
        self.print_help()
        exit(1)

    def _proc_cmdline_arg(self, arg, next_arg, allow_unknown):
        m = cmdline_arg_regex.fullmatch(arg) # XXX always matches
        internal_name, use_next = None, False
        flag, val = None, None
        if len(arg) == 2 and arg.startswith('-'):
            flag = arg[1:]
            if flag in self.short_options:
                internal_name = self.short_options[flag]
            elif allow_unknown:
                self.unknown_args.append(arg)
                return use_next
            else:
                self._cmdline_err("unknown flag '{}" \
                    .format(arg))

            if next_arg is not None:
                next_m = cmdline_arg_regex.fullmatch(next_arg) # XXX always matches
            if internal_name in self.boolean_options:
                val = True
            elif next_arg is not None and m.group('prefix') is not None:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(arg))
            elif next_arg is not None:
                val, use_next = next_arg, True
            else:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(arg))
        elif arg.startswith('+'):
            if self.script_name is not None:
                # <TODO>: Support specifying a script for some subcommands e.g. 'add'.
                # TODO: Support chained scripts; for now, signal error.
                self._cmdline_err("redundant script specifier '{}'" \
                    .format(arg))
            # TODO: Sanitize, strip '+', here or in find_script?
            self.script_name = args[i]
            return use_next
        elif m.group('keyword') is not None:
            # handle '--keyword=arg', 'keyword=arg'
            flag = m.group('keyword')
            if flag in self.long_options:
                internal_name = self.long_options[flag]
            elif flag in self.default_values: # <TODO>: Also accept sanitized internal_name.
                internal_name = flag
            val = m.group('arg')
        elif m.group('prefix') is not None:
            # handle '--keyword'
            flag = m.group('arg')
            if flag in self.long_options:
                internal_name = self.long_options[flag]
            elif flag in self.default_values: # <TODO>: Also accept sanitized internal_name.
                internal_name = flag
            elif allow_unknown:
                self.unknown_args.append(arg)
                return use_next
            else:
                self._cmdline_err("unknown flag '{}'" \
                    .format(flag))

            if next_arg is not None:
                next_m = cmdline_arg_regex.fullmatch(next_arg) # XXX always matches
            if internal_name in self.boolean_options \
                and flag in self.boolean_flags:
                val = self.boolean_flags[flag]
            elif next_arg is not None and internal_name in self.boolean_options:
                val = True
            elif next_arg is not None and m.group('prefix') is not None:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(flag))
            elif next_arg is not None:
                val, use_next = next_arg, True
            else:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(flag))
        elif allow_unknown:
            # <TODO>: Handle positional argument.
            self.unknown_args.append(arg)
            return use_next
        else:
            # <TODO>: Handle positional argument.
            self._cmdline_err("unexpected positional argument '{}'" \
                .format(arg))

        self.set_option(internal_name, val, 'cmdline')
        return use_next

    def parse_cmdline(self, args, allow_unknown=False):
        """Parse a set of command line arguments.

        Args:
            args (list of str): Command line arguments.
            allow_unknown (bool, optional): Collect any unrecognized arguments
                in the 'unknown_args' field instead of signaling an error.
                Defaults to False.

        Returns:
            self
        """
        i = 0
        while i < len(args):
            arg, next_arg = args[i], None
            if i + 1 < len(args):
                next_arg = args[i+1]
            use_next = self._proc_cmdline_arg(arg, next_arg, allow_unknown)
            i += 2 if use_next else 1
        return self

    def parse_cgi_query(self, form):
        """Parse a set of CGI options.

        Args:
            form (cgi.FieldStorage): CGI options.

        Returns:
            self
        """
        for key, internal_name in self.cgi_options.items():
            if key not in form:
                continue
            self.set_option(internal_name, form[key], 'cgi')
        return self

    def print_help(self):
        """<TODO: Docstring.>"""
        self.usage = None # TODOXXX should configure from script
        print("TODO: print help")
        pass # TODOXXX

    # TODO fields and methods for a testruns/testlogs query
    # -- if the Git commands are any guide, queries can get very complex
    # but all the particular script might care about is 'list of testruns'

    # TODO fields and methods for proper output in different formats
    # -- can take a formatter object from format_output.py?
    # -- or should we import format_output.py directly?

    def get_list(self, key, default=None):
        """Parse an option that was specified as a comma-separated list."""
        if key not in self.__dict__ or self.__dict__[key] is None:
            return default
        if isinstance(self.__dict__[key], list): # XXX already parsed
            return self.__dict__[key]
        items = []
        for val in self.__dict__[key].split(","):
            if val == "": continue
            items.append(val.strip())
        return items

    def _show_results(self):
        """For debugging: print the contents of this BunsenOptions object."""
        print("OBTAINED OPTIONS")
        # TODOXXX Filter by active options groups:
        for key, val in self.__dict__.items():
            if key in _options_base_fields:
                continue
            print ("{} = {} ({})".format(key, val, self.source[key]))
        print()

    # TODO logic for checking cgi_safe functionality

# Options for bunsen:
BunsenOptions.add_option('bunsen_dir', group='bunsen',
    cmdline='repo', env='BUNSEN_DIR', nonconfig=True, default=None,
    help="Path to Bunsen repo.")
BunsenOptions.add_option('bunsen_git_repo', group='bunsen',
    cmdline='git-repo', env='BUNSEN_GIT_REPO', default=None,
    help="Path to Bunsen repo; default '$(bunsen_dir)/bunsen.git'.")
BunsenOptions.add_option('bunsen_dir_name', group='bunsen',
    default=None,
    help="Name of Bunsen repo to search for; default '.bunsen'.")
BunsenOptions.add_option('bunsen_default_repo', group='bunsen',
    default=None, # XXX default=$HOME/.bunsen
    help="Path to the default Bunsen repo.")
BunsenOptions.add_option('use_bunsen_default_repo', group='bunsen',
    cmdline='default-repo', boolean=True,
    help="Use the default Bunsen repo if a Bunsen repo is not found.")
BunsenOptions.add_option('config_path', group='bunsen',
    cmdline='config', nonconfig=True, default=None,
    help="Path to config file; default '$(bunsen_dir)/config'.")

# Options for {init,commit}:
BunsenOptions.add_option('git_user_name', group={'init','commit'},
    cmdline='git-user-name', cgi='git_user_name', default='bunsen',
    help="Username to use for Git commits to the Bunsen repo.")
BunsenOptions.add_option('git_user_email', group={'init', 'commit'},
    cmdline='git-user-email', cgi='git_user_email', default='unknown@email',
    help="Email to use for Git commits to the Bunsen repo.")
# XXX: These git options are set for working directory clones, not for
# the main Bunsen git repo. Changing them does not affect an already cloned
# working directory.

# Options for output:
# TODOXXX output_format=json,html,console,log

# Options for ???:
# TODOXXX name of bunsen branch to check out?

# TODOXXX add --help command line option
BunsenOptions.add_option('should_print_help', group=None,
    cmdline='help', nonconfig=True, boolean=True, default=False,
    help="Show this help message")
