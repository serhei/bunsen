# Library for pretty-printing and HTML-formatting Bunsen test results.

uninteresting_fields = {'year_month',
                        'bunsen_testruns_branch',
                        'bunsen_testlogs_branch'}

def suppress_fields(testrun, suppress=set()):
    testrun = dict(testrun)
    for f in suppress:
        if f in testrun:
            del testrun[f]
    return testrun

class PrettyPrinter:
    def __init__(self, b, opts):
        self._bunsen = b
        self.opts = opts
        # TODO: set default opts.pretty, opts.verbose
        self.has_output = False

    def message(self, *args, **kwargs):
        args = list(args)
        kwargs2 = {}
        for k,v in kwargs.items():
            if k in {'sep','end','file','flush'}:
                # pass the argument on to print()
                kwargs2[k] = v
            if len(args) > 0: args.append(" ")
            args.append("{}={}".format(k,v))
        print(*args, **kwargs2)
        self.has_output = True

    def section(self):
        if self.has_output:
            print() # blank line
        self.has_output = False

    def show_testrun(self, testrun):
        self.has_output = True

        # header
        if self.opts.pretty:
            short_commit_id = testrun.bunsen_commit_id
            if len(short_commit_id) > 7:
                short_commit_id = short_commit_id[:7] + '...'
            print("* {} {} pass_count={} fail_count={}" \
                  .format(testrun.year_month, short_commit_id,
                          testrun.pass_count, testrun.fail_count))
        else: # not pretty
            print(testrun.to_json())
            return

        # details
        suppress = {'year_month', 'bunsen_commit_id', 'pass_count', 'fail_count'}
        if not self.opts.verbose:
            suppress = suppress.union(uninteresting_fields)
        testrun = suppress_fields(testrun, suppress)
        for k, v in testrun.items():
            print('  - {}: {}'.format(k, v))

    def finish(self):
        pass # no footer

# TODOXXX sanitize html in string values, use a proper templating kit
# Inspired by Martin Cermak's logproc script:
class HTMLFormatter:
    def __init__(self, b, opts):
        self._bunsen = b
        self.opts = opts

        self.has_output = False
        self.has_header = False
        self._header = None

        print("<html><head>")
        print("""<style type='text/css'>
.f { background-color: #cc5c53; }
.p { background-color: #ccff99; }
.h { writing-mode: tb-rl; width: 20px; font-size: xx-small; }
td,th {background-color: white; text-align: center; padding: 3px;}
div { font-size: xx-small; white-space:nowrap; text-align: left; display: none; }
</style>""")
        # TODO: Javascript not needed for all documents?
        # TODO: There may be a glitch where the wrong block is opened
        print("""<script type='text/javascript'>
function s(i) {
  document.getElementById(i).style.display='block';
}
function details(s) {
  var divs = document.getElementsByTagName('div');
  for (var i = 0; i < divs.length; i++) {
    divs[i].style.display=(s?'block':'none');
  }
}
</script>""")
        print("</head><body>")

    def _open_table(self, header):
        self.has_header = True
        self._header = header
        print("<table border=0 bgcolor=gray border=1 cellspacing=1 cellpadding=0>")
        row = "<tr>"
        for h in header:
            row += "<th class=h>{}</th>".format(h)
        row += "</tr>"
        print(row)

    def _close_table(self):
        print("</table>")
        self.has_header = False

    def message(self, *args, **kwargs):
        if self.has_header:
            self._close_table()

        s = "<p>"
        for arg in args:
            s += arg
        for k,v in kwargs.items():
            if k in {'sep','end','file','flush'}:
                # XXX ignore any print() arguments
                continue
            if len(s) > 0: s += " "
            s += "{}={}".format(k,v)
        s += "</p>"
        print(s)
        self.has_output = True

    def section(self):
        if self.has_header:
            self._close_table()
            pass # TODOXXX close table
        if self.has_output:
            print("<hr/>")
        self.has_output = False

    def show_testrun(self, testrun):
        testrun_header = ["year_month", "bunsen_commit_id", "pass_count", "fail_count"]
        if self.has_header and self._header != testrun_header:
            self._close_table()
        if not self.has_header:
            self._open_table(testrun_header)

        # TODOXXX details (click on short_commit_id)
        details = ""

        # header
        short_commit_id = testrun.bunsen_commit_id
        if len(short_commit_id) > 7:
            short_commit_id = short_commit_id[:7] + '...'
        # TODO: Additional fields (e.g. source commit).
        print("<tr><td>{}</td><td>{}{}</td><td>{}</td><td>{}</td></tr>" \
              .format(testrun.year_month, short_commit_id, details,
                      testrun.pass_count, testrun.fail_count))

    def finish(self):
        if self.has_header:
            self._close_table()
        print("</body></html>")

def get_formatter(b, opts):
    pretty = opts.pretty
    if pretty == 'html':
        return HTMLFormatter(b, opts)
    elif pretty == True or pretty == False:
        return PrettyPrinter(b, opts)
    else:
        return None # TODO: signal error
