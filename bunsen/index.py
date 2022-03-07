# Bunsen repo index
# Copyright (C) 2019-2022 Red Hat Inc.
#
# This file is part of Bunsen, and is free software. You can
# redistribute it and/or modify it under the terms of the GNU Lesser General
# Public License (LGPL); either version 3, or (at your option) any
# later version.

from pathlib import Path, PurePath

import git.exc

from bunsen.model import *

# TODO: Index should represent an arbitrary subset of data in a repo, be JSON {de}serializable, etc.
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
            project (str): The project to iterate through.
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
            if data is None:
                continue
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
