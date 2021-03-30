# Bunsen internal utilities
# Copyright (C) 2019-2021 Red Hat Inc.
#
# This file is part of Bunsen, and is free software. You can
# redistribute it and/or modify it under the terms of the GNU Lesser General
# Public License (LGPL); either version 3, or (at your option) any
# later version.

import os
import sys

# TODOXXX headings

class BunsenError(Exception):
    def __init__(self, msg):
        self.msg = msg

# TODO: Control with bunsen verbose/non-verbose option.
def log_print(*args, **kwargs):
    # XXX For now, consider as part of the script output.
    print(*args, **kwargs)

def warn_print(*args, **kwargs):
    prefix = "WARNING:"
    if 'prefix' in kwargs:
        prefix = kwargs['prefix']
        del kwargs['prefix']
    print(prefix, file=sys.stderr, end=('' if prefix == '' else ' '))
    print(file=sys.stderr, *args, **kwargs)

# TODO: Control with bunsen debug option.
def dbug_print(*args, **kwargs):
    prefix = "DEBUG:"
    if 'prefix' in kwargs:
        prefix = kwargs['prefix']
        del kwargs['prefix']
    if False:
        print(prefix, file=sys.stderr, end=('' if prefix == '' else ' '))
        print(file=sys.stderr, *args, **kwargs)

# One level up from os.path.basename:
def basedirname(path):
    dir = os.path.dirname(path)
    return os.path.basename(dir)

