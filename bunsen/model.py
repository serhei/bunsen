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

import re
from pathlib import Path, PurePath
import git
import json

from bunsen.utils import *
from bunsen.version import __version__

#########################
# schema for JSON index #
#########################

branch_regex = re.compile(r"(?P<tag>.*)/test(?P<kind>runs|logs)-(?P<year_month>\d{4}-\d{2})(?:-(?P<extra>.*))?")
"""Format for testruns and testlogs branch names."""

commitmsg_regex = re.compile(r"(?P<tag>.*)/test(?P<kind>runs|logs)-(?P<year_month>\d{4}-\d{2})(?:-(?P<extra>[^:]*))?:(?P<note>.*)")
"""Format for commit message summaries created by Bunsen."""

indexfile_regex = re.compile(r"(?P<tag>.*)-(?P<year_month>\d{4}-\d{2}).json")
"""Format for indexfile paths in the 'index' branch."""

INDEX_SEPARATOR = '\n---\n'
"""YAML-style separator between JSON objects in the index."""

cursor_regex = re.compile(r"(?:(?P<commit_id>[0-9A-Fa-f]+):)?(?P<path>.*):(?P<start>\d+)(?:-(?P<end>\d+))?")
"""Serialized representation of a Cursor object."""

#####################################
# schema for testruns and testcases #
#####################################

# TODOXXX remove leading _

_valid_field_types = {
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

_testrun_field_types = {'testcases':'testcases'}
"""dict: Testrun fields requiring special serialization/deserialization logic.

Key is the name of the field, value is one of the valid_field_types.
"""

_testcase_field_types = {'origin_log':'cursor',
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

# TODOXXX rename to cursor_commit_fields
_cursor_commits = {'origin_log':'bunsen_commit_id',
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
    def __init__(self, bunsen, tag, key_function=None, reverse=False):
        self._bunsen = bunsen
        self.tag = tag
        self._key_function = key_function
        self._reverse = reverse

    def _indexfiles(self):
        commit = self._bunsen.git_repo.commit('index')
        for blob in commit.tree:
            m = indexfile_regex.fullmatch(blob.path)
            if m is not None and m.group('tag') == self.tag:
                yield (blob.path, commit.tree[blob.path])

    def _iter_basic(self):
        for path, blob in self._indexfiles():
            data = blob.data_stream.read().decode('utf-8')
            for json_str in data.split(INDEX_SEPARATOR):
                json_str = json_str.strip()
                if json_str == '':
                    # XXX extra trailing INDEX_SEPARATOR
                    continue
                yield Testrun(self._bunsen, from_json=json_str, summary=True)

    def __iter__(self):
        if self._key_function is None:
            for testrun in self._iter_basic():
                yield testrun
            #return self._iter_basic()
            return
        testruns = []
        for testrun in self._iter_basic():
            testruns.append(testrun)
        testruns.sort(key=self._key_function, reverse=self._reverse)
        for testrun in testruns:
            yield testrun

class Testlog:
    def __init__(self, bunsen, path=None, commit_id=None, blob=None, input_file=None):
        self._bunsen = bunsen
        self.path = path
        self.commit_id = commit_id
        self.blob = blob
        self._input_file = input_file
        self._input_file_cleanup = False
        self._has_input_file = input_file is not None

        # XXX Populate on demand:
        self._tag = None
        self._year_month = None
        self._lines = None

    def copy_to(self, dirpath):
        if self._has_input_file:
            # TODO: Would be better to produce a GitPython commit directly?
            # !!! TODOXXX Sanitize testlog_name to avoid '../../../dir' !!!
            # For now, just stick to the basename for safety. This
            # won't work for more complex testsuites whose results are
            # organized in subdirectories.
            target_name = os.path.basename(self.path)
            target_path = os.path.join(dirpath, target_name)
            f = open(target_path, 'wb')
            content = self._data_stream.read()
            # TODO: Handle isinstance(content,str)
            if isinstance(content, str):
                content = content.encode('utf-8')
            f.write(content)
            f.close()
        else:
            shutil.copy(self.path, dirpath)

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

    # Keep private to ensure open fds are cleaned up correctly:
    @property
    def _data_stream(self):
        if self._bunsen is None and self._input_file is None:
            self._input_file_cleanup = True
            self._input_file = open(self.path, 'r')
        if self._input_file is not None:
            self._input_file.seek(0)
            return self._input_file
        return self.blob.data_stream

    def __del__(self):
        if self._input_file_cleanup:
            self._input_file.close()

    def _data_stream_readlines(self):
        if self._bunsen is not None and self.path is not None \
           and self.commit_id is not None:
            # Avoid reading _lines multiple times in different Cursor objects.
            return self._bunsen._testlog_readlines(self.path, self.commit_id)
        try:
            data_stream = self._data_stream
            # TODOXXX Problem with GitPython blob.data_stream returning OStream.
            # print("DEBUG", data_stream) -> TextIOWrapper in commit_logs
            #if isinstance(data_stream, OStream): # TODOXXX
            #    return data_stream.read().decode('utf8').split('\n')
            return data_stream.readlines()
        except UnicodeDecodeError: # yes, it happens
            warn_print("UnicodeDecodeError in TestLog, path={}".format(self.path))
            return [""]

    def line(self, line_no):
        '''Return text at specified line number (1-indexed!).'''
        if self._lines is None:
            self._lines = self._data_stream_readlines()
            # XXX: Could clear _lines if too many TestLog objects are
            # in memory, but did not observe any problem in practice.
        try:
            line = self._lines[line_no-1]
            if isinstance(line, bytes): line = line.decode('utf8')
        except UnicodeDecodeError: # yes, it happens
            warn_print("UnicodeDecodeError in TestLog, path={}, line={}".format(self.path, line_no))
            return ""
        return line

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

        # XXX parsing from_str can be delayed
        self._delay_parse = None
        self._delay_commit_id = None
        self._delay_source = None
        self._delay_input_file = None

        # XXX fast_hack has __iter__ yield the same Cursor object repeatedly.
        # Makes the yielded cursor unsafe to store in a testrun since it will change.
        self._fast_hack = True

        # XXX Support the following __init__ calls:
        # - Cursor (source=path, optional start=int, optional end=int) -> testlog=None
        if isinstance(source, str):
            assert from_str is None and commit_id is None
            testlog = Testlog(None, source, input_file=input_file)
        # - Cursor (source=tarfile.ExFileObject, optional start=int, optional end=int) -> testlog=None
        elif isinstance(source, tarfile.ExFileObject):
            assert from_str is None and commit_id is None
            testlog = Testlog(None, input_file=source)
        # - Cursor (source=Testlog, optional start=int, optional end=int)
        elif isinstance(source, Testlog):
            assert from_str is None and commit_id is None
            testlog = source
            if input_file is not None:
                testlog._input_file = input_file
        # - Cursor (source=Bunsen, from_str=str, optional commit_id=hexsha)
        #   with from_str of the form '[<commit_id>:]<testlog_path>:<start>[-<end>]'
        elif isinstance(source, Bunsen):
            assert start == 1 and end == None

            # XXX Performance fix: delay parsing until a field of this
            # Cursor (testlog, line_start, line_end) is actually accessed:
            self._delay_parse = from_str
            self._delay_commit_id = commit_id
            self._delay_source = source
            testlog, start, end = None, None, None
            #self._delayed_parse() # XXX test effect of performance fix
        # - Cursor (start=Cursor, optional end=Cursor)
        else:
            if end is None: end = start
            assert isinstance(start, Cursor) and isinstance(end, Cursor)
            assert source is None and from_str is None and commit_id is None

            if not self.name:
                self.name = start.name
            if self.name != end.name:
                warn_print("combining cursors from different files: {} vs {}" \
                           .format(start.to_str(), end.to_str()))

            testlog = start.testlog
            if input_file is not None:
                testlog._input_file = input_file
            start = start.line_start
            end = end.line_end

        self._testlog = testlog
        self._line_start = start
        self._line_end = end
        if self._line_end is None and self._testlog is not None:
            self._line_end = len(self.testlog)

    def _delayed_parse(self):
        if self._delay_parse is None:
            return

        m = cursor_regex.fullmatch(self._delay_parse)
        assert m is not None

        commit_id = self._delay_commit_id
        if m.group('commit_id') is not None:
            #assert commit_id is None # XXX may not be true during parsing
            commit_id = m.group('commit_id')
        assert commit_id is not None

        path = m.group('path')
        assert path is not None

        source = self._delay_source
        testlog = source.testlog(path, commit_id, parse_commit_id=False)
        if self._delay_input_file is not None:
            testlog._input_file = self._delay_input_file
        start = int(m.group('start'))
        end = int(m.group('end')) if m.group('end') is not None else start

        self._testlog = testlog
        self._line_start = start
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

    def contents(self, context=0):
        con_start = max(self.line_start-context,1)
        con_end = min(self.line_end+context,len(self.testlog))
        s = ""
        snipped = False
        for i in range(con_start,con_end+1):
            if i > con_start+50 and i < con_end-49:
                if not snipped:
                    s += "... snipped {} lines ...\n".format(con_end-con_start-100)
                    snipped = True
                continue
            s += self.testlog.line(i) + "\n"
        return s

    def to_str(self, serialize=False):
        repr = ''
        if self.testlog.commit_id is not None:
            repr += self.testlog.commit_id + ':'
        repr += self.name if self.name else \
                self.testlog.path if self.testlog.path else '<unknown>'
        repr += ':' + str(self.line_start)
        if self.line_end is not None and self.line_end != self.line_start:
            repr += '-' + str(self.line_end)
        if serialize and not self.name and not self.testlog.path:
            warn_print("serializing an incomplete cursor {}" \
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

        # Populate self._field_types from fields, _testcase_field_types:
        self._field_types = dict(_testcase_field_types)
        for field, field_type in fields.items():
            assert field_type in _valid_field_types
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
                     start=cur.line_end, end=cur.line_end,
                     name=cur.name)
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

        # Populate self._field_types from fields, _testrun_field_types:
        self._field_types = dict(_testrun_field_types)
        for field, field_type in fields.items():
            assert field_type in _valid_field_types
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
            if field in _cursor_commits:
                commit_id_field = _cursor_commits[field]
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
