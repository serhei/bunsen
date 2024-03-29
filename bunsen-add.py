#!/usr/bin/env python3
info='''Commit a single testlog tarball.

Test e.g. with
$ tar cvzf - test.sum test.log | ./bunsen-add.py project=myproject commit_module=systemtap.commit_logs manifest=test.sum,test.log tar=-'''
# XXX Configure in $BUNSEN_ROOT/.bunsen/config:
config_opts = [
    ('tar', None, '<tarball>',
     "Path to tarball, or '-' for standard input."),
    ('commit_module', None, '<module_name>',
     "Bunsen package containing a commit_logs() function."),
    ('project', 'unknown-project', '<tag>', # TODO: support a whitelist of multiple tags, first is default
     "Project under which submitted testlogs may be committed. Can be specified in the CGI form data."),
    ('manifest', None, # e.g. ['sysinfo','systemtap.dmesg*', 'systemtap.sum*', 'systemtap.log*'],
     '<globs>',
     "List of globs specifying logfile paths to accept. All other paths from the tarball are ignored."),
    ('allowed_fields', None, # e.g. ['package_nvr'],
     '<fields>',
     "List of additional fields that can be accepted via command line."),
]

import sys
import io
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

b = Bunsen(script_name='bunsen-add')
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=config_opts,
                          required_args=['tar', 'manifest', 'commit_module'],
                          check_required=False, # XXX required args can come from allow_unknown
                          allow_unknown=True) # XXX allow_unknown for allowed_fields
    opts.add_config("bunsen-upload") # TODO: Also handle [bunsen-upload "<tag>"]
    opts.check_required()
    if opts.should_print_help:
        opts.print_help()
        exit()
    opts.service_id = 'bunsen-upload'

    # TODOXXX Also allow standard options for _commit_logs.commit_logs()!
    opts.manifest = opts.get_list('manifest')
    opts.allowed_fields = opts.get_list('allowed_fields')
    opts.tag = opts.project # XXX alias for _commit_logs.commit_logs()

    sys.path += [str(path) for path in b.default_pythonpath]
    module_name = to_module_name(opts.commit_module)
    _commit_logs = importlib.import_module(module_name)
    if 'commit_logs' not in _commit_logs.__dict__:
        raise BunsenError("Module '{}' does not provide commit_logs() function" \
                          .format(module_name))

    # TODOXXX check that the same wd is being checked out
    wd = b.checkout_wd('master', # XXX branch does not matter
                       checkout_name='bunsen_upload')

    commit_id = None
    # XXX this is a no-op; allowed_fields should still be set for the commit_logs module
    #for field_name in opts.allowed_fields:
    #    if field_name in form:
    #        opts[field_name] = opts[field_name].value
    if opts.tar == '-':
        f = io.BytesIO(sys.stdin.buffer.read())
        tar = tarfile.open(fileobj=f)
    else:
        fh = open(opts.tar, 'rb')
        f = io.BytesIO(fh.read())
        fh.close()
        tar = tarfile.open(fileobj=f)
    log_tarfile(tar)
    commit_id = _commit_logs.commit_logs(b, wd, tar, tarfile=tar,
                                         opts=opts, push=True,
                                         tarballname=opts.tar)
    tar.close()

    print("failed" if commit_id is None else "ok {}".format(commit_id))
