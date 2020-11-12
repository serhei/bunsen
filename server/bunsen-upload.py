#!/usr/bin/env python3
info='''CGI script to receive uploaded testlog tarballs, initial version for trusted environments.

TODOXXX Test e.g. with
$ tar xvzf - test.sum test.log | curl -X POST -F "project=example-project" -F "tar=@-" http://bunsen.target:8013/bunsen-upload.py'''
# XXX Configure in $BUNSEN_ROOT/.bunsen/config:
config_opts = [
    ('commit_module', None, '<module_name>',
     "Bunsen package containing a commit_logs() function."),
    ('project', 'unknown-project', '<tag>', # TODO: support a whitelist of multiple tags, first is default
     "Project under which submitted testlogs may be committed. Can be specified in the CGI form data."),
    ('manifest', None, # e.g. ['sysinfo','systemtap.dmesg*', 'systemtap.sum*', 'systemtap.log*'],
     '<globs>',
     "List of globs specifying logfile paths to accept. All other paths from the tarball are ignored."),
]

import cgi
import cgitb

import sys
import importlib
import tarfile

from bunsen import Bunsen

def log_tarfile(tar, outfile=None):
    if outfile is None:
        outfile = sys.stderr
    # TODO: Also log: what's in the manifest, what's rejected,
    # target project, result of adding the testlogs to the Bunsen repo....
    print("{} received a payload:".format(opts.service_id),
          file=outfile)
    for tarinfo in tar:
        kind = "unknown"
        if tarinfo.isreg():
            kind = "file"
        elif tarinfo.isdir():
            kind = "directory"
        print("* {} ({} bytes, {})".format(tarinfo.name, tarinfo.size, kind),
              file=outfile)
    outfile.flush()

def to_module_name(commit_module):
    # TODO: Munge commit_module name? e.g. +gdb/commit-logs -> gdb.commit_logs
    # Strip starting '+', replace '.' -> '/', '-' -> '_'.
    return commit_module

b = Bunsen()
if b.script_name is None or b.script_name == "<unknown>":
    b.script_name = 'bunsen-upload'
if __name__=='__main__':
    cgitb.enable() # TODO: configure logging

    # XXX Read args from config only.
    opts = b.cmdline_args([], info=info, args=config_opts,
                          required_args=['manifest', 'commit_module'], use_config=True)
    opts.add_config("bunsen-upload") # TODO: Also handle [bunsen-upload "<tag>"]
    opts.service_id = 'bunsen-upload'

    # TODOXXX Also allow standard options for _commit_logs.commit_logs()!
    opts.manifest = opts.get_list('manifest')
    opts.tag = opts.project # XXX alias for _commit_logs.commit_logs() TODOXXX???

    sys.path += b.default_pythonpath
    module_name = to_module_name(opts.commit_module)
    _commit_logs = importlib.import_module(module_name)
    if 'commit_logs' not in _commit_logs.__dict__:
        raise BunsenError("Module '{}' does not provide commit_logs() function" \
                          .format(module_name))

    # TODOXXX check that the same wd is being checked out
    wd = b.checkout_wd('master', # XXX branch does not matter
                       checkout_name='bunsen_upload')

    form = cgi.FieldStorage()

    print("Content-Type: text/plain")
    print("")

    commit_id = None
    if 'project' in form:
        # TODOXXX check against whitelist
        # TODOXXX this should be set before calling cmdline_args()
        opts.project = form['project'].value
        opts.tag = opts.project # TODOXXX alias for _commit_logs.commit_logs()
    if 'tar' in form and form['tar'].file is not None:
        tar = tarfile.open(fileobj=form['tar'].file)
        # TODO: change outfile to go somewhere other than 'breakage.log'
        log_tarfile(tar)
        # TODOXXX suppress the 'Could not push branch' messages
        # probably by not pushing unmodified branches?
        commit_id = _commit_logs.commit_logs(b, wd, tar, tarfile=tar,
                                             opts=opts, push=True)
    tar.close()

    print("failed" if commit_id is None else "ok {}".format(commit_id))
    print() # XXX extra newline to separate logs cleanly
