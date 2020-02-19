#! /usr/bin/env python3
# Bunsen uploader service, initial version for trusted environments.
info='''WIP Bunsen uploader service, initial version for trusted environments.'''
cmdline_args = [
    ('commit_module', None, '<module_name>',
     "Bunsen package containing a commit_logs() function."),
    ('port', 8012, '<num>', "TCP port to listen on."),
    ('project', 'unknown-project', '<tag>', # TODO: support multiple tags, first is default
     "Project under which to commit submitted testlogs."),
    ('manifest', ['sysinfo','systemtap.dmesg*', 'systemtap.sum*', 'systemtap.log*'], '<globs>', # TODO: Change default or make mandatory.
     "List of globs specifying logfile paths to accept. All other paths from the tarball are ignored."),
    ('service_id', 'unknown-project', '<name>',
     "Name of this bunsen-push service."),
    ('service_info_url', 'https://github.com/serhei/bunsen', '<url>', # TODO: change to sourceware website
     "URL to find more info about this bunsen-push service."),
]

import importlib
import sys
import web
from bunsen import Bunsen, BunsenError

# XXX for handling tar data off the internet, needs review:
import tarfile
import io

urls = ('/upload', 'upload',
        '/manifest', 'manifest',
        '/gorilla', 'gorilla')

# XXX must disable autoreload for app to see correct globals
app = web.application(urls, globals(), autoreload=False)

# XXX application globals
opts = None # XXX set by b.cmdline_args() below
_commit_logs = None # XXX configured from bunsen-push command line

# TODO: Disable web.py's config.debug stacktraces, or control with an option.

# XXX: Leave timestamping, log storage etc. to journald.
def _log_tarfile(tar):
    # TODOXXX: Also log: what's in the manifest, what's rejected,
    # target project, result of adding the testlogs to the Bunsen repo....
    print("{} received a payload:".format(opts.service_id),
          file=sys.stderr)
    for tarinfo in tar:
        kind = "unknown"
        if tarinfo.isreg():
            kind = "file"
        elif tarinfo.isdir():
            kind = "directory"
        print("* {} ({} bytes, {})".format(tarinfo.name, tarinfo.size, kind),
              file=sys.stderr)
    sys.stderr.flush()

class upload:
    def GET(self):
        return 'This is the Bunsen {} test results uploader.\n' \
            'Please POST a tarball of test results to this endpoint to add it into the Bunsen repo.\n' \
            'Please see {} for more information.' \
            .format(opts.service_id, opts.service_info_url)

    # XXX test e.g. tar cvzf - test.sum test.log | curl -X POST --data-binary '@-' http://bunsen.target:8012/upload
    def POST(self):
        # TODOXXX: Support upload?project=<tag> (check against a whitelist)
        dat = web.data()
        bio = io.BytesIO(dat)
        tar = tarfile.open(fileobj=bio)

        # TODOXXX: Check payload against opts.manifest.
        _log_tarfile(tar)
        _commit_logs.commit_logs(b, tar, opts=opts, push=True)
        # TODOXXX: Perhaps default commit_logs() to push=True?

        tar.close()
        bio.close()

        # TODOXXX: Return a more useful message based on outcome of commit_logs:
        return 'ok\n'

class manifest:
    def GET(self):
        s = ''
        for pat in opts.manifest:
            s += pat + '\n'
        return s

class gorilla:
    def GET(self):
        return 'It is scientifically certain that the Internet MAY or MAY NOT contain a Gorilla.\n' \
            'In general, the Internet is too vast and complicated to test properly.'

def to_module_name(commit_module):
    # TODO: Munge commit_module name? e.g. +gdb/commit-logs -> gdb.commit_logs
    # Strip starting '+', replace '.' -> '/', '-' -> '_'.
    return commit_module

b = Bunsen()
if b.script_name is None or b.script_name == "<unknown>":
    b.script_name = 'bunsen-push' # XXX not set when bunsen-push invoked directly
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['commit_module'],
                          optional_args=['port'])
    opts.add_config('bunsen-push') # TODOXXX: Also handle [bunsen-push "<tag>"]!
    # TODOXXX Also allow standard options for _commit_logs.commit_logs()!
    opts.manifest = opts.get_list('manifest')

    sys.path += b.default_pythonpath
    module_name = to_module_name(opts.commit_module)
    _commit_logs = importlib.import_module(module_name)
    print("Found commit_logs module", _commit_logs)
    if 'commit_logs' not in _commit_logs.__dict__:
        raise BunsenError("Module '{}' does not provide commit_logs() function" \
                          .format(module_name))

    # TODO: Isn't there a better way to specify the port for web.py?
    sys.argv = [sys.argv[0], str(opts.port)]
    app.run()
