#!/usr/bin/env python3

import cgi
import cgitb
cgitb.enable() # TODO: configure logging

from bunsen import Bunsen

fail_reason = None

valid_cmds = {'list_commits', 'list_runs'}
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
    b.run_script('localhost', script_path, cmdline_args)
else:
    print("<h1>Error</h1>")
    print(fail_reason)

# TODO list_commits needs a regular 'git pull' in the git repo used to locate commits
