"""Bunsen: a toolkit for storing and indexing test results.

Bunsen can collect, parse, and store a collection of log files
produced by a testsuite, storing them in a Git repo together
with a JSON index.

This module provides the core classes used to access a Bunsen repo
and work with the Bunsen data model.
"""

from .model import Testlog, Cursor, Testcase, Testrun
from .index import Index
from .repo import Workdir, Bunsen # TODO Bunsen -> Repo, Bunsen
from .config import BunsenOptions # TODO BunsenOptions -> Config
from .utils import BunsenError
from .version import __version__
