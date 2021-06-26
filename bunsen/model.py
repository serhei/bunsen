# Bunsen data model
# Copyright (C) 2019-2021 Red Hat Inc.
#
# This file is part of Bunsen, and is free software. You can
# redistribute it and/or modify it under the terms of the GNU Lesser General
# Public License (LGPL); either version 3, or (at your option) any
# later version.
"""Bunsen data model.

Provides classes representing testruns and testcases stored in a Bunsen repo.
"""

import os
import re
import json
from pathlib import Path, PurePath
import tarfile
import shutil

import git.exc

from bunsen.utils import *
from bunsen.version import __version__

# <TODO: replace invalid-format assertions with BunsenError.>

#########################
# schema for JSON index #
#########################

# <TODO: check m.group('label') vs m.group('extra_label')>

BUNSEN_REPO_VERSION = __version__
"""Current version of the Bunsen repo schema."""
# TODO: use BUNSEN_REPO_VERSION in the model and repo format

branch_regex = re.compile(r"(?P<project>.*)/test(?P<kind>runs|logs)-(?P<year_month>\d{4}-\d{2})(?:-(?P<extra_label>.*))?")
"""Format for testruns and testlogs branch names."""

commitmsg_regex = re.compile(r"(?P<project>.*)/test(?P<kind>runs|logs)-(?P<year_month>\d{4}-\d{2})(?:-(?P<extra_label>[^:]*))?:(?P<note>.*)")
"""Format for commit message summaries created by Bunsen."""

indexfile_regex = re.compile(r"(?P<project>.*)-(?P<year_month>\d{4}-\d{2}).json")
"""Format for indexfile paths in the 'index' branch."""

INDEX_SEPARATOR = '\n---\n'
"""YAML-style separator between JSON objects in the index."""

cursor_regex = re.compile(r"(?:(?P<commit_id>[0-9A-Fa-f]+):)?(?P<path>.*):(?P<start>\d+)(?:-(?P<end>\d+))?")
"""Serialized representation of a Cursor object."""

related_testrun_regex = re.compile(r"(?P<branchname>.*):(?P<commit_id>[0-9A-Fa-f]+)")
"""Serialized reference to a related Testrun object."""
# TODO: use related_testrun_regex in the model and repo format

#####################################
# schema for testruns and testcases #
#####################################

valid_field_types = {
    'testcases',
    'cursor',
    'hexsha',
    'str',
    'metadata',
    # TODO: 'metadata' currently for display only. Support (de)serialization?
}
"""set: Supported field types for Testrun and Testcase.

These include:
- testcases: list of dict {name, outcome, ?subtest, ?origin_log, ?origin_sum, ...}
- cursor: a Cursor object
- hexsha: string -- a git commit hexsha
- str: string
- metadata: subordinate map with the same permitted field types as parent
"""

testrun_field_types = {'testcases':'testcases'}
"""dict: Testrun fields requiring special serialization/deserialization logic.

Key is the name of the field, value is one of the valid_field_types.
"""
# TODO: use 'project' insted of 'tag' in the testrun fields format

testcase_field_types = {'origin_log':'cursor',
                        'origin_sum':'cursor',
                        'baseline_log':'cursor',
                        'baseline_sum':'cursor',
                        'origins': 'metadata',          # XXX used in 2or diff
                        'baseline_origins': 'metadata'} # XXX used in 2or diff
"""dict: Testcase fields requiring special serialization/deserialization logic.

Key is the name of the field, value is one of the valid_field_types.

The origins and baseline_origins fields are nested metadata fields which store
the origin data of the two comparisons in a second-order diff.
"""

cursor_commit_fields = {'origin_log':'bunsen_commit_id',
                        'origin_sum':'bunsen_commit_id',
                        'baseline_log':'baseline_bunsen_commit_id',
                        'baseline_sum':'baseline_bunsen_commit_id'}
"""Cursor fields whose commit_id can be specified at the Testrun's top level.

For compactness, we can omit the commit_id from the string representation of
certain Cursor fields and instead specify it in a field of the Testrun.

Key is the name of the Testcase or Testrun field, value is the name of
the top level Testrun field which contains its commit_id.

TODO: In a second-order diff, bunsen_commit_ids and baseline_commit_ids
are fields which store pairs of (baseline,latest) commit ids. These are
not currently handled by the deserialization logic since we don't yet need
to serialize second-order-diffs.
"""

##############################
# Index, Testlog, and Cursor #
##############################

class Index:
    """Iterator through all Testrun objects for a project in the Bunsen repo.

    Yields instances of class Testrun.

    Attributes:
        project (str): The project that this Index iterates through.
    """

    def __init__(self, bunsen, project, key_function=None, reverse=False,
                 index_source=None):
        """Initialize an Index object iterating a project in a Bunsen repo.

        Args:
            key_function (optional): Sort the index according to
                key_function applied to the Testrun objects.
            reverse (bool, optional): Iterate in reverse of the usual order.
            index_source (optional): Read JSON data from this path or
                data_stream instead of searching the Bunsen repo.
        """
        self._bunsen = bunsen
        self.project = project
        self._key_function = key_function
        self._reverse = reverse
        self._index_source = index_source
        self._index_data_stream = None # if not None, need to close in __del__

    def __del__(self):
        if self._index_data_stream is not None:
            self._index_data_stream.close()

    def _indexfiles(self):
        if self._index_source is not None:
            path = None
            if isinstance(self._index_source, str) \
                or isinstance(self._index_source, Path):
                path = self._index_source
                self._index_data_stream = open(path, 'r')
                data_stream = self._index_data_stream
            else:
                data_stream = self._index_source
            yield (path, data_stream)
        else:
            try:
                commit = self._bunsen.git_repo.commit('index')
                tree = commit.tree
            except git.exc.BadName: # XXX gitdb.exc.BadName
                warn_print("no branch 'index', the Bunsen repo is empty (or invalid)")
                tree = []
            for blob in tree:
                m = indexfile_regex.fullmatch(blob.path)
                if m is not None and m.group('project') == self.project:
                    yield (blob.path, commit.tree[blob.path].data_stream)

    def _raw_key_function(self, json_str):
        testrun = Testrun(self._bunsen, from_json=json_str, summary=True)
        return self._key_function(testrun)

    def _iter_raw_basic(self):
        for path, data_stream in self._indexfiles():
            data = read_decode(data_stream)
            for json_str in data.split(INDEX_SEPARATOR):
                json_str = json_str.strip()
                if json_str == '':
                    # XXX extra trailing INDEX_SEPARATOR
                    continue
                yield json_str

    def _iter_basic(self):
        for json_str in self._iter_raw_basic():
            yield Testrun(self._bunsen, from_json=json_str, summary=True)

    def iter_raw(self):
        """Yield the string representing each Testrun in the JSON file."""
        if self._key_function is None and self._reverse is False:
            # <TODO: Evaluate the correctness of this bit:>
            return self._iter_raw_basic()
        raw_testruns = []
        for json_str in self._iter_raw_basic():
            raw_testruns.append(json_str)
        raw_testruns.sort(key=self._raw_key_function, reverse=self._reverse)
        return raw_testruns.__iter__() # <TODO: Check correctness.>

    def __iter__(self):
        if self._key_function is None and self._reverse is False:
            # <TODO: Evaluate the correctness of this bit:>
            return self._iter_basic()
            # for testrun in self.__iter_basic():
            #     yield testrun
            # return
        testruns = []
        for testrun in self._iter_basic():
            testruns.append(testrun)
        testruns.sort(key=self._key_function, reverse=self._reverse)
        return testruns.__iter__() # <TODO: Check correctness.>

class Testlog:
    """Represents a plaintext log file containing test results.

    The log file may belong to one (or more) testruns
    and be stored in the Bunsen repo,
    or it may be external to the Bunsen repo.

    Attributes:
        path (PurePath): The path of this log file within a Bunsen git tree,
            or the intended path in the git tree for an external log file.
        commit_id (str): Commit hash of the commit which stores this log file
            within a testlogs branch of the Bunsen git repo,
            or None for an external log file.
        blob (git.objects.blob.Blob): A GitPython Blob object for this log file,
            or None for an external log file.
    """

    def __init__(self, bunsen=None, path=None, commit_id=None,
                 blob=None, input_path=None, input_stream=None):
        """Initialize a Testlog object.

        Must specify path and one of
        {bunsen+commit_id+blob, input_path, input_stream}.

        Args:
            bunsen: The Bunsen repo containing this log file,
                or None for an external log file.
            path (str or PurePath): Path of this log file within a Bunsen git tree.
                Should not be an absolute path, even for an external log file.
            commit_id (str): Commit hash of the commit which stores this log file
                within a testlogs branch of the Bunsen repo,
                or None for an external log file.
            blob (git.objects.blob.Blob): A GitPython Blob object for this log file,
                or None for an external log file.
            input_path: Path to an external log file.
            input_stream: Seekable stream for an external log file.
        """

        self._bunsen = bunsen
        self.path = PurePath(path) # TODOXXX was previously str?
        self.commit_id = commit_id

        self.blob = blob
        self._input_path = Path(input_path) if input_path is not None else None
        self._input_stream = input_stream
        self._input_stream_cleanup = False # need to close in __del__
        self._lines = None

        # XXX Populate on demand:
        self._project = None
        self._year_month = None
        self._extra_label = None

    def __del__(self):
        if self._input_stream_cleanup:
            self._input_stream.close()

    @classmethod
    def from_source(cls, source, path=None, input_stream=None):
        """Produce a Testlog from a Testlog, path, or tarfile.ExFileObject.

        Args:
            source: Testlog, external path, or tarfile.ExFileObject.
            path (str or PurePath, optional): Intended path of this log file
                within a Bunsen git tree. Should not be an absolute path.
                Will override an existing path specified by source.
            input_stream: specify the input_stream for this Testlog;
                will override any existing input_stream specified by source."""
        if isinstance(source, str) or isinstance(source, Path):
            assert path is not None # must specify path in git tree
            testlog = Testlog(None, path=path, commit_id=None, input_path=source)
        elif isinstance(source, tarfile.ExFileObject):
            assert path is not None # must specify path in git tree
            testlog = Testlog(None, path=path, input_stream=source)
        elif isinstance(source, Testlog):
            testlog = Testlog.adjust_path(source, path)
            assert testlog.path is not None
        else:
            raise BunsenError("Unknown source '{}' for staging testlog." \
                .format(source))
        if input_stream is not None:
            testlog = Testlog.adjust_input(testlog, input_stream=input_stream)
        return testlog

    @classmethod
    def adjust_path(cls, testlog, path):
        """If path does not match, copy testlog and adjust to match.

        Otherwise, return the original testlog.

        Args:
            path (str or PurePath): Path of this log file in a Bunsen git tree.
                Should not be an absolute path, even for an external log file.
        """
        if path is None or str(path) == str(testlog.path):
            return testlog
        return Testlog(bunsen=testlog._bunsen, path=testlog.path,
            commit_id=testlog.commit_id, blob=testlog.blob,
            input_path=testlog._input_path, input_stream=testlog.input_stream)

    @classmethod
    def adjust_input(cls, testlog, input_path=None, input_stream=None):
        """If input parameters don't match, copy testlog and adjust to match.

        Otherwise, return the original testlog.

        Args:
            input_path: Path to an external log file.
            input_stream: A seekable stream for an external log file.
        """
        # <TODO: Doublecheck correct behaviour if both are set.>
        if input_path is not None or str(input_path) != str(testlog._input_path):
            testlog = Testlog(bunsen=testlog._bunsen, path=testlog.path,
                commit_id=testlog.commit_id, blob=testlog.blob,
                input_path=input_path) # XXX reset input_path
        if input_stream is not None:
             testlog = Testlog(bunsen=testlog._bunsen, path=testlog.path,
                commit_id=testlog.commit_id, blob=testlog.blob,
                input_stream=input_stream) # XXX reset input_stream
        return testlog

    @property
    def external(self):
        """Is this Testlog external to the bunsen repo?"""
        return self._bunsen is None or self.blob is None

    def copy_to(self, dirpath, create_subdirs=False):
        """Copy this logfile into a specified target directory.

        Could be used to extract individual log files from a Bunsen repo,
        or to copy external log files into a git working directory.

        Args:
            create_subdirs (bool, optional): Append the full path of this
                Testlog (self.path) to dirpath. The dirpath is assumed safe,
                while self.path will be sanitized to avoid escaping from
                the target directory.

                The copying operation will create subdirectories as needed.

                This option is useful for complex testsuites whose results are
                organized in subdirectories.
        """

        target_name = self.path.name
        base_dir = Path(dirpath)
        target_dir = base_dir
        if create_subdirs:
            relative_dir = self.path.parent
            target_dir = target_dir.joinpath(relative_dir)
            target_dir = sanitize_path(target_dir, base_dir)
            if not target_dir.exists():
                target_dir.makedir(parents=True)
        target_path = target_dir.joinpath(target_name)

        if self.external and self._input_stream is None:
            shutil.copy(self._input_path, target_path)
            return

        f = target_path.open('wb')
        content = self._data_stream.read()
        if isinstance(content, str):
            content = content.encode('utf-8')
        assert(isinstance(content, bytes))
        f.write(content)
        f.close()
    # TODOXXX OLD VERSION, need to doublecheck
    # def copy_to(self, dirpath):
    #     if self._has_input_file:
    #         # TODO: Would be better to produce a GitPython commit directly?
    #         # !!! TODOXXX Sanitize testlog_name to avoid '../../../dir' !!!
    #         # For now, just stick to the basename for safety. This
    #         # won't work for more complex testsuites whose results are
    #         # organized in subdirectories.
    #         target_name = os.path.basename(self.path)
    #         target_path = os.path.join(dirpath, target_name)
    #         f = open(target_path, 'wb')
    #         content = self._data_stream.read()
    #         # TODO: Handle isinstance(content,str)
    #         if isinstance(content, str):
    #             content = content.encode('utf-8')
    #         f.write(content)
    #         f.close()
    #     else:
    #         shutil.copy(self.path, dirpath)

    def _get_commit_tag(self):
        if self._bunsen is not None and self.commit_id is not None:
            self._project, self._year_month, self._extra_label = \
                self._bunsen.commit_tag(self.commit_id)

    @property
    def project(self):
        """The project which this Testlog object's testrun belongs to."""
        if self._project is None: self._get_commit_tag()
        return self._project

    @property
    def year_month(self):
        """The year_month of this Testlog object's testrun."""
        if self._year_month is None: self._get_commit_tag()
        return self._year_month

    @property
    def extra_label(self):
        """The extra_label of this Testlog object's testrun.

        May be None if the testrun was stored without an extra_label."""
        if self._extra_label is None: self._get_commit_tag()
        return self._extra_label

    # Kept private to ensure open fds are cleaned up correctly,
    # and because the same data_stream object is returned repeatedly:
    @property
    def _data_stream(self):
        # For an external log file, use self._input_stream or self._input_path,
        # since self.path should be relative to the git tree:
        if self.external and self._input_stream is None:
            self._input_stream_cleanup = True
            self._input_stream = self._input_path.open('r')
            return self._input_stream

        # self._input_stream could be cached from a prior invocation.
        # XXX Assume no one is still reading and it's safe to reset the stream:
        if self._input_stream is not None:
            self._input_stream.seek(0)
            return self._input_stream

        # For an internal log file, use self.blob:
        return self.blob.data_stream

    def _data_stream_readlines(self):
        if not self.external:
            # Use centralized caching to avoid reading _lines multiple times
            # in separate Testlog objects created by separate Cursor objects.
            return self._bunsen._testlog_readlines(self.commit_id, self.path)

        try:
            return readlines_decode(self._input_stream, must_decode=False)
            # XXX prefer to decode utf-8 later in line()
        except UnicodeDecodeError:
            warn_print("UnicodeDecodeError in Testlog {}".format(self.path))
            return [""]
    # TODOXXX OLD VERSION, need to doublecheck
    #     if self._bunsen is not None and self.path is not None \
    #        and self.commit_id is not None:
    #         # Avoid reading _lines multiple times in different Cursor objects.
    #         return self._bunsen._testlog_readlines(self.path, self.commit_id)
    #     try:
    #         data_stream = self._data_stream
    #         # TODOXXX Problem with GitPython blob.data_stream returning OStream.
    #         # print("DEBUG", data_stream) -> TextIOWrapper in commit_logs
    #         #if isinstance(data_stream, OStream): # TODOXXX
    #         #    return data_stream.read().decode('utf8').split('\n')
    #         return data_stream.readlines()
    #     except UnicodeDecodeError: # yes, it happens
    #         warn_print("UnicodeDecodeError in TestLog, path={}".format(self.path))
    #         return [""]

    def line(self, line_no):
        """Return text at specified (one-indexed) line number in log file."""
        if line_no < 1:
            raise BunsenError("out of range line number {} for Testlog {}" \
                .format(line_no, self.path))
        if self._lines is None:
            self._lines = self._data_stream_readlines()
            # XXX: Could clear _lines when too many Testlog objects are
            # in memory, but I have not observed this problem in practice.
        if line_no-1 >= len(self._lines) and line_no == 1:
            return "" # XXX empty file
        if line_no-1 >= len(self._lines):
            raise BunsenError("out of range line number {} for Testlog {}" \
                .format(line_no, self.path))
        line = self._lines[line_no-1]
        try:
            if isinstance(line, bytes):
                line = line.decode('utf-8')
            return line
        except UnicodeDecodeError:
            warn_print("UnicodeDecodeError in Testlog {} line {}" \
                .format(self.path, line_no))
            return ""

    def __len__(self):
        """Number of lines in log file."""
        if self._lines is None:
            self._lines = self._data_stream_readlines()
            # XXX: Could clear _lines when too many Testlog objects are
            # in memory, but I have not observed this problem in practice.
        return len(self._lines)

class Cursor:
    """Identifies a single line or range of lines within a Testlog or log file.

    Can be iterated to yield single-line Cursor objects for all lines
    within the Cursor's range.

    Can be stored within a Testcase object and serialized to reference
    ranges of lines within a log file from the Bunsen index.

    Attributes:
        testlog (:obj:`Testlog`): The log file referenced by this Cursor object.
        line_start (int): The (one-indexed) line number at the start
            of this Cursor object's range.
        line_end (int): The (one-indexed) line number at the end
            of this Cursor object's range.
        ephemeral (bool): If True, iteration will be sped up by having __iter__
            yield the same Cursor object repeatedly, adjusting its line_start
            and line_end fields, instead of creating a new Cursor for each line.
            The returned single-line Cursor object is also marked as ephemeral
            and is not suitable for serialization.
    """

    def __init__(self, source=None, from_str=None,
                 commit_id=None, start=None, end=None, # TODOXXX start=1 was the previous default, check code below
                 path=None, input_stream=None, ephemeral=False):
        """Initialize a Cursor.

        Supports the following calls:
        - Cursor(source=input_path, path=path, optional start, optional end)
        - Cursor(source=tarfile.ExFileObject, path=path, optional start, optional end)
        - Cursor(source=Testlog, optional start, optional end)
        - Cursor(source=Bunsen, from_str=str, optional commit_id=hexsha)
        - Cursor(start=Cursor, optional end=Cursor)

        If neither start nor end are specified, the resulting Cursor covers
        the entire logfile.

        If start is specified and end is not, the resulting Cursor is
        a single-line cursor covering start,
        except for Cursor(start=cur, end=None) where start is a Cursor.
        In that case the resulting cursor will have the same start and
        end as cur.

        Args:
            source (optional): Testlog, Bunsen, external path, or
                tarfile.ExFileObject specifying the
                the log file referenced by this Cursor object.
            from_str (str, optional): Specify attributes as a
                string of the form '[<commit_id>:]<path>:<start>[-<end>]'.
                Used when deserializing testruns from JSON representation.
            commit_id (str, optional): Hexsha of the Bunsen commit which stores
                the log file referenced by this Cursor object.
                Used in conjunction with from_str and _cursor_commits fields.
                Only used if from_str *does not* specify a commit.
            start: Line number or Cursor object specifying
                the first line of the referenced range.
            end: Line number or Cursor object specifying
                the last line of the referenced range.
            path: The path of the log file within a Bunsen git tree;
                will override an existing path specified by source or from_str.
            input_stream: specify the input_stream for this Cursor's Testlog;
                will override any existing input_stream specified by source.
            ephemeral (bool, optional): Used to mark this Cursor as ephemeral
                (not suitable for serialization) for faster iteration.
        """
        from bunsen.repo import Bunsen # XXX delayed import

        # XXX parsing from_str can be delayed
        self._delay_parse = None
        self._delay_commit_id = None
        self._delay_source = None
        self._delay_path = None
        self._delay_input_stream = None

        # Cursor(source=input_path, path=path, optional start, optional end) -> testlog=None
        # Cursor(source=tarfile.ExFileObject, path=path, optional start, optional end) -> testlog=None
        # Cursor(source=Testlog, optional start, optional end)
        if isinstance(source, str) or isinstance(source, Path) or \
            isinstance(source, tarfile.ExFileObject) or \
            isinstance(source, Testlog):
            assert from_str is None and commit_id is None # Cursor from source
            testlog = Testlog.from_source(source, path=path, input_stream=input_stream)
        # Cursor(source=Bunsen, from_str=str, optional commit_id=hexsha)
        elif isinstance(source, Bunsen):
            assert start is None and end is None # Cursor from_str

            # XXX Performance fix: delay parsing until a field of this Cursor
            # (any of testlog, line_start, line_end) is actually accessed:
            self._delay_parse = from_str
            self._delay_commit_id = commit_id
            self._delay_source = source
            self._delay_path = path # XXX overrides path in self._delay_parse
            self._delay_input_stream = input_stream
            testlog, start, end = None, None, None
            #self._delayed_parse() # XXX test effect of performance fix
        # Cursor(start=Cursor, optional end=Cursor)
        else:
            if end is None: end = start
            assert isinstance(start, Cursor) and isinstance(end, Cursor) # Cursor from Cursor(s)
            assert source is None and from_str is None and commit_id is None # Cursor from Cursor(s)

            testlog = start.testlog
            if not path:
                path = testlog.path
            if path != end.testlog.path:
                warn_print("combining Cursors with different paths: {} + {}" \
                           .format(start.to_str(), end.to_str()))
            start = start.line_start
            end = end.line_end

        # Default values for start and end:
        if start is None and end is None:
            start, end = 1, None
        elif start is not None and end is None:
            end = start # <TODOXXX: verify>
        elif start is None:
            start = 1

        if testlog is not None and path is not None and str(testlog.path) != str(path):
            testlog = Testlog.adjust_path(testlog, path=path)
        if testlog is not None:
            testlog = Testlog.adjust_input(testlog, input_stream=input_stream)
        self._testlog = testlog
        self._line_start = start
        self._line_end = end
        if self._line_end is None and self._testlog is not None:
            self._line_end = len(self._testlog)

        # XXX Enabling ephemeral leads __iter__ to yield the same Cursor object repeatedly.
        # Makes the yielded cursors unsafe to store in a testrun since they will change.
        self.ephemeral = ephemeral

    def _delayed_parse(self):
        if self._delay_parse is None:
            return

        m = cursor_regex.fullmatch(self._delay_parse)
        assert m is not None # cursor_regex (match failure)

        source = self._delay_source

        if self._delay_path is None:
            self._delay_path = m.group('path')
        path = self._delay_path
        assert path is not None # cursor_regex (missing path)

        commit_id = self._delay_commit_id
        if m.group('commit_id') is not None:
            #assert commit_id is None # XXX may not be true during parsing
            commit_id = m.group('commit_id')
        assert commit_id is not None # cursor_regex (missing commit_id)

        input_stream = self._delay_input_stream

        testlog = source.testlog(path, commit_id, input_stream=input_stream)
        start = int(m.group('start'))
        end = int(m.group('end')) if m.group('end') is not None else start

        # XXX Only set the values if they were not overwritten by a setter:
        if self._testlog is None:
            self._testlog = testlog
        if self._line_start is None:
            self._line_start = start
        if self._line_end is None:
            self._line_end = end

    @property
    def line_start(self):
        if self._line_start is None:
            self._delayed_parse()
        return self._line_start

    @line_start.setter
    def line_start(self, val):
        self._line_start = val

    @property
    def line_end(self):
        if self._line_end is None:
            self._delayed_parse()
        return self._line_end

    @line_end.setter
    def line_end(self, val):
        self._line_end = val

    @property
    def testlog(self):
        if self._testlog is None:
            self._delayed_parse()
        return self._testlog

    @testlog.setter
    def testlog(self, val):
        self._testlog = val

    @property
    def single_line(self):
        """Is this Cursor a single-line Cursor?"""
        return self.line_start == self.line_end

    def __iter__(self):
        """Yields a single-line Cursor for each line in this Cursor's range."""
        if self.ephemeral:
            cur = Cursor(source=self.testlog, start=-1, end=-1, ephemeral=True)
            for i in range(self.line_start, self.line_end+1):
                cur.line_start = i; cur.line_end = i
                yield cur
        else:
            for i in range(self.line_start, self.line_end+1):
                yield Cursor(source=self.testlog, start=i, end=i)

    @property
    def line(self):
        """The line identified by this Cursor.

        Defined for single-line Cursor objects only.
        """
        assert self.single_line
        return self.testlog.line(self.line_start)

    def contents(self, context=0, snip_lines=None,
                 snip_message="... skipped {num_lines} line{s} ..."):
        """Return the text within this Cursor's range.

        Returns the full text identified within this Cursor's range,
        or an excerpt from the start and end of the range.

        Args:
            context (int, optional): Additionally include this many lines
                before the start and also after the end of this Cursor's range.
            snip_lines (int or bool, optional): Include at most this many lines
                from this Cursor's range, skipping lines in the middle to stay
                within the maximum.
            snip_message (optional str): Message to insert in place of the
                skipped lines. Can include the number of lines skipped, e.g.
                "... skipped {num_lines} line{s} ...".
        """

        if context is None: context = 0
        if snip_lines is True: snip_lines = 50

        con_start = max(self.line_start-context,1)
        con_end = min(self.line_end+context,len(self.testlog))
        snip_start, snip_end = None, None
        num_snipped = 0
        if snip_lines and con_end-con_start+1 > snip_lines:
            keep_back = snip_lines / 2 # rounds down
            keep_front = snip_lines - keep_back # rounds up
            assert (keep_front+keep_back == snip_lines) # <TODO: should always hold>
            snip_start, snip_end = con_start + keep_front, con_end - keep_back
            num_snipped = snip_end-snip_start+1
            assert (num_snipped == con_end-con_start+1-snip_lines) # <TODO: should always hold>

        s = ""
        started_snip = False
        for i in range(con_start,con_end+1):
            if not snip_lines or i < snip_start or i > snip_end:
                s += self.testlog.line(i) + "\n"
            elif snip_lines and not started_snip:
                s += snip_message.format({'num_lines':snip_end-snip_start,
                    's': "s" if num_snipped != 1 else ""}) + "\n"
                started_snip = True
        return s

    def to_str(self, serialize=False, skip_commit_id=False):
        """Return a string representation of this Cursor.

        The returned representation has the form
        '[<commit_id>:]<path>:<start>[-<end>]', where '<commit_id>' and '<end>'
        may be omitted.

        Args:
            serialize (optional bool): Perform additional safety checks
                and output warnings if the Cursor was not suitable for
                serialization. Continue regardless of warnings so that a single
                error does not ruin a long batch process. <TODO: The resulting
                incorrect data can be corrected by fixing the underlying parser
                bug and running 'bunsen rebuild'. Need to make sure analysis
                scripts skip malformed data gracefully.>
            skip_commit_id (optional bool): Don't include a commit_id in
                this Cursor's string representation (it will be provided
                elsewhere in the serialized representation of the testrun).
        """

        repr = ''
        if not skip_commit_id and self.testlog.commit_id is not None:
            repr += self.testlog.commit_id + ':'
        repr += str(self.testlog.path) if self.testlog.path else \
                '<unknown>'
        repr += ':' + str(self.line_start)
        if self.line_end is not None and self.line_end != self.line_start:
            repr += '-' + str(self.line_end)

        if serialize and self.ephemeral:
            # XXX may have been modified between creation and serialization
            warn_print("serializing an ephemeral Cursor {}" \
                .format(repr))
        if serialize and not self.testlog.path:
            warn_print("serializing an incomplete Cursor {}" \
                .format(repr))

        return repr

########################
# Testcase and Testrun #
########################

# TODOXXX Validate required metadata e.g. bunsen_branch, bunsen_commit_id, year_month? before committing a testcase.

# XXX in common between Testcase and Testrun
def _serialize_testcases(testcases, parent_testrun):
    serialized_testcases = []
    for testcase in testcases:
        # XXX Properly serialize a testcase that is a dict:
        if isinstance(testcase, dict):
            testcase = Testcase(from_json=testcase,
                                parent_testrun=parent_testrun)
        serialized_testcases.append(testcase.to_json(as_dict=True))
    return serialized_testcases

# XXX in common between Testcase and Testrun
def _deserialize_testcases(testcases, parent_testrun):
    deserialized_testcases = []
    for testcase in testcases:
        deserialized_testcase = \
            Testcase(from_json=testcase, parent_testrun=parent_testrun)
        deserialized_testcases.append(deserialized_testcase)
    return deserialized_testcases

# Testrun/Testcase fields that should not be added to JSON:
_testrun_base_fields = {'bunsen', # Testrun only
                        'parent_testrun', # Testcase only
                        'field_types',
                        'testcase_field_types', # Testrun only
                        # TODO: '_cursor_commit_ids', # Testrun only
                        'summary'} # Testrun only
class Testcase(dict):
    """Represents a single testcase within a Testrun.

    Subclasses dict to support reading and writing arbitrary fields as dict
    fields or as attributes. These fields will be serialized together with the
    standard Testcase attributes.

    Attributes:
        name (str): The name of the testcase.
        outcome (str): Outcome code of the testcase (e.g. PASS, FAIL, etc.).
        subtest (str, optional): The name of the subtest with this outcome code.
            For DejaGnu format logs, we typically store one PASS outcome for
            a testcase which passed in its entirety, but separate FAIL outcomes
            in separate Testcase objects, one for each subtest that failed
            within the testcase.
        origin_log (Cursor, optional): Locates this testcase in a full test log
            file (e.g. a file in DejaGnu '.log' format).
        origin_sum (Cursor, optional): Locates this testcase in a test summary
            file (e.g. a file in DejaGnu '.sum' format).
        parent_testrun (Testrun, optional): The Testrun object that
            this Testcase belongs to. Not serialized.
        field_types (dict): Dictionary of fields (names and types
            from valid_field_types) which will receive special treatment
            during serialization. Includes all testcase_field_types
            by default. Not serialized.
        problems (str, optional): After running validate(), will contain a
            description of any problems that make this Testcase unsuitable
            for serialization, or None if there aren't any problems.
    """

    # TODOXXX elsewhere, rename Testcase(fields=) to Testcase(field_types=)
    def __init__(self, from_json=None, field_types=None, parent_testrun=None,
                 bunsen=None):
        """Create empty Testcase or parse Testcase from JSON string or dict.

        Args:
            from_json (str or dict, optional): JSON data for the testcase.
            field_types (dict, optional): Dictionary of additional field types
                requiring special treatment during serialization,
                in the same format as testcase_field_types.
            parent_testrun (Testrun, optional): The Testrun object that
                this Testcase belongs to. (Note that Testcase objects
                for a Testrun can be created with the Testrun.add_testcase()
                method, which will use the types specified in the
                Testrun object's testcase_field_types.)
            bunsen (Bunsen, optional): Used only as a fallback for
                deserializing Cursors when parent_testrun is None.
        """

        # XXX Populate fields so __setattr__ doesn't add them to JSON dict.
        for field in _testrun_base_fields:
            if field not in self.__dict__:
                self.__dict__[field] = None

        self.parent_testrun = parent_testrun
        if field_types is None and self.parent_testrun is not None:
            field_types = self.parent_testrun.testcase_field_types
        elif field_types is None:
            field_types = {}

        # Populate self.field_types from fields, testcase_field_types:
        self.field_types = dict(testcase_field_types)
        for field, field_type in field_types.items():
            assert field_type in valid_field_types # BUG in testcase_field_types
            self.field_types[field] = field_type

        if from_json is not None:
            self._deserialize_testcase(from_json=from_json, bunsen=bunsen)

    def _deserialize_testcase(self, deserialized_testcase=None, from_json={},
                              bunsen=None):
        if deserialized_testcase is None:
            deserialized_testcase = self

        json_data = from_json # XXX handles from_json(dict)
        if isinstance(from_json, str) or isinstance(from_json, bytes):
            json_data = json.loads(from_json)
        assert isinstance(json_data, dict) # Testcase from_json

        for field, value in json_data.items():
            if field not in self.field_types:
                pass
            elif self.field_types[field] == 'testcases':
                value = _deserialize_testcases(value, self.parent_testrun)
            elif self.field_types[field] == 'metadata':
                # Nested Testcase metadata contains Testcase fields,
                # but we don't consider it as a Testcase object:
                value = self._deserialize_testcase({}, from_json=value,
                    bunsen=bunsen)
                # TODOXXX OLD VERSION
                # value = dict(Testcase(from_json=value, field_types=fields,
                #                       parent_testrun=self.parent_testrun))
            elif self.field_types[field] == 'cursor' \
                and not isinstance(value, Cursor) \
                and self.parent_testrun is not None:
                commit_id = None
                if field in self.parent_testrun._cursor_commit_ids:
                    commit_id = self.parent_testrun._cursor_commit_ids[field]
                value = Cursor(source=self.parent_testrun.bunsen,
                    commit_id=commit_id, from_str=value)
            elif self.field_types[field] == 'cursor' \
                and not isinstance(value, Cursor):
                assert bunsen is not None # no parent_testrun and no bunsen
                value = Cursor(source=bunsen, from_str=value)
            # TODOXXX OLD VERSION OF PREV 2 CLAUSES
            # elif self.field_types[field] == 'cursor' \
            #      and not isinstance(value, Cursor):
            #     value = Cursor(source=self.parent_testrun.bunsen,
            #                    commit_id=self.parent_testrun.bunsen_commit_id,
            #                    from_str=value)
            elif self.field_types[field] not in {'str','hexsha','cursor'}:
                raise BunsenError("BUG: unknown type '{}' for testcase field '{}'" \
                    .format(self.field_types[field], field))
            else:
                pass
            deserialized_testcase[field] = value
        return deserialized_testcase

    def validate(self):
        """Verify that this Testcase includes required fields for serialization.

        If there are problems, store an explanation in self.problems.

        Returns:
            bool
        """
        valid, problems = True, ""
        if not ('name' in self and isinstance(self.name, str)):
            valid = False
            problems += "missing/incorrect name, "
        if not ('outcome' in self and isinstance(self.outcome, str)):
            valid = False
            problems += "missing/incorrect outcome, "
        if problems.endswith(", "):
            problems = problems[:-2]
        if not valid:
            self.problems = problems
        return valid

    # XXX @property is incompatible with the JSON attr protocol below
    def outcome_line(self):
        """Return the last line corresponding to this Testcase in the log file.

        For DejaGNU format logs, this line reports the testcase outcome.

        Will return None if this Testcase does not reference a log file.
        """
        cur = None
        if 'origin_sum' in self:
            cur = self.origin_sum
        elif 'origin_log' in self:
            cur = self.origin_log
        else:
            return None
        assert isinstance(cur, Cursor) # origin_sum, origin_log not deserialized
        cur = Cursor(cur); cur.line_start = cur.line_end
        return cur.line

    def to_json(self, pretty=False, as_dict=False,
                extra_fields={}):
        """Serialize Testcase data to a JSON string or dict.

        Args:
            pretty (bool or int, optional): Output the JSON as a properly
                indented string instead of as a compact string. Passing an
                int configures the indentation level (default 4).
            as_dict (bool, optional): Return a dict instead of a string.
            extra_fields (dict, optional): Dictionary of additional fields to
                include in the JSON. These will override any fields of the same
                name already present in the Testcase object.
        """
        serialized_testcase = {}
        fields = dict(self)
        fields.update(extra_fields)
        for field, value in fields.items():
            if isinstance(value, Cursor):
                # Serialize regardless of self.field_types.
                skip_commit_id = self.parent_testrun is not None \
                    and self.parent_testrun.should_skip_commit_id(value, field)
                value = value.to_str(serialize=True,
                    skip_commit_id=skip_commit_id)
            elif field not in self.field_types:
                pass
            elif self.field_types[field] == 'testcases':
                value = _serialize_testcases(value, self.parent_testrun)
            elif self.field_types[field] not in {'str','hexsha','cursor'}:
                raise BunsenError("BUG: unknown type '{}' for testcase field '{}'" \
                    .format(self.field_types[field], field))
            else:
                pass # no special processing needed
            serialized_testcase[field] = value
        # <TODO: Could use json.dump instead of json.dumps?>
        if as_dict:
            return serialized_testcase
        elif pretty:
            indent = pretty if isinstance(pretty,int) else 4
            return json.dumps(serialized_testcase, indent=indent)
        else:
            return json.dumps(serialized_testcase)

    # XXX: Protocol to support reading/writing arbitrary JSON fields as attrs:

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
            # XXX This is a builtin field. Assign None so later
            # __setattr__ calls don't add this field to the JSON dict!
            self.__dict__[field] = None

class Testrun(dict):
    """Represents a single testsuite run.

    Subclasses dict to support reading and writing arbitrary fields as dict
    fields or as attributes. These fields will be serialized together with the
    standard Testrun attributes.

    <TODO> Example of JSON representation for Testrun, Testrun with problems.

    <TODO> Regenerate project, year_month, extra_label when deserializing? Will simplify commit_tag.

    Attributes:
        bunsen (Bunsen): Bunsen repo that this Testrun belongs to.
            Not serialized.
        project (str, optional): Used to specify the project this Testrun
            belongs to. Removed when the testrun is committed.
            Used to generate the bunsen_testruns_branch for this Testrun
            if one was not provided (as well as the bunsen_testlogs_branch
            for a primary testrun).
        year_month (str, optional): Used to specify the year_month for this
            Testrun. Removed when the testrun is committed.
            Used to generate the bunsen_testruns_branch for this Testrun
            if one was not provided (as well as the bunsen_testlogs_branch
            for a primary Testrun).
        extra_label (str, optional): Used to specify an extra_label for a
            primary Testrun in a Bunsen commit. Removed when the Testrun is
            committed. Used to generate the bunsen_testlogs_branch for a
            primary testrun.
        bunsen_version (str): The version of Bunsen used to originally generate
            this testrun. Added automatically when the testrun is committed.
        bunsen_commit_id (str): The hexsha of the commit in the Bunsen Git repo
            storing the test log files corresponding to this Testrun.
            Added automatically when the Testrun is committed.
        bunsen_testlogs_branch (str): Name of the branch in the Bunsen Git repo
            storing the test log files corresponding to this Testrun.
            Added automatically when the Testrun is committed,
            unless the commit does not include any test log files.
        bunsen_testruns_branch (str): Name of the branch in the Bunsen Git repo
            storing the full representation of this testrun.
            Added automatically when the Testrun is committed.
        related_testruns (list of str, optional): The names of branches storing
            related testruns in other projects.
            Added automatically when this Testrun is committed
            as a primary testrun.
        testcases (list, optional): List of Testcase objects recording
            individual testcase outcomes.
        summary (bool): If True, this Testrun is a summary of the testsuite run
            and does not include individual testcase outcomes.
            (Summary Testruns are stored within index files.) Not serialized.
        field_types (dict): Dictionary of fields (names and type
            from valid_field_types) which will receive special treament
            during serialization. Includes all testrun_field_types
            by default. Not serialized.
        testcase_field_types (dict): Dictionary of additional fields
            in nested Testcase objects which will receive special treatment
            during serialization. Does not include default fields from
            testcase_field_types, which will be added by the Testcase class.
            Not serialized.
        problems (str, optional): After running validate(), will contain a
            description of any problems that make this Testrun unsuitable
            for serialization, or None if there aren't any problems.
    """

    def __init__(self, bunsen=None, from_json=None, summary=False,
                 field_types={}, testcase_field_types={}):
        """Create empty Testrun or parse Testrun from JSON string or dict.

        Args:
            bunsen (Bunsen): Bunsen repo that this Testrun belongs to.
            from_json (str or dict, optional): JSON data for the testsuite run.
            summary (bool): If True, produce a summary Testrun which omits
                individual testcase outcomes. If False, testcase outcomes
                will be included only if they are present in the JSON data
                and the Testrun's 'summary' field will be set accordingly.
            field_types (dict, optional): Dictionary of additional field types
                requiring special treatment during serialization,
                in the same format as testrun_field_types.
            testcase_field_types (dict, optional): Dictionary of additional
                field types for nested Testcase objects
                requiring special treatment during serialization,
                in the same format as testcase_field_types.
        """

        # XXX: Populate fields so __setattr__ won't add them to the JSON dict:
        for field in _testrun_base_fields:
            if field not in self.__dict__:
                self.__dict__[field] = None

        self.bunsen = bunsen

        # Populate self.field_types from field_types, testrun_field_types:
        self.field_types = dict(testrun_field_types)
        for field, field_type in field_types.items():
            assert field_type in valid_field_types # BUG in testrun_field_types
            self.field_types[field] = field_type

        self.testcase_field_types = testcase_field_types

        self.summary = summary
        if not self.summary:
            self.testcases = []

        if from_json is not None:
            self._deserialize_testrun(from_json=from_json)

    def _deserialize_testrun(self, from_json={}):
        json_data = from_json # XXX handles from_json(dict)
        if isinstance(from_json, str) or isinstance(from_json, bytes):
            json_data = json.loads(from_json)
        assert isinstance(json_data, dict) # BUG from_json is not str, bytes or dict
        defer_fields = [] # XXX Defer parsing until _cursor_commit_ids are known.
        self._cursor_commit_ids = {} # XXX field_name -> bunsen_commit_id
        for cursor_field, commit_field in cursor_commit_fields.items():
            self._cursor_commit_ids[commit_field] = None
        for field, value in json_data.items():
            if field not in self.field_types:
                pass
            elif self.field_types[field] == 'testcases' \
                and self.summary:
                continue # omit testcases when reading only summary
            elif self.field_types[field] == 'testcases':
                defer_fields.append(field)
            elif self.field_types[field] == 'cursor':
                defer_fields.append(field)
            elif self.field_types[field] not in {'str','hexsha'}:
                raise BunsenError("BUG: unknown type '{}' for testrun field '{}'" \
                    .format(self.field_types[field], field))
            else:
                pass # no special processing needed
            if self.summary and field == 'testcases':
                continue # omit testcases when reading only summary
            if field in self._cursor_commit_ids:
                self._cursor_commit_ids[field] = value
            self[field] = value
        for field in defer_fields:
            self[field] = self._deserialize_testrun_field(field, self[field])

        if 'testcases' not in json_data:
            self.summary = True
        return self

    def _deserialize_testrun_field(self, field, value):
        if self.field_types[field] == 'testcases':
            value = _deserialize_testcases(value, parent_testrun=self)
        elif self.field_types[field] == 'metadata':
            # Nested Testrun metadata contains Testrun fields:
            value = self._deserialize_testrun_metadata(value)
        elif self.field_types[field] == 'cursor' \
            and not isinstance(value, Cursor):
            assert self.bunsen is not None # Bunsen repo required for parsing Cursor
            commit_id = self.cursor_commit_id(field)
            value = Cursor(source=self.bunsen, from_str=value,
                commit_id=commit_id)
        return value

    # Deserialize nested metadata. Don't collect additional _cursor_commit_ids:
    def _deserialize_testrun_metadata(self, metadata):
        deserialized_testrun = {}
        for field, value in metadata.items():
            value = self._deserialize_testrun_field(field, value)
            deserialized_testrun[field] = value
        return deserialized_testrun

    def add_testcase(self, name, outcome, **kwargs):
        """Append a new Testcase object to the Testrun data.

        Args:
            name (str): The name of the testcase.
            outcome (str): Outcome code of the testcase (e.g. PASS, FAIL, etc.).

        Any additional keyword arguments (e.g. subtest) will be added to the
        Testcase object.

        Returns:
            The newly created Testcase object.
        """
        testcase = Testcase({'name':name, 'outcome':outcome},
            parent_testrun=self)
        for field, value in kwargs.items():
            testcase[field] = value
        if 'testcases' not in self:
            self.summary = False
            self.testcases = []
        self.testcases.append(testcase)
        return testcase

    def cursor_commit_id(self, field):
        """Return the cursor_commit_id for the specified field, if any.

        Returns None if the field does not have an associated commit_id.
        """
        commit_id = None
        # XXX Only available if the Testrun was deserialized:
        # if field in self._cursor_commit_ids:
        #     commit_id = self._cursor_commit_ids[field]
        if field in cursor_commit_fields and field in self:
            commit_id = self[field]
        return commit_id

    def should_skip_commit_id(self, cur, field):
        """Is a Cursor stored in this field covered by a cursor_commit_id?"""
        commit_id = self.cursor_commit_id(field)
        if commit_id is None:
            return False
        if cur.testlog.commit_id is not None and \
            cur.testlog.commit_id != commit_id:
            # cur.testlog overrides cursor_commit_id with a different value
            return False
        return True

    # XXX: To avoid confusion, don't mark this as a property:
    def commit_tag(self):
        """The (project, year_month, extra_label) values for this Testrun.

        These values are used to select a branch name for storing the Testrun's
        JSON representation in the Bunsen repo.
        """
        project, year_month, extra_label = None, None, None
        if 'project' in self: project = self.project
        if 'year_month' in self: year_month = self.year_month
        if 'extra_label' in self: extra_label = self.extra_label
        # XXX if (project is None or year_month is None or extra_label is None) \
        if (project is None or year_month is None) \
            and 'bunsen_testruns_branch' in self:
            m = branch_regex.fullmatch(self.bunsen_testruns_branch)
            assert m is not None # bunsen_testruns_branch
            if project is None: project = m.group('project')
            if year_month is None: year_month = m.group('year_month')
            if extra_label is None: extra_label = m.group('extra_label')
        # XXX extra_label is usually only present in bunsen_testlogs_branch
        if m.group('extra_label') is None and 'bunsen_testlogs_branch' in self:
            m = branch_regex.fullmatch(self.bunsen_testlogs_branch)
            extra_label = m.group('extra_label')
        return project, year_month, extra_label

    def get_project_name(self):
        """Return the project name which this Testrun is stored under in the
        Bunsen repository."""
        if 'bunsen_testruns_branch' in self:
            elts = self.bunsen_testruns_branch.split('/')
            return elts[0]
        return "unknown"

    # Return configuration properties of this Testrun as printable strings,
    # or "<unknown PROPERTY>" if unknown.  Returns a dictionary containing
    # keys for architecture, board, branch, version.

    def get_info_strings(self):
        """Return configuration properties of this Testrun as printable
        strings, or "<unknown PROPERTY>" if unknown.

        Returns:
            dict, containing keys for 'architecture', 'board', 'branch', and 'version'
        """
        info = dict()
        if 'arch' in self:
            info['architecture'] = self.arch
        else:
            info['architecture'] = '<unknown arch>'

        if 'target_board' in self:
            info['target_board'] = self.target_board
        else:
            info['target_board'] = '<unknown board>'

        if 'source_branch' in self:
            info['branch'] = self.source_branch
        else:
            info['branch'] = '<unknown branch>'

        if 'version' in self:
            info['version'] = self.version
        else:
            info['version'] = '<unknown version>'

        return info

    def validate(self, project=None, year_month=None, extra_label=None,
                 cleanup_metadata=False, validate_testcases=False):
        """Verify that this Testrun includes required fields for serialization.

        If there are problems, store an explanation in self.problems.

        Args:
            project (str, optional): The project to use for this Testrun,
                only if one is not already specified in the Testrun's fields.
            year_month (str, optional): The year_month to use for this Testrun,
                only if one is not already specified in the Testrun's fields.
            extra_label (str, optional): Optional extra_label to use for this
                Testrun, only if one is not already specified.
            cleanup_metadata (bool, optional): If True, modify this
                Testrun for serialization in the Bunsen Git repo.
                In particular, removee any (project, year_month, extra_label)
                fields and create (if not alreay present) a single
                bunsen_testruns_branch field containing the same information.
                Raise a BunsenError if any problems will prevent the Testrun
                from being serialized. Defaults to False.
            validate_testcases (bool, optional): If True, also validate all
                testcases within this Testrun. Defaults to False.

        Returns:
            bool
        """
        problems = ""
        raise_error = False

        # Populate required metadata:
        if cleanup_metadata:
            # Testrun commit_tag fields will override commit_tag args:
            if 'project' in self:
                project = self.project; del self['project']
            if 'year_month' in self:
                year_month = self.year_month; del self['year_month']
            if 'extra_label' in self:
                extra_label = self.extra_label; del self['extra_label']

            if project is None and 'bunsen_testruns_branch' not in self:
                problems += "missing project, "
                raise_error = True
            if year_month is None and 'bunsen_testlogs_branch' not in self:
                problems += "missing year_month, "
                raise_error = True

            if 'bunsen_testruns_branch' not in self:
                # XXX testruns branches don't use extra_label
                self.bunsen_testruns_branch = '{}/testruns-{}' \
                    .format(project, year_month)

            if 'bunsen_version' not in self:
                self.bunsen_version = BUNSEN_REPO_VERSION

        if 'bunsen_version' not in self and not cleanup_metadata:
            problems += "no bunsen_version, "
        else:
            pass # XXX guaranteed to be populated by cleanup_metadata

        if 'bunsen_testruns_branch' not in self \
            and ('project' not in self or 'year_month' not in self):
            problems += "missing bunsen_testruns_branch (or equivalent project/year_month), "
        elif 'bunsen_testruns_branch' not in self:
            pass # XXX don't care as long as project/year_month are present
        elif ':' in self.bunsen_testruns_branch:
            problems += "malformed bunsen_testruns_branch (contains ':'), "
            if cleanup_metadata: raise_error = True
        elif branch_regex.fullmatch(self.bunsen_testruns_branch) is None:
            problems += "malformed bunsen_testruns_branch, "
            if cleanup_metadata: raise_error = True

        if 'bunsen_testlogs_branch' not in self and not cleanup_metadata:
            problems += "missing bunsen_testlogs_branch, "
        elif 'bunsen_testlogs_branch' not in self:
            pass # may be populated later on by Bunsen.commit()
        elif ':' in self.bunsen_testlogs_branch:
            problems += "malformed bunsen_testlogs_branch (contains ':'), "
            if cleanup_metadata: raise_error = True
        elif branch_regex.fullmatch(self.bunsen_testlogs_branch) is None:
            problems += "malformed bunsen_testlogs_branch, "
            if cleanup_metadata: raise_error = True

        if 'bunsen_commit_id' not in self and not cleanup_metadata:
            problems += "missing bunsen_commit_id, "
        elif 'bunsen_commit_id' not in self:
            pass # may be populated later on by Bunsen.commit()

        if 'related_testruns' not in self:
            pass # optional
        elif not isinstance(self.related_testruns, list):
            problems += "malformed related_testruns (must be a list), "
            if cleanup_metadata: raise_error = True
        else:
            for related_testrun in self.related_testruns:
                if not related_testrun_regex.fullmatch(related_testrun):
                    problems += "malformed related_testruns, "
                    break
            if cleanup_metadata: raise_error = True

        if 'testcases' not in self:
            pass # optional
        elif not isinstance(self.testcases, list):
            problems += "malformed testcases (must be a list), "
            if cleanup_metadata: raise_error = True
        elif validate_testcases:
            testcases_valid = True
            for testcase in self.testcases:
                testcases_valid = testcates_valid and testcase.validate()
            if not testcases_valid:
                problems += "problems in testcases, "

        # TODO: Could be more strict at checking types
        # (e.g. where str expected for regex match, hexsha for commit_id).

        # TODO: Provide a way to check other desirable metadata:
        # - testcases: subtest field for failing tests?
        # - required architecture fields (e.g. arch, osver)
        # - timestamp

        if problems.endswith(", "):
            problems = problems[:-2]
        valid = (problems == "")
        if not valid and 'problems' in self: # append to existing problems
            self.problems = self.problems + ", " + problems
        elif not valid:
            self.problems = problems
        elif 'problems' in self:
            valid = False # already existing problems
        if raise_error:
            # TODO: Should also provide the full testrun -- would need to catch the error.
            raise BunsenError("could not validate Testrun for inclusion: {}" \
                .format(self.problems))
        return valid

    def to_json(self, summary=False, pretty=False, as_dict=False,
                extra_fields={}):
        """Serialize Testrun data to a JSON string or dict.

        Args:
            summary (bool, optional): Exclude testcases to produce a summary
                Testcase for inclusion in an index file.
            pretty (bool or int, optional): Output the JSON as a properly
                indented string instead of as a compact string. Passing an
                int configures the indentation level (default 4).
            as_dict (bool, optional): Return a dict instead of a string.
            extra_fields (dict, optional): Dictionary of additional fields to
                include in the JSON. These will override any fields of the same
                name already present in the Testcase object.
        """
        serialized = {}
        fields = dict(self)
        fields.update(extra_fields)
        for field, value in fields.items():
            if isinstance(value, Cursor):
                # Serialize regardless of self.field_types.
                skip_commit_id = should_skip_commit_id(value, field)
                value = value.to_str(serialize=True,
                    skip_commit_id=skip_commit_id)
            elif field not in self.field_types:
                pass
            elif self.field_types[field] == 'testcases' \
                and summary:
                continue # omit testcases when writing only summary
            elif self.field_types[field] == 'testcases' \
                and isinstance(value, list):
                value = _serialize_testcases(value, parent_testrun=self)
            elif self.field_types[field] not in {'str','hexsha','cursor'}:
                raise BunsenError("BUG: unknown type '{}' for testrun field '{}'" \
                    .format(self.field_types[field], field))
            else:
                pass # no special processing needed
            serialized[field] = value
        # <TODO: Could use json.dump instead of json.dumps?>
        if as_dict:
            return serialized
        elif pretty:
            indent = pretty if isinstance(pretty,int) else 4
            return json.dumps(serialized, indent=indent)
        else:
            return json.dumps(serialized)

    # XXX: Protocol to support reading/writing arbitrary JSON fields as attrs:

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
            # XXX This is a builtin field. Assign None so later
            # __setattr__ calls don't add this field to the JSON dict!
            self.__dict__[field] = None

# TODOXXX REFACTOR BELOW & IN OTHER FILES
# TODO renamed Testcase._parent_testrun -> Testcase.parent_testrun
# TODO renamed {Testcase,Testrun}._field_types -> Testcase.field_types
# TODO renamed Testrun._bunsen -> Testrun.bunsen
# TODO renamed Testrun._testcase_fields -> Testrun.testcase_field_types
# <TODO: replace testcase dict with Testcase, throughout the analysis scripts>
