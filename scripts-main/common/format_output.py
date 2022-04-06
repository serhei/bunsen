# Library for pretty-printing and HTML-formatting Bunsen analysis results.
# Based on some HTML table generation code written in C++ by Martin Cermak.
# TODO: Support output to a file (opts.output_file).
# TODO: Add ASCII/HTML colors.

import sys
import html
from bunsen import Testcase, Testrun

import urllib.parse

# TODO: def short_hexsha(commit): ...
# replace commit.hexsha[:7] -> short_hexsha(commit)

uninteresting_fields = {'year_month',
                        'bunsen_testruns_branch',
                        'bunsen_testlogs_branch',
                        '_cursor_commit_ids'}

def suppress_fields(testrun, suppress=set()):
    testrun = dict(testrun)
    for f in suppress:
        if f in testrun:
            del testrun[f]
    return testrun

def html_sanitize(obj):
    return html.escape(str(obj))

# TODOXXX: Rename to plain_field_summary, have field_summary() take opts and decide between regular/HTML output (instead of using 'decorate','sanitize').
def field_summary(testrun, fields=None, separator=" ", sanitize=False,
                  suppress_keys=False, decorate=False):
    if fields is None:
        fields = testrun.keys()
    s = ""
    first = True
    for k in fields:
        if not first: s += separator
        v = testrun[k]
        if sanitize:
            v = str(testrun[k]).strip()
        # XXX: 'comparisons' field contains nested HTML
        if sanitize and k != "comparisons":
            v = html_sanitize(v)
        if suppress_keys:
            s += "{}".format(v)
        elif decorate:
            # for prettier html:
            s += "<b>{}</b>: {}".format(k,v)
        else:
            s += "{}={}".format(k,v)
        first = False
    return s

# ASCII formatting code:

class PrettyPrinter:
    def __init__(self, b, opts):
        self._bunsen = b
        self.opts = opts
        if 'pretty' not in self.opts.__dict__:
            self.opts.pretty = True
        if 'verbose' not in self.opts.__dict__:
            self.opts.verbose = False

        self._section_has_output = False

    def section(self, minor=False):
        if self._section_has_output:
            print() # blank line
            if not minor:
                print("* * *\n") # separator
        self._section_has_output = False

    def sanitize(self, msg):
        return msg

    def message(self, *args, raw=True, compact=True, sanitize=True, **kwargs):
        # XXX raw,sanitize options ignored in ASCII formatter
        args = list(args)
        kwargs2 = {}
        for k,v in kwargs.items():
            if k in {'sep','end','file','flush'}:
                # pass the argument on to print()
                kwargs2[k] = v
                continue
            if compact:
                if len(args) > 0: args.append(" ")
                args.append("{}={}".format(k,v))
            else:
                if len(args) > 0: args.append("\n")
                args.append("{}: {}".format(k,v))
        print(*args, **kwargs2)
        self._section_has_output = True

    def show_testrun(self, testrun, header_fields=[],
                     show_all_details=True, **kwargs):
        self._section_has_output = True
        if not self.opts.pretty:
            print(testrun.to_json(extra_fields=kwargs))
            return

        info = dict(testrun)
        info['project'], info['year_month'], info['extra_label'] = testrun.commit_tag()
        info['project'] = testrun.get_project_name()
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
        suppress = suppress.union({'testcases'})
        suppress = suppress.union(header_fields)
        if not self.opts.verbose:
            suppress = suppress.union(uninteresting_fields)
        info = suppress_fields(info, suppress)
        for k, v in info.items():
            if isinstance(v,str):
              while v.endswith('\n'): v = v[:-1]
            print("  - {}: {}".format(k,v))

    def show_testcase(self, testrun, tc, header_fields=[],
                      show_all_details=True, **kwargs):
        self._section_has_output = True
        if testrun is None:
            testrun = Testrun()
        if isinstance(tc, dict):
            # TODO: temporarily disable this conversion and change the scripts to use Testcase class
            tc = Testcase(tc, parent_testrun=testrun)
        if not self.opts.pretty:
            print(tc.to_json(extra_fields=kwargs))
            return

        # TODO: group testcases by exp?
        # TODO: extend to 2or diffs
        info = tc.to_json(as_dict=True, extra_fields=kwargs)

        # header
        extra = field_summary(info, header_fields)
        if len(extra) > 0: extra = " " + extra
        tc_outcome = "null" if 'outcome' not in info else info['outcome']
        if tc_outcome is None: tc_outcome = "<none>"
        if 'baseline_outcome' in info:
            tc_baseline = info['baseline_outcome']
            if tc_baseline is None: tc_baseline = "<none>"
            tc_outcome = str(tc_baseline) + "=>" + str(tc_outcome)
        tc_name = "<unknown>" if 'name' not in info else str(info['name'])
        tc_subtest = "" if 'subtest' not in info else " " + str(info['subtest'].strip())
        print("* {} {}{}{}".format(tc_outcome, tc_name, tc_subtest, extra))

        # details
        if not show_all_details:
            return
        suppress = {'name', 'outcome', 'baseline_outcome', 'subtest'}
        suppress = suppress.union(header_fields)
        info = suppress_fields(info, suppress)
        for k, v in info.items():
            print("  - {}: {}".format(k,v))

    def finish(self):
        pass # no buffering or footer

# HTML formatting code:

def html_field_summary(testrun, fields=None, separator=" ", suppress_keys=False):
    return field_summary(testrun, fields, separator, sanitize=True, suppress_keys=suppress_keys, decorate=True)

def select_class(cat, val):
    # XXX HACK 'outcome' didn't quite give what we want
    if (cat == 'baseline' or cat == 'latest') and val == 'FAIL':
        return 'f'
    if cat == 'outcome' or cat == 'baseline' or cat == 'latest':
        # TODO: needs refinement for different outcomes
        if len(val) < 1:
            return 'n'
        if val.endswith('PASS') or val.endswith('XFAIL'):
            return 'p'
        elif val.endswith('FAIL') \
             and not val.startswith('FAIL') \
             and not val[1:].startswith('FAIL'):
            return 'f'
        elif val.endswith('FLAKE'):
            return 'f'
        else:
            return 'n'
    elif cat == 'pass_count':
        return 'p'
    elif cat == 'fail_count':
        return 'f'
    elif cat == 'bunsen_commit_id':
        return 'bcommit'
    elif cat == 'source_commit':
        return 'scommit'
    elif cat == 'subtest':
        return 'subtest'
    # XXX for grid_view, the field names are not really predictable but the values are:
    elif val.startswith('+'):
        return 'p'
    elif val.startswith('-'):
        return 'f'
    elif val == '?':
        return 'empty'
    # TODOXXX also support categories 'pass', 'fail', 'better' (green-orange), 'worse' (pink)
    return None

class HTMLTable:
    def __init__(self, formatter):
        self._formatter = formatter
        self.is_open = False

        self.header = set()
        self.header_href = {}
        self.header_tooltip = {}
        self.order = [] # order of (subset of) fields in header

        self.rows = []
        self.row_details = []
        self.row_categories = []
        self._next_row = None
        self._next_row_details = None
        self._next_row_categories = None # TODOXXX allow table_row, table_cell to mark cells with category

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
            self.row_categories.append(self._next_row_categories)
        if not self.match_header(row):
            self.add_columns(row.keys(), order=order)
        self._next_row = row
        self._next_row_details = {}
        self._next_row_categories = {}
        # TODOXXX doublecheck the following code
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
            self.row_categories.append(self._next_row_categories)
        self._next_row = None
        self._next_row_details = None
        self._next_row_categories = None

        print("<table border=0 bgcolor=gray border=1 cellspacing=1 cellpadding=0>")
        # header
        header = list(self.order)
        for field in self.header:
            if field not in header:
                header.append(field)
        s = "<tr>"
        for field in header:
            s += "<th class=h>"
            if field in self.header_href:
                s += "<a href=\"{}\">".format(self.header_href[field]) + str(field) + "</a>"
            else:
                s += str(field)
            if field in self.header_tooltip:
                s += "<span class=\"tooltip\">{}</span>".format(self.header_tooltip[field])
            s += "</th>"
        s += "</tr>"
        print(s)

        # contents
        for i in range(len(self.rows)):
            _this_row = self.rows[i]
            _this_row_details = self.row_details[i]
            _this_row_categories = self.row_categories[i]

            s = ""
            row_id = None
            if '_ROW' in _this_row_details:
                row_id = self._formatter.global_div_counter
                self._formatter.global_div_counter += 1
                s += "<tr class=clicky onclick='s({0})'>".format(row_id)
            else:
                s += "<tr>"
            for field in header:
                # TODO: add a way to specify td_class in caller
                val = "" if field not in _this_row else str(_this_row[field])
                cat = field if field not in _this_row_categories else _this_row_categories[field]
                td_class = select_class(cat, val)

                cell_id = None
                if field in _this_row_details:
                    cell_id = self._formatter.global_div_counter
                    self._formatter.global_div_counter += 1
                    td_class = "clicky" if td_class is None else \
                        "'clicky " + td_class + "'"
                    s += "<td class={1} onclick='s({0})'>".format(cell_id, td_class)
                elif td_class is not None:
                    s += "<td class={}>".format(td_class)
                elif field not in _this_row:
                    s += "<td class=empty>"
                else:
                    s += "<td>"
                if field in _this_row: # if not, output an empty cell
                    s += str(_this_row[field])
                else:
                    pass # TODOXXX need some padding for an empty cell
                if field in _this_row_details:
                    s += "<div id=d{0} class=detail>".format(cell_id)
                    s += str(_this_row_details[field])
                    s += "</div>"
                s += "</td>"
            s += "</tr>"

            if '_ROW' in _this_row_details:
                s += "<tr id={0} class=detail>".format(row_id)
                s += "<td colspan={}>".format(len(header))
                s += _this_row_details['_ROW']
                s += "</td>"
                s += "</tr>"

            print(s)

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
        self.finished = False

    def __del__(self):
        if not self.finished:
            self.finish()

    def _header(self):
        print("<html><head>")
        print("""<style type='text/css'>
.f { background-color: darksalmon; }
.p { background-color: #ccff99; }
.n { background-color: lavender; }
.bcommit { font-weight: bold; color: darkslateblue; }
.scommit { font-weight: bold; color: darkslategray; }
.subtest { width: 40%; white-space: pre-wrap; }
.h { writing-mode: tb-rl; width: 20px; font-size: xx-small; }
table { font-size: 1em; table-layout: auto; }
table.fixed { table-layout: fixed; width: 100%; }
td,th { background-color: white; text-align: left;
        padding: 3px; white-space: normal; overflow: hidden; }
td.empty { background-color: lightgray; color: lightgray; }
/* td.clicky { background-color: beige; } */
td.clicky:hover { background-color: azure; }
th.h > a { text-decoration: none; color: darkslategray; }
th.h > .tooltip { visibility: hidden; background-color: beige; writing-mode:horizontal-tb; font-size:small; color: #000; text-align: left; padding: 5px 5px; border-radius: 6px; position: absolute; z-index: 1; }
th.h:hover .tooltip { visibility: visible; }
tr.clicky:hover > td { background-color: beige; }
.detail { white-space: nowrap; text-align: left; display: none; }
tr.detail { font-size: medium; }
tr.detail td { white-space: pre-wrap; }
div.detail { font-size: small; }
</style>""")
# TODO: Slightly more tasteful colours than default for the links?
        print("""<script type='text/javascript'>
function s(i) {
  elt = document.getElementById('d'+i);
  disp = elt.tagName.toLowerCase() == 'tr' ? 'table-row' : 'block';
  elt.style.display = elt.style.display == disp ? 'none' : disp;
}
function details(s) {
  var divs = document.getElementsByClassName('detail');
  for (var i = 0; i < divs.length; i++) {
    disp = divs[i].tagName.toLowerCase() == 'tr' ? 'table-row' : 'block';
    divs[i].style.display = (s ? disp : 'none');
  }
}
</script>""")
        print("</head><body>")

    def _footer(self):
        # TODO: if self._section_has_output: <hr/>
        # TODO: metadata 'Generated <DATE> by Bunsen v<VERSION>'
        # TODO: metadata 'Repo last updated <DATE>'
        # TODO: metadata 'with test results from <LOCATION>'
        print("</html>")

    def section(self, minor=False):
        self.table.close()
        if not minor:
            self.table_reset()
        if self._section_has_output:
            print("<hr/>")
        self._section_has_output = False

    def sanitize(self, msg):
        return html_sanitize(msg)

    def message(self, *args, raw=False, compact=True, sanitize=True, **kwargs):
        self.table.close()
        s = ""
        prefix_len = 0
        if not raw:
            s += "<p>"
            prefix_len = len(s)
        for v in args:
            if len(s) > prefix_len: s += " " if compact else "<br/>"
            s += str(v)
        for k,v in kwargs.items():
            if k in {'sep','end','file','flush'}:
                # ignore print() arguments
                continue
            vv = v
            # XXX 'comparisons' field contains nested HTML
            if sanitize and k != "comparisons":
                vv = html_sanitize(vv)
            if compact:
                if len(s) > prefix_len: s += " "
                s += "<b>{}=</b>{}".format(html_sanitize(k),vv)
            else:
                if len(s) > prefix_len: s += "<br/>"
                s += "<b>{}</b>: {}".format(html_sanitize(k),vv)
        if not raw:
            s += "</p>"
        print(s)
        self._section_has_output = True

    def show_testrun(self, testrun, header_fields=[],
                     show_all_details=False, **kwargs):
        # XXX show_all_details is ignored -- will always reveal details on click
        self.testrun_row(testrun, header_fields, **kwargs)

    def show_testcase(self, testrun, tc, header_fields=[],
                      show_all_details=False, **kwargs):
        if testrun is None:
            testrun = Testrun()
        # XXX show_all_details is ignored -- will always reveal details on click
        self.testcase_row(testrun, tc, header_fields, **kwargs)

    def finish(self):
        if self.table.is_open:
            self.table.close()
        self._footer()
        self.finished = True

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

    # common between testrun_cell and testrun_row
    def testrun_details(self, testrun, info, list_logs=True):
        #details = html_field_summary(info, separator="<br/>")
        details = html_field_summary(info, separator="<br/>")

        if list_logs:
            # TODOXXX this is pretty slow when listing all commits :/ control with opts.list_logs instead of list_logs option?
            # TODO: add a method like b.testlog_names(testrun)?
            details += "<p>"; first = True
            try:
                commit = self._bunsen.git_repo.commit(testrun.bunsen_commit_id)
            except ValueError:
                details += "ERROR: commit {} not found in git repo".format(testrun.bunsen_commit_id)
                details += "</p>"
                return details
            for blob in commit.tree.blobs:
                if blob.name == '.gitignore': continue
                if not first: details += "<br>"
                first = False

                #details += blob.name
                # TODOXXX: Control with opts.linkify:
                _opts_linkify_url = "/bunsen-cgi.py" # link to same server /bunsen-cgi.py
                log_url = "{}?cmd=show_logs&testrun={}&key={}&exact_match=yes" \
                    .format(_opts_linkify_url, testrun.bunsen_commit_id,
                            urllib.parse.quote_plus(blob.name))
                details += "<a href=\"{}\">{}</a>" \
                    .format(log_url, blob.name)
                details += " (<a href=\"{}&pretty=no\"><b>raw</b></a>)" \
                    .format(log_url)

        # TODOXXX also link to analyses
        #details += "<p><b>details</b>: TODO"
        return details

    def testrun_cell(self, field, testrun, **kwargs):
        info = dict(testrun)
        info.update(kwargs)

        fields = ['pass_count','fail_count']
        #cell = html_field_summary(info, fields=fields, separator="<br/>")
        cell = "{}&nbsp;pass<br/>{}&nbsp;fail".format(info['pass_count'],info['fail_count']) # TODOXXX text-align right

        suppress = set(fields)
        if not self.opts.verbose:
            suppress = suppress.union(uninteresting_fields)
        info = suppress_fields(info, suppress)
        details = self.testrun_details(testrun, info)

        self.table_cell(field, cell, details=details)

    def testrun_row(self, testrun, header_fields=[], **kwargs):
        info = dict(testrun)
        info['project'], info['year_month'], info['extra_label'] = testrun.commit_tag()
        info.update(kwargs)

        # header
        row = dict()
        # TODOXXX shorten_commit_id() utility function here and in scripts
        short_commit_id = info['bunsen_commit_id']
        if len(short_commit_id) > 7: short_commit_id = short_commit_id[:7] + '...'
        row['year_month'] = html_sanitize(info['year_month'])
        row['project'] = testrun.get_project_name()
        row['bunsen_commit_id'] = html_sanitize(short_commit_id)
        row['pass_count'] = html_sanitize(info['pass_count'])
        row['fail_count'] = html_sanitize(info['fail_count'])
        order = ['year_month', 'project', 'bunsen_commit_id', 'pass_count', 'fail_count']
        for k in header_fields:
            if row in order: continue # avoid duplicates
            row[k] = html_sanitize(info[k]) if k in info else ''
            order.append(k)

        # details
        suppress = set(order)
        suppress = suppress.union({'testcases'})
        if not self.opts.verbose:
            suppress = suppress.union(uninteresting_fields)
        info = suppress_fields(info, suppress)
        details = self.testrun_details(testrun, info)

        self.table_row(row, details=details, order=order, merge_header=True)

    def testcase_cell(self, testrun, tc, **kwargs):
        pass # TODOXXX via table_cell

    def testcase_row(self, testrun, tc, header_fields=[], **kwargs):
        if isinstance(tc, dict):
            # TODO: temporarily disable this conversion and change the scripts to use Testcase class
            tc = Testcase(tc, parent_testrun=testrun)
        info = tc.to_json(as_dict=True, extra_fields=kwargs)

        tc_outcome = "null" if 'outcome' not in info else info['outcome']
        if tc_outcome is None: tc_outcome = "<none>"
        if 'baseline_outcome' in info:
            tc_baseline = info['baseline_outcome']
            if tc_baseline is None: tc_baseline = "null"
            tc_outcome = str(tc_baseline) + "=>" + str(tc_outcome)
        tc_name = "<unknown>" if 'name' not in info else str(info['name'])
        tc_subtest = "" if 'subtest' not in info else str(info['subtest'].strip())

        # header
        row = dict()
        row['outcome'] = html_sanitize(tc_outcome)
        row['name'] = html_sanitize(tc_name)
        row['subtest'] = html_sanitize(tc_subtest)
        order = ['outcome', 'name', 'subtest']
        for k in header_fields:
            if row in order: continue # avoid duplicates
            row[k] = html_sanitize(info[k])
            order.append(k)

        # details
        suppress = set(order)
        suppress = suppress.union({'baseline_outcome'})
        info = suppress_fields(info, suppress)
        details = html_field_summary(info, separator="<br/>")

        # TODO: more details -- origin_log
        if False:
            if 'baseline_log' in info:
                contents = html_sanitize(tc['baseline_log'].contents(context=3))
                #print("DEBUG got contents\n", contents,file=sys.stderr)
                details += "<p>Baseline Log</p>"
                details += "<pre>" + contents + "</pre>"
            if 'origin_log' in info:
                contents = html_sanitize(tc['origin_log'].contents(context=3))
                #print("DEBUG got contents\n", contents,file=sys.stderr)
                details += "<p>Latest Log</p>"
                details += "<pre>" + contents + "</pre>"

        self.table_row(row, details=details, order=order, merge_header=True)

def get_formatter(b, opts):
    if 'pretty' not in opts.__dict__:
        return PrettyPrinter(b, opts)
    pretty = opts.pretty
    if pretty == 'html':
        return HTMLFormatter(b, opts)
    elif pretty == True or pretty == False:
        return PrettyPrinter(b, opts)
    else:
        return None # TODO: signal error
