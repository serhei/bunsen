#!/usr/bin/env python3

import cgi
import cgitb
cgitb.enable() # TODO: configure logging

from bunsen import Bunsen

b = Bunsen()
# TODO integrate bunsen_run() with Bunsen class
# TODO this becomes the dashboard:
script_path = b.find_script('list_commits')
# TODO need a regular 'git pull' in the git repo used to locate commits
#print('Content-Type: text/html\n')
b.run_script('localhost', script_path, ['pretty=html'])
#cgi.test()
