"""Bunsen: a toolkit for storing and indexing test results.

Bunsen can collect, parse, and store a collection of log files
produced by a testsuite, storing them in a Git repo together
with a JSON index.

This module provides the core classes used to access a Bunsen repo
and work with the Bunsen data model.
"""

from .model import Index, Testlog, Cursor, Testcase, Testrun
from .repo import Workdir, Bunsen, BunsenOptions
from .utils import BunsenError
from .version import __version__
