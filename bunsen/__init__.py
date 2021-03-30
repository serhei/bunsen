"""Bunsen: a toolkit for storing and indexing test results.

Bunsen can collect, parse, and store a collection of log files
produced by a testsuite, storing them in a Git repo together
with a JSON index.

This module provides the core classes used to access a Bunsen repo
and work with the Bunsen data model.
"""

from .model import Index, Testlog, Cursor, Testcase, Testrun
from .repo import Workdir, Bunsen, BunsenOpts
# TODO: from .repo import Workdir, Bunsen, BunsenOptions
# TODO from .utils import BunsenError
from .version import __version__

# TODO: Temporary hack while moving declarations out of ../bunsen.py:
import sys, os
if os.path.basename(sys.argv[0]) != 'bunsen.py':
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    import importlib.util
    spec = importlib.util.spec_from_file_location("bunsen", os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bunsen.py')))
    b = importlib.util.module_from_spec(spec)
    sys.modules['b2'] = b
    spec.loader.exec_module(b)
    from b2 import Bunsen
