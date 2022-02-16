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
from pathlib import Path, PurePath
from configparser import ConfigParser
import tarfile
import shutil
import subprocess
import git
import git.exc

from bunsen.model import *
from bunsen.utils import *

# <TODO>: Bunsen.init_repo should create a blank 'index' branch so +list_runs works immediately

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
        except Exception as e:
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
    # skip_empty (bool, optional): Don't attempt to create the
    #     commit if it would be empty, but instead return None.
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
        # <TODO: Handle skip_empty here.>

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

# A Bunsen repo is a directory that contains the following:
# - bunsen.git/ -- a bare git repo with the following branching scheme:
#   * index
#     - <commit> '<project>/testruns-<year>-<month>-<optional extra>: ...'
#       - <project>-<year>-<month>.json (append to existing data)
#   * <project>/testruns-<year>-<month>-<optional extra>
#     - <commit> '<project>/testruns-<year>-<month>-<optional extra>: ...'
#       - <project>-<id>.json (<id> references a commit in testlogs branch)
#   * <project>/testlogs-<year>-<month>
#     - <commit> '<project>/testlogs-<year>-<month>: ...'
#       - testlogs from one test run (must remove previous commit's testlogs)
# - cache/ -- TODO will contain scratch data for Bunsen scripts, see e.g. +find_regressions
# - config -- git style INI file, with sections:
#   - [core]            -- applies always
#   - [<script_name>]   -- applies to script +<script_name> only
#   - [project "<project>"] -- applies to any script running on project <project>
#   - TODOXXX [bunsen-push] -- should also apply to 'bunsen add'
#   - TODOXXX [bunsen-push "<project>"] -- should also apply to 'bunsen add'
# - scripts/ -- a folder for user-contributed scripts

class Bunsen:
    """Represents a Bunsen repo.

    Provides methods to query and manage Testruns and Testlogs within the repo
    and to run analysis scripts.

    Attributes:
        base_dir (Path): Path to the top level directory of the Bunsen repo.
        git_repo_path (Path): Path to the Bunsen git repo.
            Defaults to 'bunsen.git' within base_dir.
        git_repo (git.Repo): A git.Repo object representing the Bunsen git repo.
        cache_dir (Path): Path to the Bunsen analysis cache. 
            Defaults to 'cache' within base_dir.
        scripts_search_path (list of Path): Paths that will be searched to
            find a Bunsen analysis script.
        default_pythonpath (list of Path): Library paths for the Bunsen
            analysis script invocation.
    """

    def __init__(self, base_dir=None, args=None,
                 script_name=None, info=None,
                 options=None, old_default_options=None,
                 required_args=[], optional_args=[],
                 repo=None, alternate_cookie=None):
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
            info (str, optional): Help string for the command or analysis script.
            options (BunsenOptions, optional): BunsenOptions object that
                represents options for the command or analysis that will be
                invoked on this repo.
                If specified, the initializer will not check for config
                files or environment variables.
                Configuration values from the  base_dir, args, and script_name
                arguments will override any values in the options object.
                Note that the original options object will be modified
                and extended with any missing default values.
            old_default_options (dict, optional): To be deprecated.
                A description of the expected default options in the old
                cmdline_args() format.
            required_args (list of str, optional): List of required
                command line arguments, which can be specified as positional
                arguments.
            optional_args (list of str, optional): List of optional
                command line arguments, which can be specified
                as positional arguments after required_args.
            repo (git.Repo, optional): A git.Repo object which will be
                used as the Bunsen git repo.
            alternate_cookie (str, optional): A string to append to
                the name of the working directory checked out by this
                Bunsen instance.
                For example, the Bunsen object in an analysis script
                forked by a Bunsen command invocation could receive
                the PID of the command invocation as its
                alternate_cookie.
        """

        # Read configuration in several stages.

        # (0a) Initialize new or pre-existing BunsenOptions object:
        if options is None:
            self._opts = BunsenOptions(bunsen=self, script_name=script_name)
        else:
            self._opts = options
            self._opts._bunsen = self

        # (0b) Set script_name, info:
        if script_name is not None:
            self._opts.script_name = script_name
        if info is not None:
            self._opts.usage_str = info
        if self._opts.script_name is None and 'BUNSEN_SCRIPT_NAME' in os.environ:
            self._opts.script_name = os.environ['BUNSEN_SCRIPT_NAME']
        initializing = ( self._opts.script_name == 'init' )

        # (1) Parse environment variables:
        if options is None:
            self._opts.parse_environment(os.environ)
        else:
            # XXX: Skip if pre-existing BunsenOptions are provided.
            pass

        # XXX These are not documented options, but are set by a
        # parent Bunsen process forking an analysis script to specify
        # how to initialize the workdir:
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
        # <TODOXXX: Add to docstring.>

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
            # TODO: Better option to configure allow_unknown here, e.g. self._opts.script_name == 'run'
            self._opts.parse_cmdline(args, required_args, optional_args,
                                     allow_unknown=(self._opts.script_name is None))

        # (4) Identify Bunsen repo location:
        if base_dir is not None:
            self._opts.bunsen_dir = str(base_dir)
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

        # (6) Check if all required args were provided:
        self._opts.check_required()
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

        # XXX: We check self.base_dir, BUNSEN_SCRIPTS_DIR for scripts
        # within 'scripts-' subfolders. Other directories in
        # scripts_search_path may contain analysis scripts directly.
        self.scripts_search_path = [str(self.base_dir), str(BUNSEN_SCRIPTS_DIR)]
        if self._opts.scripts_search_path is not None:
            # TODOXXX Here and elsewhere, replace print -> log_print
            print("got scripts_search_path", self._opts.scripts_search_path)
            extra_paths = self._opts.get_list('scripts_search_path')
            self.scripts_search_path = extra_paths + self.scripts_search_path
        self._opts.scripts_search_path = self.scripts_search_path

        # <TODO>: Old calculations, replace when improving run_script?
        self.default_pythonpath = [BUNSEN_SCRIPTS_DIR]
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
                        self.default_pythonpath.append(Path(candidate_path))
                        next_search_path.append(candidate_path)
            search_path = next_search_path
        # TODO: Also allow invoking Python scripts from shell scripts via $PATH.

        # XXX Use for (optional) duplicate detection when making commits?
        # Experiments so far show no benefit over linear scan of branches.
        #self._known_hexshas = {} # maps hexsha -> git.Objects.Commit

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
            and self._opts.sources['use_bunsen_default_repo'] != 'config':
            self.base_dir = Path(self._opts.bunsen_default_repo).resolve()
            return

        if initializing and self._opts.bunsen_dir_name is not None:
            self.base_dir = (Path() / self._opts.bunsen_dir_name).resolve()
            return

        if initializing:
            self.base_dir = (Path() / ".bunsen").resolve()
            return

        base_dirs = []
        base_dirs.append(Path()) # current working directory
        base_dirs.append(Path(git_toplevel())) # top level of current git repo
        # <TODO> top level of Bunsen source checkout -- if not installed?

        dir_names = ['.bunsen', 'bunsen-data']
        if self._opts.bunsen_dir_name is not None:
            dir_names = [self._opts.bunsen_dir_name] + dir_names

        for base_dir in base_dirs:
            for dir_name in dir_names:
                cand_path = Path(base_dir) / dir_name
                if cand_path.is_dir():
                    self.base_dir = cand_path.resolve()
                    return

        # Final fallback using bunsen_default_repo:
        if self._opts.use_bunsen_default_repo:
            # <TODO> Print a warning.
            self.base_dir = Path(self._opts.bunsen_default_repo).resolve()
            return

    @property
    def script_name(self):
        """The name of the command or analysis script being invoked on this
        repo.

        Will be None in the parent Bunsen process that is forking an
        analysis script. TODO: Probably better to have it be 'run' in
        that case?
        """
        return self._opts.script_name

    # TODOXXX: Deprecating, currently used by a couple of commit_logs scripts
    # which need a synthetic BunsenOptions object. Change to a b.opts @property.
    def opts(self, defaults={}):
        if isinstance(defaults, list):
            # XXX Handle new cmdline_args format:
            args = defaults; defaults = {}
            for t in args:
                name, default_val, cookie, description = t
                BunsenOptions.add_option(name,
                                         default=default_val,
                                         help_str=description,
                                         help_cookie=cookie,
                                         override=True)
                defaults[name] = default_val
        assert(isinstance(defaults, dict))
        for name, value in defaults.items():
            self._opts.set_option(name, value, 'default')
        return self._opts

    # TODOXXX: Deprecating, currently used by most scripts.
    # TODO: Document recognized default and cookie types,
    # and move to the BunsenOptions.add_option() scheme.
    def cmdline_args(self, argv, usage=None, info=None, args=None,
                     required_args=[], optional_args=[],
                     defaults={}, use_config=True, check_required=True,
                     allow_unknown=False):
        '''Used by analysis scripts to pass command line options.

        Supports two formats:
        - usage=str defaults={'name':default_value, ...}
        - info=str args=list of tuples ('name', default_value, 'cookie', 'Detailed description')

        The second format automatically generates a usage message
        which includes argument descriptions in the form
        "name=cookie \t Detailed description."

        TODO: This method will be deprecated in favour of
        Bunsen.from_cmdline(). That's because the Bunsen __init__
        method will also need to see the command line arguments
        to determine things such as custom configuration locations.'''

        if usage is not None:
            self._opts.usage_str = usage
        if info is not None:
            self._opts.usage_str = info

        # Handle +script_name --help. XXX argv is assumed to be sys.argv
        if len(argv) > 1 and (argv[1] == '-h' or argv[1] == '--help'):
            # <TODO>: Harmonize info and usage.
            # <TODOXXX>: Pass required_args, optional_args.
            # self._opts.print_help()
            self._opts._print_usage(info, args, usage,
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

        opts = self.opts(args if args is not None else defaults)
        opts.parse_cmdline(argv, required_args, optional_args, allow_unknown=allow_unknown)
        if check_required:
            opts.check_required()
        if self._opts.should_print_help:
            self._opts.print_help()
            exit()
        return opts

    @classmethod
    def from_cmdline(cls, args=None, info=None, required_args=[],
                     optional_args=[], script_name=None):
        """Initialize objects representing a Bunsen repo and command invocation.

        This method takes a set of command line arguments.

        Args:
            args (list of str): Command line options for the command
                or analysis script being invoked. If None, sys.argv
                will be used.
            info (str, optional): Help string for the command or analysis script.
            required_args (list of str, optional): List of required
                command line arguments, which can be specified as positional
                arguments.
            optional_args (list of str, optional): List of optional
                command line arguments, which can be specified
                as positional arguments after required_args.
            script_name (str, optional): name of the command
                or analysis script being invoked.

        Returns:
            Bunsen, BunsenOptions
        """
        if args is None:
            args = sys.argv
        b = Bunsen(args=args, info=info,
                   required_args=required_args, optional_args=optional_args,
                   script_name=script_name)
        # b._opts._show_results() # XXX for debugging purposes
        return b, b._opts

    @classmethod
    def from_cgi_query(cls, form):
        """Initialize objects representing a Bunsen repo and command invocation.

        This method takes a set of arguments from a CGI query.

        Args:
            form (cgi.FieldStorage): CGI form arguments for the command
                or analysis script being invoked.
        """
        # <TODO: Configure Bunsen input/output for CGI logging.>
        b = Bunsen()
        b._opts.parse_cgi_query(form)
        return b, b._opts

    ##############################################
    # Methods for querying testlogs and testruns #
    ##############################################

    @property
    def tags(self):
        """Deprecated. Use Bunsen.projects() instead."""
        return self.projects

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
            if m.group('kind') == 'runs':
                found_testruns.add(project)
            if m.group('kind') == 'logs':
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
                found_projects.append(project)
            elif not warned_indexfiles:
                warn_print("found project '{}' but no indexfiles in branch index" \
                    .format(project))
                warned_indexfiles = True

        return found_projects

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
        return Index(self, project, key_function=key_function, reverse=reverse)

    def testrun(self, testrun_or_commit_id, project=None,
                summary=False, raise_error=True):
        """Retrieve a Testrun from the repo.

        <TODO: More complex queries should be supported by BunsenOptions.>

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
                <TODO: Testrun() should strip other fields if other fields
                are of testcases type.>
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
            candidate_branches.append(extra_info.bunsen_testruns_branch)

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
            except git.exc.BadName: # XXX gitdb.exc.BadName
                continue # skip any nonexistent branch
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
    # TODOXXX OLD IMPLEMENTATION, need to doublecheck
        # bunsen_testruns_branch = None
        # if isinstance(testrun_or_commit_id, Testrun) \
        #    and 'bunsen_testruns_branch' in testrun_or_commit_id:
        #     bunsen_testruns_branch = testrun_or_commit_id.bunsen_testruns_branch
        # commit_id = testrun_or_commit_id.bunsen_commit_id \
        #     if isinstance(testrun_or_commit_id, Testrun) else testrun_or_commit_id

        # commit = self.git_repo.commit(commit_id)
        # testlog_hexsha = commit.hexsha
        # #dbug_print("found testlog commit", testlog_hexsha, commit.summary)
        # alt_tag, year_month, extra_label = self.commit_tag(commit=commit) # TODOXXX use extra_label
        # tag = tag or alt_tag

        # # XXX Search branches with -<extra>, prefer without -<extra>:
        # if bunsen_testruns_branch is not None:
        #     possible_branch_names = [bunsen_testruns_branch]
        # else:
        #     # TODOXXX: If bunsen_testruns_branch is not specified, should try to read json from the commit's commit_msg.
        #     # XXX If bunsen_testruns_branch is not specified and the commit's commit_msg has no json, the final (slow) fallback will search *all* branches with -<extra>
        #     # while preferring the branch without -<extra>.
        #     # This creates visible latency in analysis scripts.
        #     default_branch_name = tag + '/testruns-' + year_month
        #     possible_branch_names = [default_branch_name]
        #     for branch in self.git_repo.branches:
        #         if branch.name != default_branch_name \
        #            and branch.name.startswith(default_branch_name):
        #             possible_branch_names.append(branch.name)
        # for branch_name in possible_branch_names:
        #     try:
        #         commit = self.git_repo.commit(branch_name)
        #     except Exception: # XXX except gitdb.exc.BadName
        #         continue
        #     #dbug_print("found testrun commit", commit.hexsha, commit.summary) # check for HEAD in branch_name
        #     try:
        #         blob = commit.tree[tag + '-' + testlog_hexsha + '.json']
        #         break
        #     except KeyError:
        #         continue
        # return Testrun(self, from_json=blob.data_stream.read(), summary=summary)

    # <TODO: Go through the scripts and change
    #     testrun = ...
    #     testrun = b.full_testrun(testrun)
    # to
    #     testrun_summary = ...
    #     testrun = b.full_testrun(testrun_summary)
    # for further readability.>
    #
    # <TODO: Testrun class should have a summary testrun
    # load 'testcases' on demand, when the field is accessed.
    # Then the above pattern is not necessary.>
    def full_testrun(self, testrun_or_commit_id, project=None, summary=False):
        """Given a summary Testrun, retrieve the corresponding full Testrun.

        (This method is an alias of testrun(), provided for readability.)
        """
        return self.testrun(testrun_or_commit_id, project, summary)

    def testlog(self, testlog_path, commit_id=None,
                input_stream=None):
        """Retrieve Testlog from repo or create Testlog for external log file.

        <TODO: More complex queries should be supported by BunsenOptions.>

        <TODO> BunsenOptions query should suppoert
        testlog_id='<commit>:<path>', commit_id=None -- <path> in <commit>.

        Args:
            testlog_path (str or Path or PurePath): Path of the log file
                within the Bunsen git tree,
                or path to an external log file.
            commit_id (str, optional): Commit which stores the log file
                within a testlogs branch of the Bunsen git repo,
                or None for an external log file.
            input_stream (optional): Seekable stream for an external log file.
        """
        if commit_id is None:
            return Testlog(self, path=testlog_path, input_stream=input_stream)
        assert input_stream is None # no input_stream for a testlog in the repo
        commit = self.git_repo.commit(commit_id)
        blob = commit.tree[testlog_path]
        return Testlog(self, path=testlog_path, commit_id=commit_id, blob=blob)

    # Provides a way for separate Testlogs referencing the same log file
    # to avoid redundant reads of that log file:
    # TODO: Doublecheck argument ordering.
    def _testlog_readlines(self, commit_id, path):
        if (commit_id, path) in self._testlog_lines:
            return self._testlog_lines[(commit_id, path)]
        commit = self.git_repo.commit(commit_id)
        blob = commit.tree[str(path)]
        lines = readlines_decode(blob.data_stream, must_decode=False)
        # XXX to localize errors, decode utf-8 later in Testlog.line()
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

        If there are multiple staged testruns, this will all refer to
        the same testlog commit. This behaviour will be useful for
        data sources such as the GCC Jenkins server, which includes
        testsuites from several projects in the same set of test logs.
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
                If omitted, will default to the top level of the Git tree.
        """
        if path is None and isinstance(source, Testlog):
            path = source.path.name
        elif path is None and isinstance(source, tarfile.ExFileObject):
            pass # XXX no info available
        elif path is None: # str or Path-like object
            path = PurePath(source).name # XXX store at top level
        testlog = Testlog.from_source(source, path)
        self._staging_testlogs.append(testlog)

    def add_testrun(self, testrun):
        """Stage a Testrun to commit to the Bunsen repo."""
        self._staging_testruns.append(testrun)

    def reset_all(self):
        """Remove all staged Testlog and Testrun objects."""
        self._staging_testlogs = []
        self._staging_testruns = []

    # Save a full representation of the testrun into path.
    # Overwrites any existing testrun summary file at that path.
    # Return True if an existing summary was overwritten.
    def _serialize_testrun(self, testrun, json_path):
        updated_testrun = Path(json_path).is_file()
        with open(str(json_path), 'w') as out:
            out.write(testrun.to_json())
        return updated_testrun

    # Insert a summary of the testrun into an indexfile at index_path.
    # Overwrites any existing testrun summary with the same bunsen_commit_id
    # within the file, while leaving other testrun summaries intact.
    # Return True if an existing summary was overwritten.
    def _serialize_testrun_summary(self, testrun, project, index_path):
        updated_testrun, need_update_index = False, False
        update_path = str(index_path) + "_UPDATING"
        updated_testruns = []

        try:
            # Load the existing indexfile:
            index = Index(self, project, index_source=index_path)
            index_iter = index.iter_raw()

            # Scan the indexfile to determine how to update the contents:
            found_matching = False
            for json_str in index_iter:
                other_run = Testrun(self, from_json=json_str, summary=True)
                next_run_str = json_str
                if other_run.bunsen_commit_id == testrun.bunsen_commit_id and found_matching:
                    warn_print("duplicate/multiple testrun summaries" \
                        "found in {} (bunsen_commit_id={})" \
                        .format(Path(index_path).name,
                            other_run.bunsen_commit_id))
                elif other_run.bunsen_commit_id == testrun.bunsen_commit_id:
                    next_run_str = testrun.to_json(summary=True)
                    found_matching = True
                    # Only change the file if the testrun data has changed:
                    if next_run_str != json_str:
                        updated_testrun = True # will replace other_run
                        need_update_index = True
                updated_testruns.append(next_run_str)
        except OSError as err: # index does not exist yet
            if Path(index_path).is_file():
                warn_print("unexpected error when opening {}: {}" \
                    .format(index_path, err))
                raise BunsenError("giving up to avoid losing data")
                return # <TODOXXX>: Should append rather than overwrite, just to be safe.

        if not found_matching:
            next_run_str = testrun.to_json(summary=True)
            updated_testruns.append(next_run_str)
            need_update_index = True

        # Copy the index file to update_path:
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
                branch name for the Testlog objects. Can improve the efficiency
                of querying the Bunsen repo for projects that receive a very
                large number of testruns in one month.
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
        if not self._staging_testruns: # XXX empty
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

        # Validate and adjust Testrun data:
        found_primary_testrun = False
        related_testrun_refs = []
        for testrun in self._staging_testruns:
            # Validate/populate metadata; commit even if there are problems,
            # unless we are not able to fill in mandatory metadata:
            testrun.validate(project, year_month, extra_label,
                cleanup_metadata=True)
            # XXX This clears extra_label, but we don't care since we
            # only use the already obtained extra_label from the
            # primary testrun.

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
            if testrun_year_month != year_month:
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
        if self._staging_testlogs: # XXX not empty
            assert testlogs_commit_id is None # can't specify existing commit if there are staged testlogs
            testruns_branch_name = primary_testrun.bunsen_testruns_branch
            testlogs_branch_name = primary_testrun.bunsen_testlogs_branch
            wd.checkout_branch(testlogs_branch_name, skip_redundant_checkout=True)
            wd.clear_files() # remove log files from previous commit
            for testlog in self._staging_testlogs:
                testlog.copy_to(wd.working_tree_dir)
            commit_msg = testlogs_branch_name
            commit_msg += ": testsuite run with {} testlogs" \
                .format(len(self._staging_testlogs))
            # If the full testrun summary is included here, it may end
            # up being out of date. So now we only include its
            # bunsen_testruns_branch field, which is sufficient for
            # finding the rest of the summary:
            extra_info = {'bunsen_testruns_branch': testruns_branch_name}
            # TODO: Maybe also add related_testrun_refs to extra_info?
            extra_info = Testrun(self, from_json=extra_info, summary=True)
            commit_msg += INDEX_SEPARATOR
            commit_msg += extra_info.to_json(summary=True) # don't validate
            testlogs_commit_id = wd.commit_all(commit_msg,
                # reuse existing log files if possible:
                allow_duplicates=allow_duplicates)
        assert testlogs_commit_id is not None # must specify existing commit if no testlogs were staged
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
            updated_testrun = self._serialize_testrun(testrun, \
                Path(wd.working_tree_dir) / json_name)
            commit_msg = testrun_branch_name
            updating_testrun_str = "updating " if updated_testrun else ""
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
            updated_index = self._serialize_testrun_summary(testrun, \
                testrun_project, Path(wd.working_tree_dir) / json_name)
            commit_msg = testrun_branch_name
            updating_index_str = "updating " if updated_index else ""
            commit_msg += ": {}summary index for commit {}" \
                .format(updating_index_str, testrun.bunsen_commit_id)
            # Don't make a commit if nothing was changed:
            # <TODOXXX>: wd.commit_all(commit_msg, skip_empty=True)
            wd.commit_all(commit_msg)

        if push:
            # <TODO>: Doublecheck that we're only pushing what was modified....
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

    #####################################################
    # Methods for managing the Bunsen repo and workdirs #
    #####################################################

    def _init_git_repo(self):
        self.git_repo = git.Repo.init(str(self.git_repo_path), bare=True)

        # Create initial commit to allow branch creation:
        cloned_repo = self.checkout_wd('master', checkout_name="wd-init-repo")
        initial_file = os.path.join(cloned_repo.working_tree_dir, '.bunsen_initial')
        gitignore_file = os.path.join(cloned_repo.working_tree_dir, '.gitignore')
        open(initial_file, mode='w').close() # XXX empty file
        with open(gitignore_file, mode='w') as f: f.write('.bunsen_workdir\n')
        cloned_repo.index.add([initial_file, gitignore_file])
        commit_msg = "bunsen init: initial commit to allow branch creation"
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
        if not (self.base_dir / "config").is_file():
            # <TODO> If self.config_path is different, copy config?
            open(str(self.base_dir / "config"), mode='w').close() # XXX empty file
            # <TODO> Fill in some default config values?
        else:
            found_existing = True
        _scripts_path = self.base_dir / "scripts"
        if not _scripts_path.is_dir():
            _scripts_path.mkdir()
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
            branch_name (str, optional): Name of branch to check out.
                Defaults to the default branch set from the BUNSEN_BRANCH
                environment variable. <TODO>: self.default_branch_name
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
        if branch_name is None and self.default_branch_name:
            branch_name = self.default_branch_name
        elif branch_name is None:
            raise BunsenError('no branch name specified for checkout (check BUNSEN_BRANCH environment variable)')

        if checkout_name is None and self.default_work_dir:
            # <TODO>: default_work_dir should be a Path?
            checkout_name = os.path.basename(self.default_work_dir)
        elif checkout_name is None:
            # sanitize the branch name
            checkout_name = "wd-" + branch_name.replace('/','-')

        if checkout_path is None and self.default_work_dir:
            checkout_path = os.path.dirname(self.default_work_dir)
        elif checkout_path is None:
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
            # <TODO>: Should we write the PPID in a forked child script?

        # Git config for the cloned repo:
        with wd.config_writer() as cw:
            if not cw.has_section('user'): cw.add_section('user')
            cw.set('user', 'name', self._opts.git_user_name)
            cw.set('user', 'email', self._opts.git_user_email)

        # XXX Special case for an empty repo with no branches to checkout:
        if not wd.heads and \
           branch_name != 'master' and branch_name is not None:
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

    # TODO def clone_repo
    # TODO def pull_repo; 'bunsen pull' should also pull log sources

    #####################################################
    # Methods for locating and running analysis scripts #
    #####################################################

    # Used by find_script to choose preferred subdirectories:
    _script_type_ranking = ['scripts-master', 'scripts-host',
                            'scripts-guest', None]

    # Search through parent directories of script_path:
    def _get_script_type(self, script_path):
        while script_path.parent != script_path:
            if script_path.name in \
               {'scripts-master', 'scripts-host', 'scripts-guest'}:
                return script_path.name
            script_path = script_path.parent
        return None

    def _get_script_rank(self, script_type):
        try:
            return self._script_type_ranking.index(script_type)
        except ValueError:
            # XXX assume None
            return len(self._script_type_ranking) - 1

    def find_script(self, script_name):
        """Find an analysis script with the specified name or path.

        Returns:
            Path
        """

        # XXX Strip initial '+':
        if script_name.startswith('+'):
            script_name = script_name[1:]

        # (0) The script could be specified via absolute or relative path:
        if script_name.startswith('.') or script_name.startswith('/'):
            script_path = Path(script_name).resolve()
            if not script_path.is_file():
                raise BunsenError("Could not find script '{}'" \
                                  .format(script_name))
            return script_path

        # (1a) Otherwise, the script could be specified by name:
        candidate_names = [script_name,
                           script_name + '.sh',
                           script_name + '.py']

        # PR25090: Allow e.g. +commit-logs instead of +commit_logs:
        script_name2 = script_name.replace('-','_')
        candidate_names += [script_name2,
                            script_name2 + '.sh',
                            script_name2 + '.py']

        # (1b) Collect candidate script directories:
        candidate_dirs = []
        parent_dirs = {str(self.base_dir), str(BUNSEN_SCRIPTS_DIR)}
        curr_search_dirs = list(self.scripts_search_path)
        while len(curr_search_dirs) > 0:
            candidate_dir = curr_search_dirs.pop(0)

            # XXX: Allow globs in scripts_search_path.
            if isinstance(candidate_dir, str):
                matching_dirs = list(map(Path,glob.glob(candidate_dir)))
                # TODOXXX: Fix below.
                curr_search_dirs = matching_dirs + curr_search_dirs
                continue

            if not Path(candidate_dir).is_dir():
                continue

            if str(candidate_dir) not in parent_dirs:
                candidate_dirs.append(Path(candidate_dir))

            # XXX Search recursively in directories
            # named 'scripts' or starting with 'scripts-', e.g.
            # 'scripts-internal/scripts-master/':
            next_search_dirs = []
            candidate_subdirs = Path(candidate_dir).iterdir()
            for candidate_subdir in candidate_subdirs:
                if candidate_subdir.name != 'scripts' \
                   and not candidate_subdir.name.startswith('scripts-'):
                    continue
                if not candidate_subdir.is_dir():
                    continue
                next_search_dirs.append(candidate_subdir)
            curr_search_dirs = next_search_dirs + curr_search_dirs

        # (1c) Collect matching scripts in candidate directories:
        scripts_found = []
        all_search_dirs = []
        for candidate_dir in candidate_dirs:
            if not candidate_dir.is_dir():
                continue

            all_search_dirs.append(candidate_dir)

            # XXX: Allow script_name to be a relative path.
            # e.g. 'scripts-master/examples/hello_python.py'
            # can be invoked as '+examples/hello-python':
            for candidate_name in candidate_names:
                candidate_path = candidate_dir / candidate_name

                # XXX: May want to check for executable permissions.
                # Then again, returning a non-executable file avoids
                # confusing behaviour when the user tries to override
                # a bundled analysis script but forgets to set permissions.
                if candidate_path.is_file():
                    scripts_found.append(candidate_path)

        if len(scripts_found) == 0:
            search_paths = "\n- " + "\n- ".join(list(map(str,all_search_dirs)))
            if search_paths.endswith("\n- "):
                search_paths = search_paths[:-3]
            raise BunsenError("Could not find analysis script '+{}'\n" \
                              "Search paths:{}" \
                              .format(script_name, search_paths))

        # (2) Prioritize among scripts_found:
        # - 'scripts-master' directories are preferred
        # - TODO(rx): 'scripts-guest' and 'scripts-host' are reserved for
        #   a hypothetical remote-execution feature.
        fallback_script_path = scripts_found[0]
        preferred_script_path, preferred_script_type = None, None
        for script_path in scripts_found:
            script_type = self._get_script_type(script_path)

            # TODO(rx): Here we'd also check the target host for this command.
            if script_type == 'scripts-guest':
                # TODO(rx): We may want to be a lot more strict about this,
                # to the point of changing fallback_script_path.
                # The guest scripts might be destructive operations
                # meant to be executed inside a one-off test VM:
                continue

            # Otherwise we prefer:
            # (1) user's custom scripts override bunsen default scripts
            #     i.e. prefer scripts earlier in the search path
            # (2) prefer scripts-master/ over scripts-host/ over scripts-guest/
            #
            # - Preference (1) allows the user to customize a bundled
            #   script by copying it to .bunsen/scripts-whatever and
            #   editing it.
            # - TODO(rx): Preference (2) allows a guest script
            #   e.g. scripts-guest/my-testsuite.sh
            #   to be wrapped by a script which does additional prep on the host
            #   e.g. scripts-host/my-testsuite.py --with-patch=prNNNN.patch
            script_rank = self._get_script_rank(script_type)
            preferred_script_rank = self._get_script_rank(preferred_script_type)
            if preferred_script_path is None \
               or script_rank < preferred_script_rank:
                preferred_script_path = script_path
                preferred_script_type = script_type

        if preferred_script_path is None:
            preferred_script_path = fallback_script_path
        return preferred_script_path

    def run_command(self, script_name=None, script_path=None):
        """Run the command identified by script_name or located at script_path
        with the arguments configured when this Bunsen object was
        created. Must specify exactly one of {script_name,script_path}.
        """
        script_path = self.find_script(script_name)
        script_env = self._opts.script_env()
        extra_script_env = {'BUNSEN_DIR': self.base_dir,
                            'BUNSEN_REPO': self.git_repo_path,
                            'BUNSEN_CACHE': self.cache_dir}
        script_env.update(extra_script_env)
        script_args = self._opts.script_args()

        # TODO(rx): Add job control, e.g. fork a long-running task in a tmux.
        # TODO(rx): Remote execution functionality to launch script inside vm.
        rc = subprocess.run([str(script_path)] + script_args, env=script_env)
        # TODO: Check rc and handle any unexpected results?
        # TODO: Make sure command results are cached where appropriate.

    # TODO show_command - show cached results from a command (invoke from bunsen-cgi?)

    # TODO additional Bunsen commands that may be handled by analysis scripts:
    # - 'bunsen add': import tarball, log file, or set of log files
    # - 'bunsen list' / 'bunsen ls': list testruns or log files
    # - 'bunsen show': display testrun, log file, or set of log files
    # - 'bunsen rebuild': regenerate repo to parse (all or subset) of testruns

    # TODOXXX Deprecated. Currently used by 'bunsen run' and bunsen-cgi.py.
    def run_script(self, hostname, script_path, script_args,
                   wd_path=None, wd_branch_name=None, wd_cookie=None,
                   script_name=None):
        script_env = {'BUNSEN_DIR': self.base_dir,
                      'BUNSEN_REPO': self.git_repo_path,
                      'BUNSEN_CACHE': self.cache_dir}

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
        script_env['PATH'] = str(BUNSEN_SCRIPTS_DIR) + ":" + os.environ['PATH']

        # TODO: Configure only when script is written in Python?
        script_env['PYTHONPATH'] = ':'.join([str(p) for p in self.default_pythonpath])

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

# BunsenOptions fields that should not be overwritten by actual options:
_options_base_fields = {
    '_bunsen',
    'script_name',
    'usage_str',
    'expected_groups',
    'sources',
    '_delayed_config',
    '_unknown_args',
    '_required_args',
    '_positional_args',
    '_cmdline_required_args',
    '_cmdline_optional_args',
}

cmdline_arg_regex = re.compile(r"(?P<prefix>--)?(?:(?P<keyword>[0-9A-Za-z_-]+)=)?(?P<arg>.*)")
# XXX Format for compact command line args.

# TODOXXX Proper usage of cmdline_err(fatal=True|False)
class BunsenOptions:
    """Collects options for a Bunsen analysis script invocation.

    Each option has an internal name and possibly some external names
    used to specify that option in different configuration sources
    (command line arguments, config files, environment variables, and
    CGI invocations). The internal name of the option is the name
    of the BunsenOptions instance variable storing the option's value.

    Attributes:
        script_name (str): Name of the analysis script or command.
        usage_str (str): Explanatory string which will be part of the
            usage message output by print_help().
        expected_groups (set): Option groups that will be used by this script.
            Used to generate a usage message in print_help().
        sources (map): Identifies the configuration source of each option.
    """

    # TODO: Wishlist:
    # - Support for chaining commands (one command produces JSON as output to next command).
    # - Support for generating shell autocompletions.
    # - Support for providing options as JSON (parse_json_query e.g. for REST API).

    # XXX The following is weird but IMO preferable to defining 13 class
    # variables to track the metadata defining each option.

    _options = set()
    # Set of internal_name for all defined options.

    _option_names = {}
    # Maps (external_name, option_type) -> internal_name.
    # See docstring for option_name() for details.

    _option_info = {}
    # Maps (internal_name, attr_or_flag) -> value
    # See docstring for option_info() for details.

    _option_groups = {}
    # Maps group_name -> set(internal_name).

    _negated_options = {}
    # Maps cmdline name of negated option to internal name of the original option.

    @classmethod
    def option_name(cls, external_name, option_type):
        """Returns the internal name of a specified option, or
        None if the external name does not reference an option.

        Args:
            external_name (str): External name of the option.
            option_type (str): One of 'cmdline', 'cmdline_short',
                'env', 'cgi', or 'config'.
        """
        if (external_name, option_type) not in cls._option_names:
            return None
        return cls._option_names[external_name, option_type]

    @classmethod
    def option_names(cls, option_type):
        """Iterate all external names of a specified option type.

        Args:
            option_type (str): One of 'cmdline', 'cmdline_short',
                'env', 'cgi', or 'config'.

        Yields:
            (external_name, internal_name)
        """
        for k, internal_name in cls._option_names.items():
            external_name, k_type = k
            if k_type != option_type: continue
            yield external_name, internal_name

    @classmethod
    def option_info(cls, internal_name, attr_or_flag_name):
        """Returns the specified attribute of a specified option.

        Permitted attributes:
        - default_value: default value of the option.
        - help_string: info about the option, used by print_help().
        - help_cookie: placeholder for the option value, used by print_help().

        Permitted flags (value is boolean):
        - nonconfig: the option may not be specified in a config file.
        - boolean_flag: the option controls a boolean flag.
        - accumulate: multiple flags specifying the option will accumulate
          into a list. New options are appended to the end if low priority,
          at the start if high priority, e.g. "o=c,d -oa -ob => [a,b,c,d]".

        TODO: Options with boolean_flag+accumulate should accumulate into
        a number, e.g. -vvvv => -v4.

        Args:
            internal_name (str): Internal name of the option.
            attr_or_flag_name (str): One of the attribute or flag
                names specified above.
        """
        #assert internal_name in cls._options
        if internal_name not in cls._options:
            return None
        return cls._option_info[internal_name, attr_or_flag_name]

    @classmethod
    def option_group(cls, group_name):
        """Returns a set of internal names of options in specified group."""
        return cls._option_groups(group_name)

    @classmethod
    def _add_option_name(cls, option_type, external_name, internal_name,
                        override=False):
        if external_name is None: return
        cls._option_names[external_name, option_type] = internal_name

    @classmethod
    def add_option(cls, internal_name, group=None,
                   cmdline=None, cmdline_short=None,
                   env=None, cgi=None, config=None,
                   nonconfig=False, boolean=False,
                   accumulate=False, default=None,
                   help_str=None, help_cookie=None,
                   override=False):
        """Define an option.

        Args:
            internal_name (str): Internal name of the option.
                Use '_' to separate words, and the command-line parser will
                allow the hyphen '-' to be used interchangeably.
            group (str or set of str, optional): Group or set of groups this
                option belongs to. Can be None (in which case the option is
                assumed to always be accepted).
            cmdline (str, optional): Long command-line flag for the option.
                Can be used in addition to the internal name of the option.
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
            help_str (str, optional): A description of the option.
            help_cookie (str, optional): A 'placeholder' string for
                the option value to be used in the help message.
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
            if group_name not in cls._option_groups:
                cls._option_groups[group_name] = set()
            cls._option_groups[group_name].add(internal_name)

        if internal_name in cls._options and not override:
            warn_print("overriding definition for option '{}'" \
                    .format(internal_name))
        cls._options.add(internal_name)
        if boolean and default is None:
            default = False
        cls._option_info[internal_name, 'default_value'] = default

        cls._option_info[internal_name, 'help_str'] = help_str
        cls._option_info[internal_name, 'help_cookie'] = help_cookie

        cls._add_option_name('cmdline', cmdline, internal_name, override)
        assert(cmdline_short is None or len(cmdline_short) == 1) # short option must be 1char
        cls._add_option_name('cmdline_short', cmdline_short, internal_name, override)
        cls._add_option_name('env', env, internal_name, override)
        cls._add_option_name('cgi', cgi, internal_name, override)
        cls._add_option_name('config', config, internal_name, override)

        cls._option_info[internal_name, 'nonconfig'] = nonconfig
        cls._option_info[internal_name, 'boolean_flag'] = boolean
        cls._option_info[internal_name, 'accumulate'] = accumulate

        # Also generate a negating version for boolean command line options:
        if cmdline is not None and boolean:
            cls._negated_options['no'+internal_name] = internal_name
            cls._negated_options['no-'+internal_name] = internal_name

    # <TODO>: add_legacy_options to wrap from_cmdline?

    def __init__(self, bunsen, script_name=None, usage_str=None, expected_groups=set()):
        """Initialize a BunsenOptions object representing a command.

        Args:
            bunsen (Bunsen): Bunsen repo this command will run against.
            script_name (str, optional): Name of command or analysis script.
            usage_str (str, optional): Help string for the command or analysis script.
            expected_groups (set, optional): Options groups that will be
              used by this command or analysis script.
        """
        self._bunsen = bunsen
        self.script_name = script_name
        self.usage_str = usage_str
        self.expected_groups = expected_groups
        self.expected_groups.update({'bunsen', 'output'})

        # <TODO>: Add a parameter for this.
        self.usage_str = None

        # Set defaults:
        self.sources = {}
        for k, value in self._option_info.items():
            key, attr_or_flag_name = k
            if attr_or_flag_name != 'default_value': continue
            self.set_option(key, value, 'default')

        # Set additional computed defaults:
        self.set_option('bunsen_default_repo', str(Path.home() / ".bunsen"), 'default')

        # Save configuration files & args in case later sections are activated:
        self._delayed_config = []
        self._unknown_args = []

        # Handling required and optional positional args:
        self._positional_args = []
        self._required_args = [] # XXX set by parse_cmdline, check by check_required
        self._cmdline_required_args = [] # XXX set by parse_cmdline, used by print_help
        self._cmdline_optional_args = [] # XXX set by parse_cmdline, used by print_help

    source_priorities = ['args', 'cgi', 'env', 'local', 'global', 'default']
    """Possible sources of options in order of decreasing priority.

    Here:
    - 'args','cgi' represents command line or CGI arguments
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

    # XXX: Could use in Bunsen.__init__ but the existing (natural)
    # code which directly assigns fields will correctly lead to
    # assigned values treated as being from a lowest-priority
    # (unknown) source.
    def set_option(self, key, value, source, allow_unknown=False):
        """Set an option if it wasn't set from any higher-priority source.

        Args:
            key (str): Internal name of the option.
            value (str): New value for the option.
            source (str): The configuration source of the new value.
                Should be an element of BunsenOptions.source_priorities.
            allow_unknown (bool): Allow setting an option that has
                not been defined. Default False."""
        # <TODO> Warn about duplicate options added from the same source?
        if key in _options_base_fields:
            warn_print("attempt to set reserved BunsenOptions field '{}'" \
                    .format(key))
            return
        if key in self.__dict__ and key in self.sources \
           and not BunsenOptions.source_overrides(source, self.sources[key]):
            return # XXX A value exists with higher priority.
        if key not in self._options:
            if source == 'global' or source == 'local':
                # XXX while cmdline_args() is still used, option may not yet be configured
                self.__dict__[key] = value
                self.sources[key] = source
                return
            if source == 'args' and not allow_unknown:
                err_print("unknown option '{}={}'".format(key, value))
                self.print_help()
                exit(1)
            if not allow_unknown:
                # <TODO> name the config file where this option originated from
                warn_print("unknown option '{}={}', skipping".format(key, value))
                return
            self.__dict__[key] = value
            self.sources[key] = source
            return
        if self.option_info(key, 'boolean_flag') or \
           isinstance(self.option_info(key, 'default_value'), bool):
            if value in {'True','true','yes'}:
                value = True
            elif value in {'False','false','no'}:
                value = False
            elif value == 'html' and key == 'pretty':
                value = 'html' # XXX special-case for option pretty=yes|no|html
            elif not isinstance(value, bool):
                warn_print("unknown boolean option '{}={}'".format(key, value))
                return # keep the default value
        elif isinstance(self.option_info(key, 'default_value'), int):
            if value in {'infinity', 'unlimited'}:
                value = -1
            value = int(value)
        self.__dict__[key] = value
        self.sources[key] = source
        # <TODO>: Check for any _delayed_config sections that were activated?

    def add_config(self, section, config=None, is_global=False, is_project=False):
        """Add configuration options from an additional config section.

        Args:
            section (str): Name of the config section to add.
            config (ConfigParser): ConfigParser object representing
               the config file to add options from. If None, will scan
               previously seen config files.
            is_global (bool): If True, the config file provided in the
                config argument is a system-wide config
                file. Prioritize the options lower than options from a
                repo-specific config.
            is_project (bool): If True, the name of the config section
                identifies a project. The config section that will be
                parsed is [project "<section>"].

        """
        if config is None:
            # add sections from all active config files
            for config, kind in self._delayed_config:
                is_global = kind == 'global'
                self.add_config(section, config,
                                is_global=is_global, is_project=is_project)
            return
        if is_project:
            section = 'project "{}"'.format(section)
        if section not in config:
            return
        for key, value in config[section].items():
            if self.option_name(key, 'config') is not None:
                self.set_option(self.option_name(key, 'config'),
                                value, 'global' if is_global else 'local')
            elif not self.option_info(key, 'nonconfig'):
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
            raise BunsenError("configuration file {} not found".format(config_path))

        # section [core], [<script_name>]
        self.add_config('core', config, global_config)
        if self.script_name is not None:
            self.add_config(self.script_name, config, global_config)

        # section [project "<project>"]
        # XXX Load only when the project is unambigous:
        projects = self.get_list('project')
        if projects is not None and len(projects) == 1:
            self.add_config(projects[0], config, global_config, is_project=True)

        # <TODOXXX> handle sections [bunsen-{add,push} {,"<project>"}] -> in bunsen-add, bunsen-upload.py implementations

        # Save config in case other sections are requested later.
        self._delayed_config.append \
            ((config, 'global' if global_config else 'local'))

    def parse_environment(self, env):
        """Parse a set of environment variables.

        Args:
            env (map or environ): Environment variables.

        Returns:
            self
        """
        for external_name, internal_name in self.option_names('env'):
            if external_name in env:
                self.set_option(internal_name, env[external_name], 'env')
            if internal_name in env:
                self.set_option(internal_name, env[internal_name], 'env')
        return self

    def _cmdline_err(self, msg):
        err_print(msg)
        print()
        self.print_help()
        exit(1)

    # TODOXXX update to self. API changes
    def _proc_cmdline_arg(self, arg, next_arg, allow_unknown,
                          accumulate_front=None):
        m = cmdline_arg_regex.fullmatch(arg) # XXX always matches
        internal_name, use_next = None, False
        flag, val = None, None
        is_negating = False
        if len(arg) >= 2 and arg.startswith('-') and not arg.startswith('--'):
            # handle '-o arg', '-oarg'
            flag = arg[1:2]
            val = arg[2:]
            if len(val) == 0:
                val = None

            if self.option_name(flag, 'cmdline_short') is not None:
                internal_name = self.option_name(flag, 'cmdline_short')
            elif allow_unknown:
                self._unknown_args.append(arg)
                return use_next
            else:
                self._cmdline_err("unknown flag '{}'".format(arg)) # TODOXXX Delay towards the end.

            next_m = None
            if val is None and next_arg is not None:
                next_m = cmdline_arg_regex.fullmatch(next_arg) # XXX always matches

            if val is None and self.option_info(internal_name, 'boolean_flag'):
                val = True
            elif val is None and next_m is not None and \
                 next_m.group('prefix') is not None:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(arg)) # TODOXXX Delay towards the end.
            elif val is None and next_arg is not None:
                val, use_next = next_arg, True
            elif val is None:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(arg)) # TODOXXX Delay towards the end.
        elif arg.startswith('+'):
            if self.script_name is not None:
                # <TODO>: Support specifying a script for some subcommands e.g. 'add'.
                # <TODO>: Support chained scripts; for now, signal error.
                self._cmdline_err("redundant script specifier '{}'" \
                    .format(arg)) # TODOXXX Delay towards the end.
            # XXX: The '+' will be stripped by Bunsen.find_script().
            self.script_name = arg
            return use_next
        elif m.group('keyword') is not None:
            # handle '--keyword=arg', 'keyword=arg'
            flag = m.group('keyword')
            val = m.group('arg')
            # PR25090: Allow e.g. +source-repo= instead of +source_repo=
            flag = flag.replace('-','_')
            if self.option_name(flag, 'cmdline') is not None:
                internal_name = self.option_name(flag, 'cmdline')
            elif flag in self._negated_options:
                internal_name = self._negated_options[flag]
                is_negating = True
            elif flag in self._options:
                internal_name = flag
            elif allow_unknown:
                # XXX Set the option, for scripts like bunsen-add
                # that take arbitrary options to set testrun metadata fields.
                self.set_option(flag, val, 'args', allow_unknown=True)
                self._unknown_args.append(arg)
                return use_next
            if is_negating: val = self._negate_arg(val) # TODOXXX also negates str
        elif m.group('prefix') is not None:
            # handle '--keyword', '--keyword arg'
            flag = m.group('arg')
            # PR25090: Allow e.g. +source-repo= instead of +source_repo=
            flag = flag.replace('-','_')
            if self.option_name(flag, 'cmdline') is not None:
                internal_name = self.option_name(flag, 'cmdline')
            elif flag in self._negated_options:
                internal_name = self._negated_options[flag]
                is_negating = True
            elif flag in self._options:
                internal_name = flag
            elif allow_unknown:
                # XXX Don't set option if it was given in GNU format,
                # we don't know if it takes an arg or not.
                self._unknown_args.append(arg)
                return use_next
            else:
                self._cmdline_err("unknown flag '{}'" \
                    .format(flag)) # TODOXXX Delay towards the end.

            if next_arg is not None:
                next_m = cmdline_arg_regex.fullmatch(next_arg) # XXX always matches
            has_next_arg = next_arg is not None and next_m.group('prefix') is None
            if self.option_info(internal_name, 'boolean_flag'):
                # XXX Don't support '--opt-foo yes' in command line args,
                # only 'opt-foo=yes', '--opt-foo=yes',
                # and '--opt-foo/--no-opt-foo'.
                val = True
            elif not has_next_arg:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(flag)) # TODOXXX Delay towards the end.
            else:
                val, use_next = next_arg, True
            if is_negating: val = self._negate_arg(val)
        else:
            self._positional_args.append(arg)
            return use_next

        if internal_name is None:
            err_print("unknown option '{}'".format(arg))
            self.print_help() # <TODOXXX> Delay this towards the end, here and elsewhere.
            exit(1)
        elif internal_name in self._options \
           and self.option_info(internal_name, 'accumulate'):
            self._append_option(internal_name, val, 'args', accumulate_front) # <TODOXXX>
        else:
            self.set_option(internal_name, val, 'args')
        return use_next

    def parse_cmdline(self, args, required_args=[], optional_args=[],
                      is_sys_argv=True, allow_unknown=False):
        """Parse a set of command line arguments.

        Args:
            args (list of str): Command line arguments.
            required_args (list of str): Internal names of required arguments.
                These must be specified on the command line if they were not
                specified by another configuration source.
            optional_args (list of str): Internal names of arguments that
                can be specified as positional arguments
                (after the required arguments).
            is_sys_argv (bool, optional): Command line arguments are from sys.argv.
                The first argument (the name of the program) will be skipped.
                Defaults to True.
            allow_unknown (bool, optional): Avoid signaling an error on
                encountering unknown command line arguments.
                Defaults to False.

        Returns:
            self
        """
        self._cmdline_required_args = required_args
        self._cmdline_optional_args = optional_args

        # Handle sys.argv[0]:
        if len(args) > 0 and is_sys_argv:
            args = args[1:]

        # Handle keyword args and flags:
        i = 0
        accumulate_front = {} # XXX args shouldn't accumulate in reverse order
        while i < len(args):
            arg, next_arg = args[i], None
            if i + 1 < len(args):
                next_arg = args[i+1]
            use_next = self._proc_cmdline_arg(arg, next_arg, allow_unknown,
                                              accumulate_front)
            i += 2 if use_next else 1

        # Handle positional args:
        check_required = False # will check for required_args not in _positional_args
        j = 0 # index into self._positional_args
        for i in range(len(required_args)):
            if j >= len(self._positional_args):
                check_required = True
                break
            if required_args[i] in self.__dict__ \
               and self.sources[required_args[i]] == 'args':
                continue # argument is already specified
            self.set_option(required_args[i], self._positional_args[j], 'args')
            j += 1
        for i in range(len(optional_args)):
            if j >= len(self._positional_args):
                break
            if optional_args[i] in self.__dict__ \
               and self.sources[optional_args[i]] == 'args':
                continue # argument is already specified
            self.set_option(optional_args[i], self._positional_args[j], 'args')
        if j < len(self._positional_args):
            warn_print("unexpected extra positional argument '{}'" \
                       .format(self._positional_args[j]))
            self.print_help()
            exit(1)

        # Handle accumulating parameters:
        for key, val in accumulate_front.items():
            if isinstance(self.__dict__[key], list):
                self.__dict__[key] = [val] + self.__dict__[key]
            elif isinstance(self.__dict__[key], str):
                self.__dict__[key] = val + "," + self.__dict__[key]
            else:
                self.__dict__[key] = val + "," + str(self.__dict__[key])

        # Saved required args for later verification:
        if check_required:
            self._required_args = required_args

        return self

    def parse_cgi_query(self, form):
        """Parse a set of CGI options.

        Args:
            form (cgi.FieldStorage): CGI options.

        Returns:
            self
        """
        for external_name, internal_name in self.option_names('cgi'):
            if external_name in form:
                self.set_option(internal_name, form[external_name], 'cgi')
            if internal_name in form:
                self.set_option(internal_name, form[internal_name], 'cgi')
        return self

    def check_required(self):
        """Check if required arguments from a previous parse_cmdline() invocation
        were set from any configuration source. If not, output an error
        message and set self.should_print_help."""
        for internal_name in self._required_args:
            if internal_name not in self.__dict__ \
               or self.sources[internal_name] == 'default':
                # TODO: Should use cmdline name instead?
                err_print("missing required argument '{}'".format(internal_name))
                self.should_print_help = True

    # TODOXXX: Legacy method from Bunsen class, merge into print_help():
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
            # TODO: Later args could override earlier args with same name.
            name, default_val, cookie, description = t
            internal_name = self.option_name(name, 'cmdline')
            if internal_name is None: internal_name = name # XXX
            arginfo_map[internal_name] = t
            if internal_name not in required_args and internal_name not in optional_args:
                other_arginfo.append(t)
        for internal_name in required_args:
            required_arginfo.append(arginfo_map[internal_name])
        for internal_name in optional_args:
            optional_arginfo.append(arginfo_map[internal_name])
        all_args = required_arginfo + optional_arginfo + other_arginfo
        for t in all_args:
            name, default_val, cookie, description = t
            if cookie is None and name == 'pretty' \
                 and isinstance(default_val, bool):
                cookie = 'yes|no|html' if default_val else 'no|yes|html'
            elif cookie is None and isinstance(default_val, bool):
                cookie = 'yes|no' if default_val else 'no|yes'
            elif cookie is None and isinstance(default_val, int):
                # XXX: Need to sort *after* bool since a bool is also an int :/
                cookie = "<num>"

            basic_arg_desc = "{}={}".format(name, cookie)
            internal_name = self.option_name(name, 'cmdline')
            if internal_name is None: internal_name = name
            if internal_name in required_args:
                arg_desc = "[{}=]{}".format(name, cookie)
            elif internal_name in optional_args:
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
        if info is not None:
            usage += "\n\n"
            usage += info
        usage += "\n\nArguments:\n"
        usage += arg_info
        warn_print(usage, prefix="")

    # TODO Should sort command line args further by group.
    # TODOXXX Docstring. For now, imitate prior print_usage.
    def print_help(self):
        args = []
        # TODO: Need access to the script info message here. self._opts.info?
        # TODO: Sort arguments in the correct order.
        # TODO: For the baseline Bunsen command, print these groups:
        skipped_groups = {'bunsen', 'init', 'commit', 'run'}
        skipped_opts = set()
        for group in skipped_groups:
            for internal_name in self._option_groups[group]:
                skipped_opts.add(internal_name)
        cmdline_opts = set()
        for external_name, internal_name in self.option_names('cmdline'):
            if internal_name in skipped_opts:
                continue
            cmdline_opts.add(internal_name)
            default_val = self.option_info(internal_name, 'default_value')
            cookie = self.option_info(internal_name, 'help_cookie')
            description = self.option_info(internal_name, 'help_str')
            args.append((external_name, default_val, cookie, description))
        for internal_name in self._options:
            if internal_name in skipped_opts:
                continue
            if internal_name in cmdline_opts:
                continue
            default_val = self.option_info(internal_name, 'default_value')
            cookie = self.option_info(internal_name, 'help_cookie')
            description = self.option_info(internal_name, 'help_str')
            args.append((internal_name, default_val, cookie, description))
        self._print_usage(self.usage_str, args,
                          required_args=self._cmdline_required_args,
                          optional_args=self._cmdline_optional_args)

    # TODO fields and methods for a testruns/testlogs query
    # -- if the Git commands are any guide, queries can get very complex
    # but all the particular script might care about is 'list of testruns'

    # TODO fields and methods for proper output in different formats
    # -- can take a formatter object from format_output.py?
    # -- or should we import format_output.py directly?

    def _split_list(self, value):
        # TODO: Handle quoted/escaped commas.
        items = []
        for val in value.split(","):
            if val == "": continue
            items.append(val.strip())
        return items

    def get_list(self, key, default=None):
        """Parse an option that was specified as a comma-separated list."""
        if key not in self.__dict__ or self.__dict__[key] is None:
            return default
        if isinstance(self.__dict__[key], list): # XXX already parsed
            return self.__dict__[key]
        return self._split_list(self.__dict__[key])

    # TODO logic for checking cgi_safe functionality

    def _show_results(self):
        """For debugging: print the contents of this BunsenOptions object."""
        print("OBTAINED OPTIONS")
        print ("script_name = {}".format(self.script_name))
        print ("expected_groups = {}".format(self.expected_groups))
        # TODO: Filter by active options groups?
        for key, val in self.__dict__.items():
            if key in _options_base_fields:
                continue
            print ("{} = {} ({})".format(key, val, self.sources[key]))
        if len(self._unknown_args) > 0:
            print ("_unknown_args = {}".format(self._unknown_args))
        # TODOXXX Print delayed_config?
        print()

    def script_env(self):
        """Output environment variable options as a dict."""
        env_values = {}
        for key, internal_name in self.option_names('env'):
            env_values[key] = str(self.__dict__[internal_name])
        return env_values

    def script_args(self):
        """Output non-default options as a list of command line args.

        Omits any options that can be specified via script variables.
        """
        env_keys = set()
        arg_values = []
        for key, internal_name in self.options_names('env'):
            # Skip values that can be satisfied by script_env:
            env_keys.add(internal_name)
        for key, val in self.__dict__.items():
            if key in _options_base_fields:
                continue
            if self.source[key] == 'default':
                # XXX Assume Bunsen child process will have the same defaults.
                continue
            if key in env_keys:
                continue
            if isinstance(val, list):
                # TODO: Escape commas in val.
                val = ",".join(val)
            # TODO: Handle other arg types as necessary.
            arg_values.append("{}={}".format(key, str(val)))
        arg_values += self.unknown_args
        return arg_values

    # TODO script_env, script_args for script invocation

# Options for bunsen:
BunsenOptions.add_option('bunsen_dir', group='bunsen',
    cmdline='repo', env='BUNSEN_DIR', nonconfig=True, default=None,
    help_str="Path to Bunsen repo.")
BunsenOptions.add_option('bunsen_git_repo', group='bunsen',
    cmdline='git-repo', env='BUNSEN_GIT_REPO', default=None,
    help_str="Path to Bunsen repo; default '$(bunsen_dir)/bunsen.git'.")
BunsenOptions.add_option('bunsen_dir_name', group='bunsen',
    default=None,
    help_str="Name of Bunsen repo to search for; default '.bunsen'.")
BunsenOptions.add_option('bunsen_default_repo', group='bunsen',
    default=None, # XXX default=$HOME/.bunsen
    help_str="Path to the default Bunsen repo.")
BunsenOptions.add_option('use_bunsen_default_repo', group='bunsen',
    cmdline='default-repo', boolean=True,
    help_str="Use the default Bunsen repo if a Bunsen repo is not found.")
  # TODO: Fallback if config, required if command line arg?
BunsenOptions.add_option('config_path', group='bunsen',
    cmdline='config', nonconfig=True, default=None,
    help_str="Path to config file; default '$(bunsen_dir)/config'.")
# TODO: Add a config option to use a different repo than bunsen.git?

# Options for {init,commit}:
BunsenOptions.add_option('git_user_name', group={'init','commit'},
    cmdline='git-user-name', cgi='git_user_name', default='bunsen',
    help_str="Username to use for Git commits to the Bunsen repo.")
BunsenOptions.add_option('git_user_email', group={'init', 'commit'},
    cmdline='git-user-email', cgi='git_user_email', default='unknown@email',
    help_str="Email to use for Git commits to the Bunsen repo.")
# XXX: These git options are set for working directory clones, not for
# the main Bunsen git repo. Changing them does not affect an already cloned
# working directory.

# TODOXXX extract script_name whenever group 'run' is configured?
# TODO add these options to bunsen add, bunsen run?
# Options for {run}:
BunsenOptions.add_option('scripts_search_path', group={'run'},
    cmdline='scripts-search-path', cmdline_short='I', default=None,
    accumulate=True,
    help_str="Additional directories to search for analysis scripts.")
# TODOXXX add --script-name command line option as alternative to +script?

# Options for output:
# TODOXXX output_format=json,html,console,log
# TODOXXX -o output_dest.html, hint message "Output written to <path>"

# Options for ???:
# TODOXXX name of bunsen branch to check out?

# TODOXXX add --help command line option
BunsenOptions.add_option('should_print_help', group=None,
    cmdline='help', nonconfig=True, boolean=True, default=False,
    help_str="Show this help message")
