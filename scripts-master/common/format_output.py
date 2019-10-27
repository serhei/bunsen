# Library for pretty-printing and HTML-formatting Bunsen analysis results.
# Based on some HTML table generation code written in C++ by Martin Cermak.
# TODO: Support output to a file (opts.output_file).
# TODO: Add ASCII colors.

import html

uninteresting_fields = {'year_month',
                        'bunsen_testruns_branch',
                        'bunsen_testlogs_branch'}

def html_sanitize(obj):
    return html.escape(str(obj))

def suppress_fields(testrun, suppress=set()):
    testrun = dict(testrun)
    for f in suppress:
        if f in testrun:
            del testrun[f]
    return testrun

def field_summary(testrun, fields=None, separator=" ", sanitize=False):
    if fields is None:
        fields = testrun.keys()
    s = ""
    first = True
    for k in fields:
        if not first: s += separator
        v = html_sanitize(testrun[k]) if sanitize else testrun[k]
        s += "{}={}".format(k, v)
        first = False
    return s

def html_field_summary(testrun, fields=None, separator=" "):
    return field_summary(testrun, fields, separator, sanitize=True)

class PrettyPrinter:
    def __init__(self, b, opts):
        self._bunsen = b
        self.opts = opts
        if 'pretty' not in self.opts.__dict__:
            self.opts.pretty = True
        if 'verbose' not in self.opts.__dict__:
            self.opts.verbose = False

        self._section_has_output = False

    def section(self):
        if self._section_has_output:
            print() # blank line
        self._section_has_output = False

    def message(self, *args, **kwargs):
        args = list(args)
        kwargs2 = {}
        for k,v in kwargs.items():
            if k in {'sep','end','file','flush'}:
                # pass the argument on to print()
                kwargs2[k] = v
                continue
            if len(args) > 0: args.append(" ")
            args.append("{}={}".format(k,v))
        print(*args, **kwargs2)
        self._section_has_output = True

    def show_testrun(self, testrun, header_fields=[], show_all_details=True, **kwargs):
        self._section_has_output = True
        if not self.opts.pretty:
            print(testrun.to_json())
            return

        info = dict(testrun)
        info.update(kwargs)

        # header
        extra = field_summary(info, header_fields)
        if len(extra) > 0: extra = " " + extra
        # TODOXXX shorten_commit_id() utility function here and in scripts
        short_commit_id = info['bunsen_commit_id']
        if len(short_commit_id) > 7: short_commit_id = short_commit_id[:7] + '...'
        print("* {} {} pass_count={} fail_count={}{}" \
              .format(info['year_month'], short_commit_id,
                      info['pass_count'], info['fail_count'],
                      extra))

        # details
        if not show_all_details:
            return
        suppress = {'year_month', 'bunsen_commit_id', 'pass_count', 'fail_count'}
        suppress = suppress.union(header_fields)
        if not self.opts.verbose:
            suppress = suppress.union(uninteresting_fields)
        info = suppress_fields(info, suppress)
        for k, v in testrun.items():
            print("  - {}: {}".format(k,v))

    def show_testcase(self, testrun, tc, **kwargs):
        pass # TODOXXX

    def finish(self):
        pass # no buffering or footer

class HTMLTable:
    def __init__(self, formatter):
        self._formatter = formatter
        self.is_open = False

        self.header = set()
        self.order = [] # order of (subset of) fields in header

        self.rows = []
        self.row_details = []
        self._next_row = None
        self._next_row_details = None

    def open(self):
        self.is_open = True

    def close(self):
        if not self.is_open:
            return
        self._flush()
        self.is_open = False

    def match_header(self, info):
        for k in info:
            if k not in self.header:
                return False
        return True

    def add_columns(self, columns=[], order=[]):
        for field in order:
            if field not in self.header:
                self.header.add(field)
            if field not in self.order:
                self.order.append(field)
        for field in columns:
            if field not in self.header:
                self.header.add(field)

    def add_row(self, row, details=None, order=[]):
        if self._next_row is not None:
            self.rows.append(self._next_row)
            self.row_details.append(self._next_row_details)
        if not self.match_header(row):
            self.add_columns(row.keys(), order=order)
        self._next_row = row
        self._next_row_details = {}
        if details is not None and len(details) > 0:
            self._next_row_details['_ROW'] = details
        # XXX subsequent add_cell() calls modify _next_row

    def add_cell(self, field, cell, details=None):
        '''Add a cell (with optional details) to the previous row of the table.'''
        if self._next_row is None:
            self.add_row({})
        if field not in self.header:
            self.add_columns([field])
        self._next_row[field] = cell
        if details is not None and len(details) > 0:
            self._next_row_details[field] = details

    def _flush(self):
        if self._next_row is not None:
            self.rows.append(self._next_row)
            self.row_details.append(self._next_row_details)
        self._next_row = None
        self._next_row_details = None

        print("<table border=0 bgcolor=gray border=1 cellspacing=1 cellpadding=0>")
        # header
        header = list(self.order)
        for field in self.header:
            if field not in header:
                header.append(field)
        s = "<tr>"
        for field in header:
            s += "<th class=h>"
            s += str(field)
            s += "</th>"
        s += "</tr>"
        print(s)

        # contents
        for i in range(len(self.rows)):
            _this_row = self.rows[i]
            _this_row_details = self.row_details[i]

            s = "<tr>"
            for field in header:
                s += "<td>" # TODO: add onclick for row/cell details
                if field in _this_row: # if not, output an empty cell
                    s += _this_row[field]
                # TODO: add details div + id
                s += "</td>"
            s += "</tr>"
            print(s)

            if '_ROW' in _this_row_details:
                # TODO: add details tr + id with colspan
                pass

        print("</table>")

        # XXX clear rows but retain header
        self.rows = []
        self.row_details = []

class HTMLFormatter:
    def __init__(self, b, opts):
        self._bunsen = b
        self.opts = opts
        # XXX self.opts.pretty is not used
        if 'verbose' not in self.opts.__dict__:
            self.opts.verbose = False

        self.table = HTMLTable(self)
        self._section_has_output = False
        self.global_div_counter = 1 # XXX id's for details view

        self._header()

    def _header(self):
        print("<html><head>")
        print("""<style type='text/css'>
.f { background-color: #cc5c53; }
.p { background-color: #ccff99; }
.h { writing-mode: tb-rl; width: 20px; font-size: xx-small; }
td,th {background-color: white; text-align: center; padding: 3px;}
div { font-size: xx-small; white-space:nowrap; text-align: left; display: none; }
</style>""") # TODO: increase table width
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
</script>""") # TODO: also generalize to show/hide <tr> elements by id
        print("</head><body>")

    def _footer(self):
        # TODO: <hr/>
        # TODO: metadata 'Generated <DATE> by Bunsen v<VERSION>'
        # TODO: metadata 'Repo last updated <DATE>'
        # TODO: metadata 'with test results from <LOCATION>'
        print("</html>")

    def section(self):
        self.table.close()
        if self._section_has_output:
            print("<hr/>")
        self._section_has_output = False

    def message(self, *args, **kwargs):
        self.table.close()
        s = "<p>"
        args = list(args)
        for k,v in kwargs.items():
            if k in {'sep','end','file','flush'}:
                # ignore print() arguments
                continue
            if len(s) > len("<p>"): s += " "
            s += "<b>{}=</b>{}".format(html_sanitize(k),html_sanitize(v))
        s += "</p>"
        self._section_has_output = True

    def show_testrun(self, testrun, header_fields=[], show_all_details=False, **kwargs):
        # XXX show_all_details is ignored -- will always reveal details on click
        self.testrun_row(testrun, header_fields, **kwargs)

    def show_testcase(self, testrun, tc, **kwargs):
        self.testcase_row(testrun, tc, **kwargs)

    def finish(self):
        if self.table.is_open:
            self.table.close()
        self._footer()

    # HTML-only methods:

    def table_reset(self):
        self.table.close()
        self.table = HTMLTable(self)

    def table_row(self, row, details=None, order=[], merge_header=False):
        if not merge_header and not self.table.match_header(row):
            self.table_reset()
        if not self.table.is_open:
            self.table.open()
        self.table.add_row(row, details=details, order=order) # XXX adds any new columns
        self._section_has_output = True

    def table_cell(self, field, cell, details=None):
        '''Add a cell (with optional details) to the previous row of the table.'''
        assert self.table.is_open
        self.table.add_cell(field, cell, details)
        self._section_has_output = True

    def testrun_cell(self, field, testrun, **kwargs):
        info = dict(testrun)
        info.update(kwargs)

        fields = ['pass_count','fail_count']
        cell = html_field_summary(info, fields=fields, separator="<br/>")

        suppress = set(fields)
        if not self.opts.verbose:
            suppress = suppress.union(uninteresting_fields)
        info = suppress_fields(info, suppress)
        details = html_field_summary(info, separator="<br/>")

        self.table_cell(field, cell, details=details)

    def testrun_row(self, testrun, header_fields=[], **kwargs):
        info = dict(testrun)
        info.update(kwargs)

        # header
        row = dict()
        # TODOXXX shorten_commit_id() utility function here and in scripts
        short_commit_id = info['bunsen_commit_id']
        if len(short_commit_id) > 7: short_commit_id = short_commit_id[:7] + '...'
        row['year_month'] = html_sanitize(info['year_month'])
        row['bunsen_commit_id'] = html_sanitize(short_commit_id)
        row['pass_count'] = html_sanitize(info['pass_count'])
        row['fail_count'] = html_sanitize(info['fail_count'])
        order = ['year_month', 'bunsen_commit_id', 'pass_count', 'fail_count']
        for k in header_fields:
            if row in order: continue # avoid duplicates
            row[k] = html_sanitize(info[k])
            order.append(k)

        # details
        suppress = set(order)
        if not self.opts.verbose:
            suppress = suppress.union(uninteresting_fields)
        info = suppress_fields(info, suppress)
        details = html_field_summary(info, separator="<br/>")

        self.table_row(row, details=details, order=order, merge_header=True)

    def testcase_cell(self, testrun, tc, **kwargs):
        pass # TODOXXX via table_cell

    def testcase_row(self, testrun, tc, **kwargs):
        pass # TODOXXX via table_row

def get_formatter(b, opts):
    pretty = opts.pretty
    if pretty == 'html':
        return HTMLFormatter(b, opts)
    elif pretty == True or pretty == False:
        return PrettyPrinter(b, opts)
    else:
        return None # TODO: signal error
