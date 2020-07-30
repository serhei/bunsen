#!/usr/bin/env python3

import cgi
import cgitb
from bunsen import Bunsen, BunsenCommand

cgitb.enable(1)
# TODO: cgitb.enable(0,opts.cgi_logdir) -- may need to handle in BunsenOptions.

form = cgi.FieldStorage()
b, opts = Bunsen.from_cgi_query(form)
b.run_command()
