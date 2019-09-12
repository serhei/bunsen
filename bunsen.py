#! /usr/bin/env python3

# TODO Redirect certain prints to stderr, show git command output / progress.

import os
import sys
import shutil
import time
import subprocess
import argparse

import json
import re
#import string
#import random

from configparser import ConfigParser
from git import Repo
from tqdm import tqdm

# Requires Python 3.
assert sys.version_info[0] >= 3

# TODO Replace DEBUG prints with dbug_print():
def dbug_print(*args, **kwargs):
    if False:
        print('DEBUG: ', file=sys.stderr, *args, **kwargs)

# XXX For now, hardcode Bunsen data to live in the git checkout directory:
bunsen_repo_dir = os.path.dirname(os.path.realpath(__file__))
bunsen_default_dir = os.path.join(bunsen_repo_dir, ".bunsen")
# OR bunsen_default_dir = os.path.join(bunsen_default_dir, "bunsen-data")

# XXX Format for testrun and testlog branch names and commit msgs:
branch_regex = re.compile(r"(?P<tag>.*)/test(?P<kind>runs|logs)-(?P<year_month>\d{4}-\d{2})(?:-(?P<extra>.*))?")
commitmsg_regex = re.compile(r"(?P<tag>.*)/test(?P<kind>runs|logs)-(?P<year_month>\d{4}-\d{2})(?:-(?P<extra>[^:]*))?:(?P<note>.*)")

# XXX Format for indexfile (in 'index' branch):
indexfile_regex = re.compile(r"(?P<tag>.*)-(?P<year_month>\d{4}-\d{2}).json")
INDEX_SEPARATOR = '\n---\n' # XXX YAML separator between JSON objects in index

# XXX Format for cursor:
cursor_regex = re.compile(r"(?:(?P<commit_id>[0-9A-Fa-f]+):)?(?P<path>.*):(?P<start>\d+)(?:-(?P<end>\d+))?")

# One level up from os.path.basename:
def basedirname(path):
    dir = os.path.dirname(path)
    return os.path.basename(dir)

class Index:
    def __init__(self, bunsen, tag):
        self._bunsen = bunsen
        self.tag = tag

    def _indexfiles(self):
        commit = self._bunsen.git_repo.commit('index')
        for blob in commit.tree:
            m = indexfile_regex.fullmatch(blob.path)
            if m is not None and m.group('tag') == self.tag:
                yield (blob.path, commit.tree[blob.path])

    def __iter__(self):
        for path, blob in self._indexfiles():
            data = blob.data_stream.read().decode('utf-8')
            for json_str in data.split(INDEX_SEPARATOR):
                json_str = json_str.strip()
                if json_str == '': continue
                yield Testrun(self._bunsen, from_json=json_str, summary=True)

class Testlog:
    def __init__(self, bunsen, path=None, commit_id=None, blob=None, input_file=None):
        self._bunsen = bunsen
        self.path = path
        self.commit_id = commit_id
        self.blob = blob
        self._input_file = input_file

        # XXX Populate on demand:
        self._tag = None
        self._year_month = None
        self._lines = None

    @property
    def tag(self):
        if self._tag is None and self.commit_id is not None:
            self._tag, self._year_month = self._bunsen.commit_tag(self.commit_id)
        return self._tag

    @property
    def year_month(self):
        if self._year_month is None and self.commit_id is not None:
            self._tag, self._year_month = self._bunsen.commit_tag(self.commit_id)
        return self._year_month

    @property
    def data_stream(self):
        if self._input_file is not None:
            self._input_file.seek(0)
            return self._input_file
        if self._bunsen is None:
            # TODO: What about closing the data stream?
            return open(self.path, 'r')
        return self.blob.data_stream

    def _data_stream_readlines(self):
        try:
            return self.data_stream.readlines()
        except UnicodeDecodeError: # yes, it happens
            print("WARNING: UnicodeDecodeError in TestLog, path={}".format(self.path), file=sys.stderr)
            return [""]

    def line(self, line_no):
        '''Return text at specified line number (1-indexed!).'''
        if self._lines is None:
            self._lines = self._data_stream_readlines()
            # XXX: Could clear _lines if too many TestLog objects are
            # in memory, but did not observe any problem in practice.
        return self._lines[line_no-1]

    def __len__(self):
        if self._lines is None:
            self._lines = self._data_stream_readlines()
            # XXX: Could clear _lines if too many TestLog objects are
            # in memory, but did not observe any problem in practice.
        return len(self._lines)

class Cursor:
    '''
    Identifies a line or range of lines within a Testlog.
    '''

    def __init__(self, source=None, from_str=None,
                 commit_id=None, start=1, end=None,
                 name=None, input_file=None, fast_hack=False):
        self.name = name

        # XXX fast_hack has __iter__ yield the same Cursor object repeatedly.
        # Makes the yielded cursor unsafe to store in a testrun since it will change.
        self._fast_hack = True

        # XXX Support the following __init__ calls:
        # - Cursor (source=path, optional start=int, optional end=int) -> testlog=None
        if isinstance(source, str):
            assert from_str is None and commit_id is None
            testlog = Testlog(None, source, input_file=input_file)
        # - Cursor (source=Testlog, optional start=int, optional end=int)
        elif isinstance(source, Testlog):
            assert from_str is None and commit_id is None
            testlog = source
            if input_file is not None:
                testlog._input_file = input_file
        # - Cursor (source=Bunsen, from_str=str, optional commit_id=hexsha)
        #   with from_str of the form '[<commit_id>:]<testlog_path>:<start>[-<end>]'
        elif isinstance(source, Bunsen):
            assert start is 1 and end is None

            m = cursor_regex.fullmatch(from_str)
            assert m is not None
            if m.group('commit_id') is not None:
                #assert commit_id is None # XXX may not be true during parsing
                commit_id = m.group('commit_id')
            assert commit_id is not None
            path = m.group('path')
            assert path is not None
            testlog = source.testlog(path, commit_id, parse_commit_id=False)
            if input_file is not None:
                testlog._input_file = input_file
            start = int(m.group('start'))
            end = int(m.group('end')) if m.group('end') is not None else start
        # - Cursor (start=Cursor, optional end=Cursor)
        else:
            if end is None: end = start
            assert isinstance(start, Cursor) and isinstance(end, Cursor)
            assert source is None and from_str is None and commit_id is None

            # TODO: Validate that start and end refer to the same Testlog.
            if not self.name:
                self.name = start.name

            testlog = start.testlog
            if input_file is not None:
                testlog._input_file = input_file
            start = start.line_start
            end = end.line_end

        self.testlog = testlog
        self.line_start = start
        self.line_end = end
        if self.line_end is None:
            self.line_end = len(self.testlog)

    def __iter__(self):
        '''
        Yields single-line Cursors for all lines within this Cursor's range.
        '''
        if self._fast_hack:
            cur = Cursor(source=self.testlog, start=-1, end=-1, name=self.name)
            for i in range(self.line_start, self.line_end+1):
                cur.line_start = i; cur.line_end = i
                yield cur
        else:
            for i in range(self.line_start, self.line_end+1):
                yield Cursor(source=self.testlog, start=i, end=i, name=self.name)

    @property
    def line(self):
        assert self.line_start == self.line_end
        return self.testlog.line(self.line_start)

    def to_str(self):
        repr = ''
        if self.testlog.commit_id is not None:
            repr += self.testlog.commit_id + ':'
        repr += self.name if self.name else \
                self.testlog.path if self.testlog.path else '<unknown>' # TODOXXX avoid <unknown> -- should signal a warning
        repr += ':' + str(self.line_start)
        if self.line_end is not None and self.line_end != self.line_start:
            repr += '-' + str(self.line_end)
        return repr

# Testrun fields that should not be added to JSON:
_testrun_base_fields = {'_bunsen',
                        '_field_types',
                        '_testcase_field_types',
                        'summary'}

# Testrun fields that require special serialization/deserialization logic:
_testrun_field_types = {'testcases':'testcases'}

# Testrun fields that require special serialization/deserialization logic:
_testcase_field_types = {'origin_log':'cursor',
                         'origin_sum':'cursor',
                         'baseline_log':'cursor',
                         'baseline_sum':'cursor'}
# TODOXXX Fields to handle 2nd-order diffs -- origins, baseline_origins. Can be handled by adding a 'metadata' type that applies the top-level rules recursively.

# Toplevel fields that identify the commit_id of a Cursor:
_cursor_commits = {'origin_log':'bunsen_commit_id',
                   'origin_sum':'bunsen_commit_id',
                   'baseline_log':'baseline_bunsen_commit_id', # TODOXXX populate in diff_runs.py
                   'baseline_sum':'baseline_bunsen_commit_id'} # TODOXXX populate in diff_runs.py
# TODOXXX Fields to handle 2nd-order diffs -- commit_ids, baseline_commit_ids

# XXX Valid _testrun_field_types, _testcase_field_types:
_valid_field_types = {
    'testcases', # list of dict {name, outcome, ?subtest, ?origin_log, ?origin_sum, ...}
    'cursor',    # Cursor object
    'hexsha',    # string -- git commit id
    'str',       # string
}

# TODOXXX Validate required metadata e.g. bunsen_branch, bunsen_commit_id, year_month? before committing a testcase.

class Testrun(dict):
    def __init__(self, bunsen=None, from_json=None,
                 fields={}, testcase_fields={},
                 summary=False):
        '''
        Create empty Testrun or parse Testrun data from a JSON string.
        '''

        # XXX Populate fields so __setattr__ doesn't add them to JSON dict.
        for field in _testrun_base_fields:
            if field not in self.__dict__:
                self.__dict__[field] = None

        self._bunsen = bunsen

        # Populate self._field_types from fields, _testrun_field_types:
        self._field_types = dict(_testrun_field_types)
        for field, field_type in fields.items():
            assert field_type in _valid_field_types
            self._field_types[field] = field_type

        # Likewise self._testcase_field_types:
        self._testcase_field_types = dict(_testcase_field_types)
        for field, field_type in testcase_fields.items():
            assert field_type in _valid_field_types
            self._testcase_field_types[field] = field_type

        self.summary = summary
        if not self.summary:
            self.testcases = []

        if from_json is not None:
            json_data = json.loads(from_json)
            assert isinstance(json_data, dict)
            defer_fields = [] # XXX Defer parsing until cursor_commit_ids are known.
            cursor_commit_ids = {} # XXX bunsen_commit_id -> {value}
            for cursor_field, commit_field in _cursor_commits.items():
                cursor_commit_ids[commit_field] = None
            for field, value in json_data.items():
                if field not in self._field_types:
                    pass
                elif self._field_types[field] == 'testcases' \
                     and summary:
                    continue # read only summary from JSON
                elif self._field_types[field] == 'testcases':
                    defer_fields.append(field)
                elif self._field_types[field] == 'cursor':
                    defer_fields.append(field)
                else:
                    assert self._testcase_field_types[field] == 'str' \
                        or self._testcase_field_types[field] == 'hexsha'
                if summary and field == 'testcases':
                    continue # load only summary from JSON
                if field in cursor_commit_ids:
                    cursor_commit_ids[field] = value
                self[field] = value
            for field in defer_fields:
                if self._field_types[field] == 'testcases':
                    self[field] = self._deserialize_testcases(self[field],
                                                              cursor_commit_ids)
                elif self._field_types[field] == 'cursor':
                    commit_id = None
                    if field in _cursor_commits:
                        commit_id_field = _cursor_commits[field]
                        commit_id = cursor_commit_ids[commit_id_field]
                    self[field] = Cursor(source=self._bunsen,
                                         commit_id=commit_id,
                                         from_str=self[field])

            # XXX Set summary=False if JSON was missing testcases.
            self.summary = self.summary and 'testcases' in json_data

    def add_testcase(self, name, outcome, **kwargs):
        '''
        Append a testcase result to the Testrun data.
        '''
        testcase = {'name':name, 'outcome':outcome}
        for field, value in kwargs.items():
            testcase[field] = value
        if 'testcases' not in self:
            self.summary = False
            self.testcases = []
        self.testcases.append(testcase)
        return testcase

    def _deserialize_testcases(self, testcases, cursor_commit_ids):
        deserialized_testcases = []
        for testcase in testcases:
            deserialized_testcase = {}
            for field, value in testcase.items():
                if field not in self._testcase_field_types:
                    pass
                elif self._testcase_field_types[field] == 'testcases':
                    value = self._deserialize_testcases(value, cursor_commit_ids)
                elif self._testcase_field_types[field] == 'cursor':
                    value = Cursor(source=self._bunsen,
                                   commit_id=self.bunsen_commit_id,
                                   from_str=value)
                else:
                    assert self._testcase_field_types[field] == 'str' \
                        or self._testcase_field_types[field] == 'hexsha'
                deserialized_testcase[field] = value
            deserialized_testcases.append(deserialized_testcase)
        return deserialized_testcases

    def _serialize_testcases(self, testcases):
        serialized_testcases = []
        for testcase in testcases:
            serialized_testcases.append(self._serialize_testcase(testcase))
        return serialized_testcases

    def testcase_to_json(self, testcase):
        '''
        Serialize a single Testcase to a JSON string.
        '''
        return json.dumps(self._serialize_testcase(testcase))

    def _serialize_testcase(self, testcase):
        serialized_testcase = {}
        for field, value in testcase.items():
            if isinstance(value, Cursor):
                # XXX serialize regardless of self._testcase_field_types
                value = value.to_str()
            elif field not in self._testcase_field_types:
                pass
            elif self._testcase_field_types[field] == 'testcases':
                value = self._serialize_testcases(value)
            else:
                assert self._testcase_field_types[field] == 'str' \
                    or self._testcase_field_types[field] == 'hexsha' \
                    or self._testcase_field_types[field] == 'cursor' # XXX can be given as str
            serialized_testcase[field] = value
        return serialized_testcase

    def to_json(self, summary=False, pretty=False):
        '''
        Serialize Testrun data to a JSON string.
        '''
        serialized = {}
        for field, value in self.items():
            if isinstance(value, Cursor):
                # XXX serialize regardless of self._field_types
                value = value.to_str()
            elif field not in self._field_types:
                pass
            elif self._field_types[field] == 'testcases' \
                 and summary:
                continue # write only summary to JSON
            elif self._field_types[field] == 'testcases' \
                 and isinstance(value, list):
                value = self._serialize_testcases(value)
            else:
                assert self._field_types[field] == 'str' \
                    or self._field_types[field] == 'hexsha' \
                    or self._field_types[field] == 'cursor' # XXX can be given as str
            serialized[field] = value
        # XXX: Could use json.dump instead of json.dumps?
        if pretty:
            return json.dumps(serialized, indent=4)
        else:
            return json.dumps(serialized)

    # XXX: Hackish protocol to support reading/writing JSON fields as attrs:

    def __getattr__(self, field):
        # XXX Called if attribute is not found -- look in JSON dict.
        return self[field]

    def __setattr__(self, field, value):
        if field not in self.__dict__:
            # Add the attribute to JSON dict.
            self[field] = value
        else:
            self.__dict__[field] = value

    def __delattr__(self, field, value):
        if field not in self.__dict__:
            # Remove the attribute from JSON dict.
            del self[field]
        else:
            # XXX Assign None so that later __setattr__ calls won't add field to JSON dict!
            self.__dict__[field] = None
            # TODO: Perhaps throw AttributeError instead?

# TODO: Ideally all nasty Git trickery should be confined to this class.
class Workdir(Repo):
    '''
    A temporary clone of a Bunsen git repo. Includes some higher-level
    functionality for safely working with Bunsen data.
    '''

    def __init__(self, bunsen, path_or_repo):
        self._bunsen = bunsen
        if isinstance(path_or_repo, Repo):
            path_or_repo = path_or_repo.working_tree_dir
        super().__init__(path_or_repo)

    def push_all(self, branch_names=None):
        '''
        Push all (or specified) branches in the working directory.
        '''
        if branch_names is None:
            # XXX Could also use self.branches
            branch_names = [b.name for b in self._bunsen.git_repo.branches]
        # TODOXXX: Doing separate push operations is risky as it may
        # result in incomplete data. Figure out something better.
        # Is a git push --all with multiple branches even atomic?
        # Perhaps index branches should be updated last of all.
        for candidate_branch in branch_names:
            try:
                self.push_branch(candidate_branch)
            except Exception: # XXX except git.exc.GitCommandError ??
                # XXX This is most typically the result of a partial commit.
                # May want to assemble branch names from self.branches.
                print("WARNING: could not push branch {}".format(candidate_branch), file=sys.stderr)

    def push_branch(self, branch_name=None):
        '''
        Push current (or specified) branch in the working directory.
        '''
        if branch_name is None:
            branch_name = self.head.reference.name
        # TODO: Need to show progress for large updates.
        # TODO: Need to find the 'proper' GitPython equivalent for this:
        print("Pushing branch {}...".format(branch_name))
        self.git.push('origin', branch_name)

    def checkout_branch(self, branch_name, skip_redundant_checkout=False):
        '''
        Check out specified branch in the working directory.
        '''
        branch = None
        # TODO: This ugly linear search is the best API I could find so far:
        for candidate_ref in self._bunsen.git_repo.branches: # XXX self.branches also works
            if candidate_ref.name == branch_name:
                branch = candidate_ref
                break

        # if necessary, create appropriate branch based on master
        if branch is None:
            print("Created new branch", branch_name, file=sys.stderr)
            branch = self.create_head(branch_name)
            branch.commit = 'master'
            #branch = self.create_head(branch_name, self.git_repo.refs.master)
            # TODO: Need to find the 'proper' GitPython equivalent for this:
            self.git.push('--set-upstream', 'origin', branch_name)
            # TODO: ??? wd.remotes.origin.push() ???

        # TODO: For an existing wd already on the correct branch, may
        # not want to check out at this point. It might be useful to
        # chain the outputs of one script into inputs for another one,
        # without committing any files.

        # checkout appropriate branch
        if skip_redundant_checkout and self.head.reference == branch:
            return
        # XXX print("Checked out existing branch", branch_name)

        self.head.reference = branch
        self.head.reset(index=True, working_tree=True) # XXX destroys changes
        #branch.checkout() # XXX alternative, preserves uncommitted changes
        self.git.pull('origin', branch_name) # TODO: Prevent non-fast-forward refs? There were some issues with this in early testing.

    def clear_files(self):
        '''
        Remove (almost) all files in the working directory
        (used to commit logically distinct testlogs to the same branch).
        '''
        keep_files = ['.git', '.gitignore', '.bunsen_workdir']
        if len(self.index.entries) > 0:
            remove_files = [k for k, v in self.index.entries if k not in keep_files]
            #print("DEBUG removing", remove_files, file=sys.stderr)
            self.index.remove(remove_files)
        # Be sure to remove any non-index files:
        for path in os.listdir(self.working_tree_dir):
            if path not in keep_files:
                os.remove(os.path.join(self.working_tree_dir,path)) # TODO: Does not handle subdirectories.
                #shutil.rmtree(os.path.join(self.working_tree_dir,path)) # A bit extreme, but will be needed for more complex testsuites such as DynInst.
        for path in os.listdir(self.working_tree_dir):
            assert path in keep_files

    def commit_all(self, commit_msg, allow_duplicates=True):
        '''
        Commit all files in the working directory. Returns hexsha of the new commit.

        If allow_duplicates=False, will find a previous commit in the
        same branch with the same files and returns its commit hexsha,
        rather than committing again.
        '''
        for path in os.listdir(self.working_tree_dir):
            if path == '.bunsen_initial':
                # Delete dummy placeholder file from master branch.
                self.index.remove(['.bunsen_initial'])
                os.remove(os.path.join(self.working_tree_dir,path))
            elif path != '.git' and path != '.bunsen_workdir':
                self.index.add([path])
        if not allow_duplicates:
            index_tree = self.index.write_tree() # computes hexsha
            # Memoize known hexshas to avoid quadratic re-scanning of branch:
            #if index_tree.hexsha in _bunsen._known_hexshas:
            #    # XXX If this takes too much memory, store just the hexsha
            #    commit = _bunsen._known_hexshas[index_tree.hexsha]
            #    print("WARNING: proposed commit {}\n.. is a duplicate of already existing commit {} ({})".format(commit_msg, commit.summary, commit.hexsha), file=sys.stderr)
            #    return commit.hexsha
            for commit in self.iter_commits(): # XXX iters tiny branch (~1month)
                #print("DEBUG should be active branch (not many) --", commit.hexsha, file=sys.stderr)
                #_bunsen._known_hexshas[commit.tree.hexsha] = commit
                if commit.tree.hexsha == index_tree.hexsha:
                    print("WARNING: proposed commit {}\n.. is a duplicate of already existing commit {} ({})".format(commit_msg, commit.summary, commit.hexsha))
                    return commit.hexsha
        commit = self.index.commit(commit_msg)
        return commit.hexsha

    # TODO: Add a --keep option to suppress workdir destruction.
    def destroy(self):
        '''
        Delete the working directory.
        '''
        # Additional safety check (don't destroy a non-Bunsen git checkout):
        files = os.listdir(self.working_tree_dir)
        if '.git' in files and '.bunsen_workdir' in files:
            shutil.rmtree(self.working_tree_dir)
            return
        print(("WARNING: {} doesn't look like a Bunsen workdir, "
               "skip destroying it").format(self.working_tree_dir), file=sys.stderr)

class Bunsen:
    def __init__(self, bunsen_dir=None, alternate_cookie=None):
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
        - cache/ -- TODO will contain scratch data for Bunsen scripts
        - config -- TODO will be a git style INI file
        - scripts/ -- a folder for user-contributed scripts
        '''
        self.base_dir = bunsen_dir
        if self.base_dir is None:
            if 'BUNSEN_DIR' in os.environ:
                self.base_dir = os.environ['BUNSEN_DIR']
            else:
                self.base_dir = bunsen_default_dir

        self.git_repo_path = os.path.join(self.base_dir, "bunsen.git")
        if os.path.isdir(self.git_repo_path):
            self.git_repo = Repo(self.git_repo_path)

        self.cache_dir = os.path.join(self.base_dir, "cache")

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

        # XXX Search scripts/, scripts-*/ in these directories:
        self.scripts_search_path = [self.base_dir, bunsen_repo_dir]

        # XXX Include Python modules from these directories:
        self.default_pythonpath = [bunsen_repo_dir]
        for parent_dir in self.scripts_search_path:
            if not os.path.isdir(parent_dir):
                continue
            for candidate_dir in os.listdir(parent_dir):
                candidate_path = os.path.join(parent_dir, candidate_dir)
                # TODO: Also consider 'modules', 'modules-'?
                if candidate_dir == 'scripts' \
                   or candidate_dir.startswith('scripts-'):
                    if not os.path.isdir(candidate_path):
                        continue
                    self.default_pythonpath.append(candidate_path)
        # TODO: Also allow invoking Python scripts from shell scripts via $PATH.

        # XXX Add the following environment variables to a running script:
        self.default_script_env = {'BUNSEN_DIR': self.base_dir,
                                   'BUNSEN_CACHE': self.cache_dir}
        # XXX BUNSEN_WORK_DIR, etc. set for each individual run.

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
                tag = m.group('tag')
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
                #print("DEBUG found index commit", commit.hexsha, commit.summary, file=sys.stderr) # check for HEAD in index
                found_index = False
                for blob in commit.tree:
                    m = indexfile_regex.fullmatch(blob.path)
                    if m is not None and m.group('tag') == tag:
                        #print("DEBUG found indexfile", blob.path, file=sys.stderr)
                        found_index = True
                if found_index:
                    found_tags.append(tag)
                elif not warned_indexfiles:
                    print(("WARNING: found tag {} but no indexfiles "
                           "in branch index").format(tag), file=sys.stderr)
                    warned_indexfiles = True

        return found_tags

    def commit_tag(self, commit_id=None, commit=None):
        '''
        Find the (tag, year_month) pair for a commit in the repo.
        '''
        if commit is None:
            assert commit_id is not None
            commit = self.git_repo.commit(commit_id)
            #print("DEBUG found commit_tag commit", commit.hexsha, commit.summary, file=sys.stderr)
        m = commitmsg_regex.fullmatch(commit.summary)
        tag = m.group('tag')
        year_month = m.group('year_month')
        return tag, year_month

    def testruns(self, tag):
        '''
        Create an Index object for a log category in the repo.
        '''
        return Index(self, tag)

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
        #print("DEBUG found testlog commit", testlog_hexsha, commit.summary, file=sys.stderr)
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
            #print("DEBUG found testrun commit", commit.hexsha, commit.summary, file=sys.stderr) # check for HEAD in branch_name
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
        #print("DEBUG found testlog commit", commit.hexsha, commit.summary, file=sys.stderr)
        blob = commit.tree[testlog_path]
        return Testlog(self, path=testlog_path, commit_id=commit_id, blob=blob)

    # Methods for adding testlogs and testruns:

    @property
    def staging(self):
        '''
        List of Testlog and Testrun objects to commit to the Bunsen repo.
        '''
        return (self._staging_testlogs, self._staging_testruns)

    def add_testlog(self, testlog_or_path):
        '''
        Stage a Testlog to commit to the repo.
        '''
        if isinstance(testlog_or_path, Testlog):
            testlog = testlog_or_path
        else:
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
            elif testrun.year_month is not None:
                assert year_month == testrun.year_month # TODO Signal error.
        assert year_month is not None # TODO Signal error.

        temporary_wd = wd is None
        if temporary_wd:
            wd = self.checkout_wd()
        refspec = [] # -- list of modified branches.

        testruns_branch_name = tag + '/testruns-' + year_month
        # XXX For large repos, may need to split into a larger number of branches:
        if branch_extra is not None:
            assert ':' not in branch_extra
            testruns_branch_name += '-' + branch_extra

        testlogs_branch_name = tag + '/testlogs-' + year_month

        for testrun in self._staging_testruns:
            # XXX: Some earlier repos named bunsen_testlogs_branch as
            # bunsen_branch_name -- may need to temporarily check for
            # both field names in analysis scripts until those repos
            # are rebuilt.
            testrun.bunsen_testlogs_branch = testlogs_branch_name

            # XXX: Record the branches where we stored this testrun.
            # This info will also be added to the index allowing
            # fast lookup of a full Testrun object and its logs.
            testrun.bunsen_testruns_branch = testruns_branch_name

        if True:
            branch_name = testlogs_branch_name
            wd.checkout_branch(branch_name, skip_redundant_checkout=True)
            wd.clear_files()
            for testlog in self._staging_testlogs:
                shutil.copy(testlog.path, wd.working_tree_dir)
            commit_msg = branch_name # XXX Ensures commit msg contains year_month.
            commit_msg += ": testrun with {} testlogs".format(len(self._staging_testlogs))
            # XXX append testcase summary json to commit msg for testruns_branch lookup
            commit_msg += INDEX_SEPARATOR
            commit_msg += testrun.to_json(summary=True)
            commit_id = wd.commit_all(commit_msg, allow_duplicates=allow_duplicates)
            refspec.append(branch_name)

            # Metadata that is only known after commit is made:
            for testrun in self._staging_testruns:
                testrun.bunsen_commit_id = commit_id

        testrun_tag = None
        if True:
            # XXX: Duplicate testruns will overwrite previous json with a
            # freshly parsed one. This behaviour is probably desirable.
            if wd_testruns is None: wd_testruns = wd
            branch_name = testruns_branch_name
            wd_testruns.checkout_branch(branch_name, skip_redundant_checkout=True)
            have_primary = False
            for testrun in self._staging_testruns:
                testrun_tag = testrun.tag if 'tag' in testrun else tag
                if tag is None or testrun_tag == tag:
                    assert not have_primary # TODO Signal error.
                    have_primary = True
                json_name = testrun_tag + "-" + commit_id + ".json"
                json_path = os.path.join(wd_testruns.working_tree_dir, json_name)
                with open(json_path, 'w') as out:
                    out.write(testrun.to_json())
            assert have_primary # TODO Signal error.
            commit_msg = branch_name
            commit_msg += ": {} index files for commit {}" \
                .format(len(self._staging_testruns), commit_id)
            wd_testruns.commit_all(commit_msg)
            refspec.append(branch_name)

        if True:
            # TODOXXX: Duplicate testrun data can still be added here,
            # although they will refer to the same testlogs commits.
            # This behaviour is probably undesireable -- should skip.
            if wd_index is None: wd_index = wd
            wd_index.checkout_branch('index', skip_redundant_checkout=True)
            for testrun in self._staging_testruns: # XXX reuse modified testrun
                if testrun_tag is None:
                    testrun_tag = testrun.tag if 'tag' in testrun else tag
                if 'tag' in testrun and testrun.tag != tag:
                    continue # XXX Do not put secondary json files in the index.
                json_name = testrun_tag + "-" + year_month + ".json"
                json_path = os.path.join(wd_index.working_tree_dir, json_name)
                with open(json_path, 'a') as out:
                    out.write(testrun.to_json(summary=True))
                    out.write(INDEX_SEPARATOR)
            commit_msg = branch_name # XXX reuse testruns branch name
            commit_msg += ": summary index for commit {}".format(commit_id)
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
        self.git_repo = Repo.init(self.git_repo_path, bare=True)

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
            # TODO Write some default config values -- _init_config() method?
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
        else:
            # TODO Signal error properly:
            assert branch_name is not None

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
        for parent_dir in self.scripts_search_path:
            for candidate_dir in os.listdir(parent_dir):
                candidate_path = os.path.join(parent_dir, candidate_dir)
                # TODO Prefer 'scripts-master' over others.
                if candidate_dir == 'scripts' \
                   or candidate_dir.startswith('scripts-'):
                    # XXX Allow script_name to be a relative path
                    # e.g. scripts-host/examples/hello-shell.sh
                    # invoked as +examples/hello-shell.
                    candidate_path = os.path.join(candidate_path, script_name)
                    candidate_paths = [candidate_path,
                                       candidate_path + '.sh',
                                       candidate_path + '.py']
                    for candidate_path in candidate_paths:
                        if os.path.isfile(candidate_path):
                            scripts_found.append(candidate_path)
        assert len(scripts_found) > 0 # TODO Signal error properly.

        # Prioritize among scripts_found:
        fallback_script_path = scripts_found[0]
        preferred_script_path = None
        for script_path in scripts_found:
            script_dir = basedirname(script_path)

            # These preferences activate when preferred_host is not None:
            if script_dir == 'scripts-master' \
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
            # and TODO (2) scripts-master/ overrides scripts-host/,scripts-guest/
            #
            # Preference (1) lets the user cleanly customize a script
            # by copying files from self.base-dir/scripts-whatever to
            # .bunsen/scripts-whatever and editing them.
            #
            # Preference (2) lets a guest script
            # e.g. scripts-guest/my-testsuite.sh be 'wrapped' by a
            # host script which does additional prep on the master
            # e.g. scripts-host/my-testsuite.py --with-patch=local-changes.patch
            preferred_script_path = script_path
            break

        return preferred_script_path if preferred_script_path \
            else fallback_script_path

    def run_script(self, hostname, script_path, script_args,
                   wd_path=None, wd_branch_name=None, wd_cookie=None):
        script_env = self.default_script_env

        if wd_path:
            script_env['BUNSEN_WORK_DIR'] = wd_path
        if wd_branch_name:
            script_env['BUNSEN_BRANCH'] = wd_branch_name
        if wd_cookie is not None:
            script_env['BUNSEN_COOKIE'] = wd_cookie

        # Add the ability to invoke bunsen commands:
        # TODO: Configure only when script is running on the Bunsen master.
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
            print("TODO: Need to support remote script execution.")
            assert False

    # TODO: Add required_args option for optional later args.
    # TODOXXX Add a more flexible var=value arguments scheme.
    def cmdline_args(self, argv, nargs=None, usage=None, defaults=None):
        '''Verify number of command line arguments and return them as a list
           (if nargs=None) or a tuple (otherwise). Exit if the number of
           arguments fails to match nargs, unless no arguments are provided
           and defaults is provided.'''
        argv = argv[1:] # XXX Removes sys.argv[0].
        if not nargs:
            return argv
        assert defaults is None or len(defaults) <= nargs
        if defaults is not None and len(argv) == 0 and len(defaults) == nargs:
            if len(defaults) == 1: return defaults[0]
            return tuple(defaults)
        minargs = nargs - (0 if defaults is None else len(defaults))
        if defaults is not None and len(argv) < nargs and len(argv) >= minargs:
            delta = len(argv) - minargs + 1
            for i in range(len(argv),nargs):
                argv.append(defaults[i-delta])
        if len(argv) != nargs:
            print("USAGE:", usage)
            exit(1)
        if len(argv) == 1: return argv[0]
        return tuple(argv)

# Subcommand 'init'

def bunsen_init(b):
    found_existing = b.init_repo()
    if found_existing:
        print("Reinitialized existing Bunsen repository in", b.base_dir)
    else:
        print("Initialized empty Bunsen repository in", b.base_dir)

# Subcommand 'checkout-wd'

def bunsen_checkout_wd(b, branch_name=None, checkout_path=None):
    if branch_name is None:
        # XXX Branch (should have been) specified from environment.
        assert b.default_branch_name is not None # TODO Signal error properly.
        branch_name = b.default_branch_name
    if checkout_path is None and b.default_work_dir is not None:
        # XXX Checkout path was specified from environment.
        checkout_path = b.default_work_dir

    if checkout_path is None:
        # Checkout in current directory:
        checkout_name = None
        checkout_dir = os.getcwd()
    elif os.path.isdir(checkout_path):
        # Checkout within checkout_path:
        checkout_name = None
        checkout_dir = checkout_path
        # TODO Handle the case where checkout_path is already a Bunsen checkout.
        # Requires checkout-wd to mark .git to distinguish from other Git repos.
    else:
        # Checkout at checkout_path:
        checkout_name = os.path.basename(checkout_path)
        checkout_dir = os.path.dirname(checkout_path)
    wd = b.checkout_wd(branch_name, \
                       checkout_name=checkout_name, checkout_dir=checkout_dir)
    # TODO Print one message if updating, another message if meant for human output (rather than a checkout-wd call from a bash script).
    print(wd.working_tree_dir)

# TODO Subcommand 'destroy-wd'

# Subcommand 'run'

def bunsen_run(b, hostname, scriptname, invocation_args):
    script_path = b.find_script(scriptname, preferred_host=hostname)
    script_dirname = basedirname(script_path)
    if hostname is None:
        if script_dirname == "scripts-master":
            hostname = 'localhost'
        elif script_dirname == "scripts-host":
            # XXX For now the VM host is always the Bunsen master:
            #hostname = b.default_vm_host
            hostname = 'localhost'
        elif script_dirname == "scripts-guest":
            # TODO Signal error properly;
            printf("Hostname not specified for guest script", script_path)
            assert False
        else:
            # If hostname is not specified, default to running locally:
            hostname = 'localhost'

    # Set up working directory:
    wd_path = None
    wd_branch_name = None
    # TODO Accept an option to specify already existing workdir + branch.
    # TODO May not need to set wd_path in some cases?
    if True:
        # Generate checkout name
        wd_name = scriptname
        if hostname is not 'localhost':
            wd_name = hostname + "/" + wd_name
        wd_name = "wd-" + wd_name.replace('/','-')

        # XXX Option to generate checkout name with a random cookie:
        # random_letters = ''.join([random.choice(string.ascii_lowercase) \
        #                           for _ in range(3)] \
        #                          + [random.choice(string.digits) \
        #                             for _ in range(1)])
        # wd_name = wd_name + "-" + random_letters

        wd_path = os.path.join(b.base_dir, wd_name)
        wd_branch_name = 'index' # TODOXXX need to pick a reasonable branch

        print("Using branch {}, checkout name {}" \
              .format(wd_branch_name, wd_name))

    # TODO Better formatting for invocation_args.
    print("Running", scriptname if hostname == 'localhost' \
                                else scriptname+"@"+hostname, \
          ("at " + wd_path + " from") if wd_path else "from",
          script_path, "with", invocation_args)
    print("===")
    b.run_script(hostname, script_path, invocation_args,
                 wd_path=wd_path, wd_branch_name=wd_branch_name,
                 wd_cookie='') # XXX empty cookie defaults to PID

# Subcommand 'gorilla' -- a parable about false negative errors

def detect_gorilla(number):
    gorilla_number = 44 # according to Science, the number 44
                        # indicates that a Gorilla is present in the
                        # project
    time.sleep(0.1) # according to Science, Gorilla detection takes a
                    # non-trivial amount of time
    return number == gorilla_number

def bunsen_gorilla():
    """very important functionality to detect Gorillas;
       cf https://youtu.be/SgdV4SGkD9E"""
    gorilla_detected = False # the null hypothesis i.e. that a Gorilla
                             # is NOT present
    # the Scientific method requires us to test a reasonably large
    # amount of numbers, say 42
    for i in tqdm(iterable=range(42), desc="Detecting Gorilla",
                  leave=False, unit='scientifications'):
        if detect_gorilla(i):
            gorilla_detected = True # according to Science, the null
                                    # hypothesis has been violated
    if gorilla_detected:
        print("According to Science, your project contains a Gorilla.\n"
              "Further testing may be warranted to determine how it got there.")
    else:
        print("It has been scientifically established that:\n"
              "- Your project does NOT contain a Gorilla.")

# Command Line Interface

def sub_init(parser, args):
    b = Bunsen()
    bunsen_init(b)

def sub_checkout_wd(parser, args):
    b = Bunsen(alternate_cookie=str(os.getppid()))
    branch_name = args.branch
    bunsen_checkout_wd(b, branch_name)

# TODO sub_destroy_wd

def sub_run(parser, args):
    # Syntax: bunsen run host +script1 arg1 arg2 ... +script2 arg1 arg2 ... ...
    # TODO Also allow compact syntax of the form +script=arg
    # TODO Syntax will be reused -- split out to parse_invocations() routine.
    hostname = None # optional
    invocations = []
    invocation = None
    for arg in args.args:
        if len(arg) > 0 and arg[0] == '+':
            if invocation is not None:
                invocations.append(invocation)
            invocation = [arg[1:]]
        elif invocation is None and hostname is None:
            hostname = arg
        elif invocation is None:
            # TODO Signal error properly through parser:
            print("Unexpected argument","'"+arg+"'")
            exit(1)
        else:
            invocation.append(arg)
    if invocation is not None:
        invocations.append(invocation)

    if not invocations:
        # TODO Use parser to signal error.
        print("No invocations found " + \
              "(hint: 'bunsen run +script' not 'bunsen run script').")
        exit(1)
    b = Bunsen()
    for invocation in invocations:
        scriptname = invocation[0]
        invocation_args = invocation[1:]
        bunsen_run(b, hostname, scriptname, invocation_args)

def sub_gorilla(parser, args):
    bunsen_gorilla()

def sub_run_or_help(parser, args):
    if len(args.args) > 0 and \
       len(args.args[0]) > 0 and args.args[0][0] == '+':
        sub_run(parser, args)
    else:
        sub_help(parser, args)

def sub_help(parser, args):
    # TODO Add support for 'help subcommand'.
    parser.print_help()

if __name__=="__main__":
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='cmd', metavar='<command>')

    supported_commands = ['init', 'checkout-wd', 'run', 'gorilla', 'help']

    parser_init = subparsers.add_parser('init', \
        help='create directory for bunsen data')
    parser_init.set_defaults(func=sub_init)

    parser_checkout_wd = subparsers.add_parser('checkout-wd', \
        help='check out a bunsen working directory')
    parser_checkout_wd.add_argument('branch', nargs='?', \
        help='name of branch to check out', default='index')
    parser_checkout_wd.set_defaults(func=sub_checkout_wd)

    parser_run = subparsers.add_parser('run', \
        help='run a script with bunsen env')
    parser_run.add_argument('args', nargs=argparse.REMAINDER)
    parser_run.set_defaults(func=sub_run)

    # XXX This was a sanity test for tqdm that got way out of hand.
    # parser_gorilla = subparsers.add_parser('gorilla', \
    #     help='detect gorilla')
    # parser_gorilla.set_defaults(func=sub_gorilla)
    if len(sys.argv) > 1 and sys.argv[1] == 'gorilla':
        sub_gorilla(None, sys.argv[1:])
        exit(0)

    parser_help = subparsers.add_parser('help', \
        help='show this help message and exit')
    parser_help.set_defaults(func=sub_help)

    parser.set_defaults(func=sub_help)

    # XXX Handle $ bunsen +command similarly to $ bunsen run +command
    # TODO: Document bunsen +command shorthand in command line help.
    basic_parser = argparse.ArgumentParser()
    basic_parser.add_argument('args', nargs=argparse.REMAINDER)
    basic_parser.set_defaults(func=sub_run_or_help)

    # XXX Trickery to make sure extra_args end up in the right place.
    if len(sys.argv) > 1 and sys.argv[1] not in supported_commands:
        # TODO: Instead, catch the exception thrown by parser.parse_args()?
        # TODO: Need to print help for the parent parser, not the child parser.
        args = basic_parser.parse_args()
        args.func(basic_parser, args)
    else:
        args = parser.parse_args()
        args.func(parser, args) # XXX pass subparser instead?