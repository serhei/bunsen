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

from bunsen.utils import *
from bunsen.version import __version__

#########################
# schema for JSON index #
#########################

BUNSEN_REPO_VERSION = __version__
"""Current version of the Bunsen repo schema."""
# TODO: use BUNSEN_REPO_VERSION in the model and repo format

branch_regex = re.compile(r"(?P<project>.*)/test(?P<kind>runs|logs)-(?P<year_month>\d{4}-\d{2})(?:-(?P<extra>.*))?")
"""Format for testruns and testlogs branch names."""

commitmsg_regex = re.compile(r"(?P<project>.*)/test(?P<kind>runs|logs)-(?P<year_month>\d{4}-\d{2})(?:-(?P<extra>[^:]*))?:(?P<note>.*)")
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
            commit = self._bunsen.git_repo.commit('index')
            for blob in commit.tree:
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
            input_stream: A seekable stream for an external log file.
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
        return Testlog(bunsen=testlog.bunsen, path=path,
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
            testlog = Testlog(bunsen=testlog.bunsen, path=path,
                commit_id=testlog.commit_id, blob=testlog.blob,
                input_path=input_path) # XXX reset input_path
        if input_stream is not None:
             testlog = Testlog(bunsen=testlog.bunsen, path=path,
                commit_id=testlog.commit_id, blob=testlog.blob,
                input_stream=input_stream) # XXX reset input_stream
        return testlog

    @property
    def external(self):
        """Is this Testlog external to the bunsen repo?"""
        return self._bunsen is None

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
            # TODOXXX OLD VERSION, need to update Bunsen class
            self._project, self._year_month = self._bunsen.commit_tag(self.commit_id)
            # TODOXXX NEW VERSION
            # self._project, self._year_month, self._extra_label = \
            #     self._bunsen.commit_tag(self.commit_id)

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
            return read_decode_lines(self._input_stream, must_decode=False)
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
        """Returns text at specified (one-indexed) line number in log file."""
        if line_no < 1:
            raise BunsenError("out of range line number {} for Testlog {}" \
                .format(line_no, self.path))
        if self._lines is None:
            self._lines = self._data_stream_readlines()
            # XXX: Could clear _lines when too many Testlog objects are
            # in memory, but I have not observed this problem in practice.
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
        a single-line cursor covering start.

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

        # # XXX parsing from_str can be delayed
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
            start, end = 1, 1
        elif start is not None and end is None:
            end = len(self.testlog)
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

        testlog = source.testlog(path, commit_id, parse_commit_id=False, input_stream=input_stream)
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
        repr += self.testlog.path if self.testlog.path else \
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
    fields = parent_testrun._testcase_fields
    deserialized_testcases = []
    for testcase in testcases:
        deserialized_testcase = \
            Testcase(from_json=testcase, parent_testrun=parent_testrun)
        deserialized_testcases.append(deserialized_testcase)
    return deserialized_testcases

# Testrun/Testcase fields that should not be added to JSON:
_testrun_base_fields = {'_bunsen', # Testrun only
                        '_parent_testrun', # Testcase only
                        '_field_types',
                        '_testcase_fields', # Testrun only
                        'summary'} # Testrun only
class Testcase(dict):
    def __init__(self, from_json=None, fields=None, parent_testrun=None):
        '''
        Create empty Testcase or parse Testcase data from JSON string or dict.
        '''

        # XXX Populate fields so __setattr__ doesn't add them to JSON dict.
        for field in _testrun_base_fields:
            if field not in self.__dict__:
                self.__dict__[field] = None

        # TODO: Provide sane defaults for when parent_testrun is None?
        assert parent_testrun is not None

        self._parent_testrun = parent_testrun
        if fields is None and self._parent_testrun is not None:
            fields = self._parent_testrun._testcase_fields
        elif fields is None:
            fields = {}

        # Populate self._field_types from fields, testcase_field_types:
        self._field_types = dict(testcase_field_types)
        for field, field_type in fields.items():
            assert field_type in valid_field_types
            self._field_types[field] = field_type

        if from_json is not None:
            json_data = from_json # XXX handle from_json(dict)
            if isinstance(from_json, str) or isinstance(from_json, bytes):
                json_data = json.loads(from_json)
            assert isinstance(json_data, dict)

            for field, value in json_data.items():
                if field not in self._field_types:
                    pass
                elif self._field_types[field] == 'testcases':
                    value = _deserialize_testcases(value, self._parent_testrun)
                elif self._field_types[field] == 'metadata':
                    # XXX Nested Testcase metadata contains Testcase fields,
                    # but we don't consider it as a Testcase object:
                    value = dict(Testcase(from_json=value, fields=fields,
                                          parent_testrun=self._parent_testrun))
                elif self._field_types[field] == 'cursor' \
                     and not isinstance(value, Cursor):
                    value = Cursor(source=self._parent_testrun._bunsen,
                                   commit_id=self._parent_testrun.bunsen_commit_id,
                                   from_str=value)
                else:
                    pass # TODOXXX skip this assertion elsewhere
                    #assert self._field_types[field] == 'str' \
                    #    or self._field_types[field] == 'hexsha' \
                    #    or self._field_types[field] == 'cursor'
                self[field] = value

    # TODOXXX def validate(self):

    # XXX @property does not seem to work with the 'map' protocol
    def outcome_line(self):
        if 'origin_sum' not in self:
            return None
        cur = self.origin_sum
        assert isinstance(cur, Cursor)
        cur = Cursor(source=cur.testlog,
                     start=cur.line_end, end=cur.line_end)
        return cur.line

    def to_json(self, pretty=False, as_dict=False,
                extra_fields={}):
        '''
        Serialize Testcase data to a JSON string or dict.
        '''
        serialized_testcase = {}
        fields = dict(self)
        fields.update(extra_fields)
        for field, value in fields.items():
            if isinstance(value, Cursor):
                # XXX serialize regardless of self._field_types
                # TODO: Remove bunsen_commit_id prefix.
                value = value.to_str(serialize=True)
            elif field not in self._field_types:
                pass
            elif self._field_types[field] == 'testcases':
                value = _serialize_testcases(value, self._parent_testrun)
            else:
                assert self._field_types[field] == 'str' \
                    or self._field_types[field] == 'hexsha' \
                    or self._field_types[field] == 'cursor' # XXX can be given as str
            serialized_testcase[field] = value
        # XXX: Could use json.dump instead of json.dumps?
        if as_dict:
            return serialized_testcase
        elif pretty:
            return json.dumps(serialized_testcase, indent=4)
        else:
            return json.dumps(serialized_testcase)

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
            # XXX This is a builtin field. Assign None so later
            # __setattr__ calls don't add this field to the JSON dict!
            self.__dict__[field] = None

    pass # TODOXXX replace testcase dict with Testcase, throughout the scripts

class Testrun(dict):
    def __init__(self, bunsen=None, from_json=None,
                 fields={}, testcase_fields={},
                 summary=False):
        '''
        Create empty Testrun or parse Testrun data from JSON string or dict.
        '''

        # XXX Populate fields so __setattr__ doesn't add them to JSON dict.
        for field in _testrun_base_fields:
            if field not in self.__dict__:
                self.__dict__[field] = None

        self._bunsen = bunsen

        # Populate self._field_types from fields, testrun_field_types:
        self._field_types = dict(testrun_field_types)
        for field, field_type in fields.items():
            assert field_type in valid_field_types
            self._field_types[field] = field_type

        # XXX testcase_fields passed down to Testcase class:
        self._testcase_fields = testcase_fields

        self.summary = summary
        if not self.summary:
            self.testcases = []

        if from_json is not None:
            json_data = from_json # XXX handle from_json(dict)
            if isinstance(from_json, str) or isinstance(from_json, bytes):
                json_data = json.loads(from_json)
            assert isinstance(json_data, dict)
            defer_fields = [] # XXX Defer parsing until cursor_commit_ids are known.
            cursor_commit_ids = {} # XXX bunsen_commit_id -> {value}
            for cursor_field, commit_field in cursor_commit_fields.items():
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
                    assert self._field_types[field] == 'str' \
                        or self._field_types[field] == 'hexsha'
                if summary and field == 'testcases':
                    continue # load only summary from JSON
                if field in cursor_commit_ids:
                    cursor_commit_ids[field] = value
                self[field] = value
            for field in defer_fields:
                self[field] = self._deserialize_testrun_field(field, self[field],
                                                              cursor_commit_ids)

            # XXX Set summary=False if JSON was missing testcases.
            self.summary = self.summary and 'testcases' in json_data

    def get_project_name(self):
        if 'bunsen_testruns_branch' in self:
            elts = self.bunsen_testruns_branch.split('/')
            return elts[0]
        return "unknown"

    # Return configuration properties of this Testrun as printable strings,
    # or "<unknown PROPERTY>" if unknown.  Returns a dictionary containing
    # keys for architecture, board, branch, version.

    def get_info_strings(self):
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

    def add_testcase(self, name, outcome, **kwargs):
        '''
        Append a testcase result to the Testrun data.
        '''
        testcase = Testcase({'name':name, 'outcome':outcome},
                            parent_testrun=self)
        for field, value in kwargs.items():
            testcase[field] = value
        if 'testcases' not in self:
            self.summary = False
            self.testcases = []
        self.testcases.append(testcase)
        return testcase

    # TODOXXX def validate(self):

    def _deserialize_testrun_field(self, field, value, cursor_commit_ids):
        if self._field_types[field] == 'testcases':
            value = _deserialize_testcases(value, parent_testrun=self)
        elif self._field_types[field] == 'metadata':
            # Nested Testrun metadata contains Testrun fields:
            value = self._deserialize_testrun_metadata(value, cursor_commit_ids)
        elif self._field_types[field] == 'cursor' \
             and not isinstance(value, Cursor):
            commit_id = None
            if field in cursor_commit_fields:
                commit_id_field = cursor_commit_fields[field]
                commit_id = cursor_commit_ids[commit_id_field]
            value = Cursor(source=self._bunsen,
                           commit_id=commit_id,
                           from_str=value)
        return value

    def _deserialize_testrun_metadata(self, metadata, cursor_commit_ids):
        '''
        Deserialize nested Testrun metadata. Don't gather cursor_commit_ids.
        '''
        deserialized_testrun = {}
        for field, value in metadata.items():
            value = self._deserialize_testrun_field(field, value,
                                                    cursor_commit_ids)
            deserialized_testrun[field] = value
        return deserialized_testrun

    def to_json(self, summary=False, pretty=False, as_dict=False,
                extra_fields={}):
        '''
        Serialize Testrun data to a JSON string or dict.
        '''
        serialized = {}
        fields = dict(self)
        fields.update(extra_fields)
        for field, value in fields.items():
            if isinstance(value, Cursor):
                # XXX serialize regardless of self._field_types
                # TODO: Remove bunsen_commit_id prefix.
                value = value.to_str(serialize=True)
            elif field not in self._field_types:
                pass
            elif self._field_types[field] == 'testcases' \
                 and summary:
                continue # write only summary to JSON
            elif self._field_types[field] == 'testcases' \
                 and isinstance(value, list):
                value = _serialize_testcases(value, parent_testrun=self)
            else:
                assert self._field_types[field] == 'str' \
                    or self._field_types[field] == 'hexsha' \
                    or self._field_types[field] == 'cursor' # XXX can be given as str
            serialized[field] = value
        # XXX: Could use json.dump instead of json.dumps?
        if as_dict:
            return serialized
        elif pretty:
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
            # XXX This is a builtin field. Assign None so later
            # __setattr__ calls don't add this field to the JSON dict!
            self.__dict__[field] = None