#! /usr/bin/env python3

import os
import sys
import time
import subprocess
import argparse
from tqdm import tqdm

# Requires Python 3.
assert sys.version_info[0] >= 3

# TODO: Currently refactoring below declarations into a separate 'bunsen' module:
from bunsen import *
from bunsen.utils import log_print, basedirname
# TODO: from bunsen import Bunsen, BunsenOptions

# Subcommand 'init'

def bunsen_init(b):
    found_existing = b.init_repo()
    if found_existing:
        log_print("Reinitialized existing Bunsen repository in", b.base_dir)
    else:
        log_print("Initialized empty Bunsen repository in", b.base_dir)

# TODO Subcommand 'add'

# TODO Subcommand 'list'

# TODO Subcommand 'show'

# TODO Subcommand 'delete'

# TODO Subcommand 'rebuild'

# TODO Subcommand 'gc'

# Subcommand 'checkout'

def bunsen_checkout_wd(b, branch_name=None, checkout_path=None):
    if branch_name is None:
        # XXX Branch (should have been) specified from environment.
        if b.default_branch_name is None:
            raise BunsenError('no branch name specified for checkout (check BUNSEN_BRANCH environment variable)')
        branch_name = b.default_branch_name
    if checkout_path is None and b.default_work_dir is not None:
        # XXX Checkout path was specified from environment.
        checkout_path = b.default_work_dir

    if checkout_path is None:
        # Checkout in current directory:
        checkout_name = None
        checkout_dir = os.getcwd()
    elif os.path.isdir(checkout_path):
        # Checkout within checkout_path:
        checkout_name = None
        checkout_dir = checkout_path
        # TODO Handle the case where checkout_path is already a Bunsen checkout.
        # Requires checkout to mark .git to distinguish from other Git repos.
    else:
        # Checkout at checkout_path:
        checkout_name = os.path.basename(checkout_path)
        checkout_dir = os.path.dirname(checkout_path)
    wd = b.checkout_wd(branch_name, \
                       checkout_name=checkout_name, checkout_path=checkout_dir)
    # TODO Print one message if updating, another message if meant for human output (rather than a checkout call from a bash script).
    print(wd.working_tree_dir)

# Subcommand 'run'

def bunsen_run(b, hostname, scriptname, invocation_args):
    # <TODO>: Merge the following into Bunsen.run_command().
    #script_path = b.find_script(scriptname, preferred_host=hostname)
    script_path = b.find_script(scriptname) # XXX preferred_host no longer supported
    script_dirname = basedirname(script_path)
    if hostname is None:
        if script_dirname == "scripts-main":
            hostname = 'localhost'
        elif script_dirname == "scripts-host":
            # XXX For now the VM host is also the Bunsen server:
            #hostname = b.default_vm_host
            hostname = 'localhost'
        elif script_dirname == "scripts-guest":
            raise BunsenError("hostname not specified for guest script {}" \
                              .format(script_path))
        else:
            # If hostname is not specified, default to running locally:
            hostname = 'localhost'

    # Set up working directory:
    wd_path = None
    wd_branch_name = None
    # TODO Accept an option to specify already existing workdir + branch.
    # TODO May not need to set wd_path in some cases?
    if True:
        # Generate checkout name
        wd_name = scriptname
        if hostname != 'localhost':
            wd_name = hostname + "/" + wd_name
        wd_name = "wd-" + wd_name.replace('/','-')

        # XXX Option to generate checkout name with a random cookie:
        # random_letters = ''.join([random.choice(string.ascii_lowercase) \
        #                           for _ in range(3)] \
        #                          + [random.choice(string.digits) \
        #                             for _ in range(1)])
        # wd_name = wd_name + "-" + random_letters

        wd_path = os.path.join(b.base_dir, wd_name)
        wd_branch_name = 'index' # TODOXXX need to pick a reasonable branch

        print("Using branch {}, checkout name {}" \
              .format(wd_branch_name, wd_name), file=sys.stderr)

    # TODO Better formatting for invocation_args.
    print("Running", scriptname if hostname == 'localhost' \
                                else scriptname+"@"+hostname, \
          ("at " + wd_path + " from") if wd_path else "from",
          script_path, "with", invocation_args, file=sys.stderr)
    print("===", file=sys.stderr)
    b.run_script(hostname, script_path, invocation_args,
                 wd_path=wd_path, wd_branch_name=wd_branch_name,
                 wd_cookie='', # XXX empty cookie defaults to PID
                 script_name=scriptname)

# Subcommand 'gorilla' -- a parable about false negative errors

def detect_gorilla(number):
    gorilla_number = 44 # according to Science, the number 44
                        # indicates that a Gorilla is present in the
                        # project
    time.sleep(0.1) # according to Science, Gorilla detection takes a
                    # non-trivial amount of time
    return number == gorilla_number

def bunsen_gorilla():
    """very important functionality to detect Gorillas;
       cf https://youtu.be/SgdV4SGkD9E"""
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

# Command Line Interface

def sub_init(parser, args):
    b = Bunsen(repo=args.repo, script_name="init")
    bunsen_init(b)

def sub_checkout(parser, args):
    b = Bunsen(repo=args.repo, alternate_cookie=str(os.getppid()), script_name="checkout_wd")
    branch_name = args.branch
    bunsen_checkout_wd(b, branch_name)

def sub_run(parser, args):
    # Syntax: bunsen run host +script1 arg1 arg2 ... +script2 arg1 arg2 ... ...
    # TODO Also allow compact syntax of the form +script=arg
    # TODO Syntax will be reused -- split out to parse_invocations() routine.
    hostname = None # optional
    invocations = []
    invocation = None
    for arg in args.args:
        if len(arg) > 0 and arg[0] == '+':
            if invocation is not None:
                invocations.append(invocation)
            invocation = [arg[1:]]
        elif invocation is None and hostname is None:
            hostname = arg
        elif invocation is None:
            parser.error("Unexpected argument '{}'".format(arg))
        else:
            invocation.append(arg)
    if invocation is not None:
        invocations.append(invocation)

    if not invocations:
        parser.error("No invocations found " + \
                    "(hint: 'bunsen run +script' not 'bunsen run script').")
    b = Bunsen(repo=args.repo, script_name=None) # script_name set in child process
    for invocation in invocations:
        scriptname = invocation[0]
        invocation_args = invocation[1:]
        bunsen_run(b, hostname, scriptname, invocation_args)

def sub_gorilla(parser, args):
    bunsen_gorilla()

def sub_run_or_help(parser, args):
    if len(args.args) > 0 and \
       len(args.args[0]) > 0 and args.args[0][0] == '+':
        sub_run(parser, args)
    else:
        sub_help(parser, args)

def sub_help(parser, args):
    # TODO: Add option for 'help subcommand'.
    if 'arg' in args and \
       args.arg is not None and \
       len(args.arg) > 0 and args.arg[0] == '+':
        args.args = [args.arg, '--help']
        sub_run(parser, args)
    else:
        parser.print_help()

if __name__=="__main__":
    common_parser = argparse.ArgumentParser()
    common_parser.add_argument('--repo', \
        help="path to bunsen git repo (XXX defaults to $BUNSEN_DIR/bunsen.git or .bunsen/bunsen.git in the same directory as bunsen.py)") # TODO PR25074 pick a more general way of finding bunsen.git
    # TODO Add another option for bunsen_dir

    parser = argparse.ArgumentParser(parents=[common_parser], add_help=False)
    subparsers = parser.add_subparsers(dest='cmd', metavar='<command>')

    supported_commands = ['init', 'checkout', 'run', 'gorilla', 'help']

    parser_init = subparsers.add_parser('init', \
        help='create directory for bunsen data')
    parser_init.set_defaults(func=sub_init)

    parser_checkout_wd = subparsers.add_parser('checkout', \
        help='check out a bunsen working directory')
    parser_checkout_wd.add_argument('branch', nargs='?', \
        metavar='<branch>', help='name of branch to check out', default='index')
    parser_checkout_wd.set_defaults(func=sub_checkout)

    parser_run = subparsers.add_parser('run', \
        help='run a script with bunsen env')
    parser_run.add_argument('args', nargs=argparse.REMAINDER, \
        metavar='<args>', help='+name and arguments for analysis script')
    parser_run.set_defaults(func=sub_run)

    # XXX This was a sanity test for tqdm that got way out of hand.
    # parser_gorilla = subparsers.add_parser('gorilla', \
    #     help='detect gorilla')
    # parser_gorilla.set_defaults(func=sub_gorilla)
    if len(sys.argv) > 1 and sys.argv[1] == 'gorilla':
        sub_gorilla(None, sys.argv[1:])
        exit(0)

    parser_help = subparsers.add_parser('help', \
        help='show the help message for a script')
    parser_help.add_argument('arg', nargs='?', default=None, \
        metavar='<script>', help='+name of analysis script to get help on')
    parser_help.set_defaults(func=sub_help)

    parser.set_defaults(func=sub_help)

    # XXX Handle $ bunsen +command similarly to $ bunsen run +command
    # TODO: Document bunsen +command shorthand in command line help.
    basic_parser = \
        argparse.ArgumentParser(parents=[common_parser], add_help=False)
    basic_parser.add_argument('args', nargs=argparse.REMAINDER)
    basic_parser.set_defaults(func=sub_run_or_help)

    # XXX Trickery to make sure extra_args end up in the right place.
    if len(sys.argv) > 1 and sys.argv[1] not in supported_commands \
        and sys.argv[1].startswith('+'):
        # TODO: Instead, catch the exception thrown by parser.parse_args()?
        # TODO: Need to print help for the parent parser, not the child parser.
        args = basic_parser.parse_args()
        args.func(basic_parser, args)
    else:
        args = parser.parse_args()
        args.func(parser, args) # XXX pass subparser instead?
