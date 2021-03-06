#!/usr/bin/env python3

import cgi
import cgitb
cgitb.enable() # TODO: configure logging

import sys
from bunsen import Bunsen

fail_reason = None

# TODOXXX Need to secure 'find_regressions' to use cachefile in read-only fashion.
valid_cmds = {'overview', 'find_regressions', 'list_commits', 'list_runs', 'diff_commits', 'diff_runs', 'show_logs'}
def validate_cmd(script_name, args):
    global fail_reason
    if script_name not in valid_cmds:
        fail_reason = 'script {} not permitted, try one of {}'.format(script_name, valid_cmds)
        return False # TODO: reason
    # TODO: also validate args
    return True

b = Bunsen()
form = cgi.FieldStorage()
script_name, args = form['cmd'].value if 'cmd' in form else 'list_commits', {}
for field in form.keys():
    if field == 'cmd': continue
    args[field] = form[field].value
if 'pretty' not in args:
    # XXX override defaults
    args['pretty'] = 'html'
if validate_cmd(script_name, args):
    # TODO: integrate with bunsen_run and b.run_script:
    script_path = b.find_script(script_name)
    cmdline_args = []
    for k, v in args.items():
        cmdline_args.append('{}={}'.format(k,v))
    # TODOXXX fix out-of-order WARNING
    print("bunsen-cgi running:\n*", script_path, " ".join(cmdline_args), file=sys.stderr)
    b.run_script('localhost', script_path, cmdline_args)
    print(file=sys.stderr)
else:
    # TODOXXX also log to stderr
    print("<h1>Error</h1>")
    print(fail_reason)

# TODO list_commits needs a regular 'git pull' in the git repo used to locate commits
