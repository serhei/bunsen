#! /usr/bin/env python3
# Bunsen command line interface
# Copyright (C) 2019-2020 Red Hat Inc.
#
# This file is part of Bunsen, and is free software. You can
# redistribute it and/or modify it under the terms of the GNU Lesser General
# Public License (LGPL); either version 3, or (at your option) any
# later version.

import sys
import time
from tqdm import tqdm

# Requires Python 3.
assert sys.version_info[0] >= 3

from bunsen import Bunsen, BunsenOptions
from bunsen.utils import err_print

# TODO ensure that
# bunsen.py -- current file, installed into $PATH
# bunsen/__init__.py -- bunsen module, visible in library path
# bunsen/utils.py -> other utilities e.g. err_print()

# Subcommand 'init'

def cmd_init(args):
    b, opts = Bunsen.from_cmdline(args, script_name='init')
    found_existing = b.init_repo()
    if found_existing:
        print("Reinitialized existing Bunsen repo at {}".format(b.base_dir))
    else:
        print("Initialized Bunsen repo at {}".format(b.base_dir))

# Subcommand 'add'

def cmd_add(args):
    b, opts = Bunsen.from_cmdline(args, script_name=None)
    pass # TODOXXX

# Subcommand 'run'

def cmd_run(args):
    b, opts = Bunsen.from_cmdline(args, script_name=None)
    b.run_command()

# Subcommand 'gorilla' -- a parable about false negative errors

def detect_gorilla(number):
    gorilla_number = 44 # according to Science, the number 44
                        # indicates that a Gorilla is present in the
                        # project
    time.sleep(0.1) # according to Science, Gorilla detection takes a
                    # non-trivial amount of time
    return number == gorilla_number

def cmd_gorilla(args):
    # Very important functionality to detect Gorillas;
    # cf https://youtu.be/SgdV4SGkD9E

    # TODOXXX
    opts = BunsenOptions(bunsen=None)
    opts.required_groups = set() # TODOXXX remove 'bunsen', add 'output' opts
    opts.parse_cmdline(args) # TODOXXX no unknown options
    # TODOXXX for opts: adjust how the output is printed depending on console

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

# Subcommand 'help'

def cmd_help(args=[]):
    if len(args) > 1:
        first_arg = args[0]
        if first_arg not in supported_commands:
            err_print("unknown subcommand '{}'".format(first_arg))
            cmd_help() # show help for all commands
            return
        cmd = supported_commands[first_arg]
        return
    # TODO: bunsen help -> prints general help
    # TODO: bunsen help subcommand -> equivalent to bunsen subcommand --help
    print("TODO: print help")
    pass

supported_commands = {
    'init':cmd_init,
    'add':cmd_add,
    # TODO: 'ls':cmd_ls,
    # TODO: 'show':cmd_show,
    # TODO: 'diff':cmd_diff,
    'run':cmd_run,
    # TODO: 'checkout':cmd_checkout,
    # TODO: 'rm':cmd_rm,
    # TODO: 'gc':cmd_gc,
    # TODO: 'rebuild':cmd_rebuild,
    # TODO: 'clone':cmd_clone,
    # TODO: 'pull':cmd_pull,
    # TODO: 'push':cmd_push,
    # TODO: 'restore':cmd_restore,
    # TODO: 'backup':cmd_backup,
    # TODO: 'rotate':cmd_rotate,
    'gorilla':cmd_gorilla,
    'help':cmd_help,
}

if __name__=="__main__":
    # Choose command depending on the first non-option argument:
    args = sys.argv[1:]
    first_arg, ix = None, 0
    for arg in args:
        if not arg.startswith('-'):
            first_arg = arg; break
        ix += 1
    if len(args) == 0:
        cmd_help(args)
    elif first_arg is None:
        cmd_run(args)
    elif first_arg.startswith('+'):
        cmd_run(args)
    elif first_arg in supported_commands:
        # Remove first_arg from the command line:
        cmd = supported_commands[first_arg]
        cmd_args = args[:ix] + args[ix+1:]
        cmd(cmd_args)
    else:
        err_print("unknown subcommand '{}'".format(first_arg))
        cmd_help()
