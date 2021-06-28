# Bunsen internal utilities
# Copyright (C) 2019-2021 Red Hat Inc.
#
# This file is part of Bunsen, and is free software. You can
# redistribute it and/or modify it under the terms of the GNU Lesser General
# Public License (LGPL); either version 3, or (at your option) any
# later version.

import os
import sys
from pathlib import Path, PurePath
import subprocess

# TODO progress uses tqdm when printing to console, suppresses output otherwise
# TODO add colorization for console printing
# TODO consider using a Python logging framework instead of this stuff (?)

class BunsenError(Exception):
    def __init__(self, msg):
        self.msg = msg

# TODO: Control with bunsen verbose/non-verbose option.
# TODO log_print prints to stdout when printing non-html, stderr otherwise
# TODOXXX log_print takes verbosity levels
def log_print(*args, **kwargs):
    """
    Print an ordinary log message to standard output.

    Supports the same arguments are 'print'.

    Args:
        prefix (optional): optional prefix for the message.
    """
    # XXX For now, consider as part of the script output.
    prefix = ""
    if 'prefix' in kwargs:
        prefix = kwargs['prefix']
        del kwargs['prefix']
    print(prefix, end=('' if prefix == '' else ' '))
    print(*args, **kwargs)

def err_print(*args, **kwargs):
    """
    Print an error message to standard error.

    Supports the same arguments as 'print'.

    Args:
        prefix (optional): custom prefix to use instead of 'bunsen ERROR:'.
    """
    prefix = "{} ERROR:".format(os.path.basename(sys.argv[0]))
    if 'prefix' in kwargs:
        prefix = kwargs['prefix']
        del kwargs['prefix']
    print(prefix, file=sys.stderr, end=('' if prefix == '' else ' '))
    print(file=sys.stderr, flush=True, *args, **kwargs)

def warn_print(*args, **kwargs):
    """
    Print a warning message to standard error.

    Supports the same arguments as 'print'.

    Args:
        prefix (optional): custom prefix to use instead of 'bunsen WARNING:'.
    """
    prefix = "{} WARNING:".format(os.path.basename(sys.argv[0]))
    if 'prefix' in kwargs:
        prefix = kwargs['prefix']
        del kwargs['prefix']
    print(prefix, file=sys.stderr, end=('' if prefix == '' else ' '))
    print(file=sys.stderr, flush=True, *args, **kwargs)

# TODOXXX edit to match other _print methods
# TODO: Control with bunsen debug option.
# TODO dbug_print prints to stderr, checks Bunsen settings -- or combine with log_print and set verbosity level?
def dbug_print(*args, **kwargs):
    """
    Print a debugging message to standard error.

    Supports the same arguments are 'print'.

    Args:
        prefix (optional): custom prefix to use instead of 'DEBUG:'.
    """
    prefix = "DEBUG:"
    if 'prefix' in kwargs:
        prefix = kwargs['prefix']
        del kwargs['prefix']
    if False:
        print(prefix, file=sys.stderr, end=('' if prefix == '' else ' '))
        print(file=sys.stderr, *args, **kwargs)

# TODO: Gradually replace this with Path methods.
def basedirname(path):
    """Return the directory path one level up from os.path.basename."""
    dir = os.path.dirname(path)
    return os.path.basename(dir)

def git_toplevel():
    """Return the path to the toplevel of the git repo in the current directory.

    Obtains the path reported by 'git rev-parse --show_toplevel'."""
    rc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                        capture_output=True)
    return str(rc.stdout.strip())

def sanitize_path(target_path, base_path):
    """Clean up target_path and ensure it is inside base_path
    or a subdirectory thereof. If that is not possible, raise an error."""
    target_path = Path(target_path)
    base_path = Path(base_path)
    try:
        target_path = target_path.relative_to(base_path)
        base_path = base_path.resolve()
    except ValueError: # XXX raised by relative_to()
        raise
    return base_path.join_path(target_path)

def read_decode(data_stream):
    """Read the content from a data_stream; decode UTF-8 if it yields bytes.

    Raises UnicodeDecodeError if the UTF-8 decoding fails.
    """
    data = data_stream.read()
    if isinstance(data, bytes):
        data = data.decode('utf-8') # raises UnicodeDecodeError
    assert(isinstance(data, str))
    return data

def readlines_decode(data_stream, must_decode=True):
    """Read and decode the content from a data_stream and split into lines.

    Raises UnicodeDecodeError if the UTF-8 decoding fails.

    Args:
        data_stream: Data stream to read from.
        must_decode (bool, optional): Guarantee that the returned lines have
            been decoded to a string. If False, in some cases a readlines()
            method may return a list of byte strings instead. This is useful
            for recovery from UnicodeDecodeError since a malformed line
            can be skipped without discarding the entirety of the file.
    """
    if hasattr(data_stream,'readlines') and \
        callable(getattr(data_stream,'readlines')):
        # Prefer readlines() since decoding arrors can be localized:
        lines = data_stream.readlines()
    else:
        return read_decode(data_stream).split('\n')
    if must_decode:
        for i in range(len(lines)):
            if isinstance(lines[i], bytes):
                lines[i] = lines[i].decode('utf-8') # raises UnicodeDecodeError
    return lines
