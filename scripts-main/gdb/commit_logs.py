#!/usr/bin/env python3
info='''WIP -- Commit GDB test logs to a Bunsen git repo. Some assembly required.'''
cmdline_args = [
    ('raw_logs', None, '<path>',
     '''raw buildbot log repository'''),
    ('tag', 'gdb', '<name>',
     '''project tag for the created logs'''),
    ('rebuild', True, None,
     '''enable to ignore BUNSEN_COMMIT files. Bunsen should still prevent duplication of testlog data, but the process of checking will be slower.'''),
    ('timeslice', None, '<year_month>,...',
     '''enable to only commit testruns whose year_month tag belongs to this set. Requires gdb_label_year_month.py to be run first in order to add year_month.txt to the buildbot raw log directories.
e.g. timeslice=2019-08,2019-09'''),
    ('push_every', -1, '<num>',
     '''enable to push + recreate all working directories every few logs.
e.g. push_every=250'''),
    ('skip_until', None, '<path>',
     '''enable to skip all testruns until you reach a certain log directory (whose path ends in <path>). This is handy if your commit_logs process was interrupted, but assumes os.listdir() ordering is stable.
e.g. skip_until=Fedora-x86_64-native-extended-gdbserver-m64/a0/a051e2f3e0c1cedf4be0e1fedcd383fd203c769c'''),
]

# This assumes the format of the public GDB buildbot data:
# - https://gdb-buildbot.osci.io/results/
# - https://gdb-build.sergiodj.net/results/
#
# In each testrun's directory, a BUNSEN_COMMIT directory is created to
# mark the testrun as committed. By default, testruns with an already
# existing BUNSEN_COMMIT are skipped (see 'rebuild' option above).

# TODO: Suppress spurious progress bar and printing when used for cron jobs.

import sys
import tarfile
from bunsen import Bunsen, BunsenError, Testrun
from common.parse_dejagnu import grok_architecture

# TODO: wrap in a Bunsen API e.g. start_progress(), Bunsen.progress(),
# Bunsen.end_progress(), etc.?
from tqdm import tqdm

# TODO: add a command line option to enable/disable profiler
import cProfile, pstats, io
profiler = cProfile.Profile()
#profiler = None

import os
import shutil
import subprocess
import tempfile

from gdb.parse_dejagnu import parse_README, parse_dejagnu_sum, annotate_dejagnu_log

# TODO: 'osver' is a misnomer as this field also gives gdbserver configuration
# TODO: add other osver prefixes that show up in the older GDB buildbot logs
def is_osver(osver):
    for prefix in ['CentOS', 'Debian', 'Fedora', 'Ubuntu', 'AIX']:
        if osver.startswith(prefix):
            return True
    return False

def find_file_or_xz(testdir, name):
    if os.path.isfile(os.path.join(testdir, name)):
        return os.path.join(testdir, name)
    if os.path.isfile(os.path.join(testdir, name+'.xz')):
        return os.path.join(testdir, name+'.xz')
    return None

def remove_existing(path):
    if os.path.isfile(path):
        os.remove(path)

def add_testlog_or_xz(b, tmpdir, logpath):
    '''Uncompress if xz or gz, then add to Bunsen commit.

    Note: an uncompressed file is stored in tmpdir.
    Later, files in tmpdir should be deleted.
    '''
    if logpath.endswith('.xz'):
        assert os.path.isfile(logpath)
        print("will decompress {}".format(logpath))
        subprocess.run(['xz', '--decompress', '--keep', '--force', logpath])
        logpath_unxz = logpath[:-len('.xz')]
        logname = os.path.basename(logpath_unxz)
        destination_path = os.path.join(tmpdir, logname)
        if destination_path != logpath_unxz:
            remove_existing(destination_path)
            shutil.move(logpath_unxz, tmpdir)
        logpath_unxz = destination_path
    elif logpath.endswith('.gz'): # XXX xfail tables
        assert os.path.isfile(logpath)
        subprocess.run(['gunzip', '--keep', '--force', logpath])
        logpath_unxz = logpath[:-len('.gz')]
        logname = os.path.basename(logpath_unxz)
        destination_path = os.path.join(tmpdir, logname)
        if destination_path != logpath_unxz:
            remove_existing(destination_path)
            shutil.move(logpath_unxz, tmpdir)
        logpath_unxz = destination_path
    else:
        logpath_unxz = logpath
    b.add_testlog(logpath_unxz)
    return logpath_unxz

def pick_testlog(testdir, tmpdir, name):
    # '''Prefer already-uncompressed version of the file in tmpdir.'''
    #testlog_path = os.path.join(tmpdir, name)
    #if os.path.isfile(testlog_path):
    #    return testlog_path
    if testdir == '<tarfile>':
        return os.path.join(tmpdir, name)
    testlog_path = os.path.join(testdir, name)
    return testlog_path

def is_testdir(testdir):
    if not os.path.isfile(os.path.join(testdir,'README.txt')):
        return False
    if find_file_or_xz(testdir,'gdb.log') is None:
        return False
    if find_file_or_xz(testdir,'gdb.sum') is None:
        return False
    return True

def traverse_logs(log_src, restrict=None):
    '''
    Iterates the following logs:
    - <log_src>/<vm_name>/<hexsha_prefix>/<hexsha>/{README.txt,gdb.log,gdb.sum}
    '''

    for logdir in os.listdir(log_src):
        if restrict is not None and not logdir.startswith(restrict): continue
        osver = logdir
        logdir = os.path.join(log_src, logdir)
        if not os.path.isdir(logdir): continue
        for bigdir in os.listdir(logdir):
            big_sha = bigdir
            bigdir = os.path.join(logdir, bigdir)
            if not os.path.isdir(bigdir): continue
            # TODO: bigdir is also a valid testdir -- allows
            # specifying logs one level down? but osver must be
            # configured correctly / detected from log_src
            #if is_testdir(bigdir):
            #    yield osver, big_sha, bigdir
            for testdir in os.listdir(bigdir):
                test_sha = testdir
                testdir = os.path.join(bigdir, testdir)
                if not os.path.isdir(testdir): continue
                if not is_testdir(testdir): continue
                yield osver, test_sha, testdir

# TODO: factor into a common utility function, also used by systemtap
def flatten_logfiles(arglist):
    curr_args = arglist
    logfiles = []
    while len(curr_args) > 0:
        next_args = []
        for arg in curr_args:
            if isinstance(arg, str):
                logfiles.append((arg, None))
            elif isinstance(arg, list):
                next_args += arg
            elif isinstance(arg, tarfile.TarFile):
                for tarinfo in arg:
                    # XXX don't accept subdirectories for now
                    if not tarinfo.isreg(): continue
                    logfiles.append((tarinfo.name, tarinfo))
            elif isinstance(arg, tarfile.TarInfo):
                if not tarinfo.isreg(): continue
                logfiles.append((tarinfo.name, tarinfo))
            else:
                raise BunsenError("unknown commit_logs arg {}".format(arg))
        curr_args = next_args
    return logfiles

# TODOXXX Factor code from commit_repo_logs:
def commit_logs(b, wd, *args, **kwargs):
    opts = kwargs['opts'] if 'opts' in kwargs else None
    push = kwargs['push'] if 'push' in kwargs else True
    alt_year_month = kwargs['year_month'] if 'year_month' in kwargs else None
    tarfile = kwargs['tarfile'] if 'tarfile' in kwargs else None
    tarballname = kwargs['tarballname'] if 'tarballname' in kwargs else None
    osver = kwargs['osver'] if 'osver' in kwargs else None

    # for error reporting:
    testdir = kwargs['testdir'] if 'testdir' in kwargs else None
    if tarfile is not None and testdir is None: testdir = '<tarfile>'
    # TODOXXX also extract datestamp for bunsen-push upload

    # XXX tmpdir is required for unxzing
    tmpdir = kwargs['tmpdir'] if 'tmpdir' in kwargs else None
    tmpdir_created = False
    if tmpdir is None:
        tmpdir_created = True
        tmpdir = tempfile.mkdtemp()

    # flatten list of args to list of (path, OPTIONAL tarfile.TarInfo)
    logfiles = flatten_logfiles(args)

    for logfile, tarinfo in logfiles:
        if logfile == 'BUNSEN_COMMIT': continue # don't add to commit
        if logfile == 'year_month.txt': continue # don't add to commit
        if logfile.startswith('index.html'): continue # don't add to commit
        if logfile.startswith('baseline'): continue # don't add to commit
        if logfile.startswith('xfail'): continue # don't add to commit
        if logfile.startswith('previous_'): continue # don't add to commit
        if tarinfo is not None:
            t = tarfile.extractfile(tarinfo)
            logname = os.path.basename(logfile)
            with open(os.path.join(tmpdir, logname), 'wb') as f:
                f.write(t.read()) # TODOXXX read_decode utf-8?
            logpath = os.path.join(tmpdir, logname)
        else:
            logpath = os.path.join(testdir, logfile)
        if os.path.isdir(logpath): continue # don't add to commit
        add_testlog_or_xz(b, tmpdir, logpath)

    testrun = Testrun()
    all_cases = []
    gdb_README = pick_testlog(testdir, tmpdir, 'README.txt')
    gdb_sum = pick_testlog(testdir, tmpdir, 'gdb.sum') # XXX parser autodetects .xz
    gdb_log = pick_testlog(testdir, tmpdir, 'gdb.log') # XXX parser autodetects .xz
    testrun = parse_README(testrun, gdb_README)
    testrun.osver = osver
    testrun = parse_dejagnu_sum(testrun, gdb_sum, all_cases=all_cases)
    testrun = annotate_dejagnu_log(testrun, gdb_log, all_cases, verbose=False)

    if testrun is None:
        b.reset_all()
        return None # TODOXXX Pass error message?

    b.add_testrun(testrun)

    if testrun.year_month is None and alt_year_month is not None:
        testrun.year_month = alt_year_month
    # TODOXXX handle year_month from tarballname
    if testrun.year_month is None:
        print("WARNING: skipped {} due to missing year_month"\
              .format(testdir))
        b.reset_all()
        return None # TODOXXX Pass error message?

    # XXX To avoid huge working copies, use branch_extra to split testruns branches by source buildbot:
    if 'osver' in testrun:
        commit_id = b.commit(opts.tag, wd=wd, push=False, allow_duplicates=False, extra_label=testrun.osver)
    else:
        # TODOXXX Need to extract osver more diligently for tarfile submissions:
        commit_id = b.commit(opts.tag, wd=wd, push=False, allow_duplicates=False)
    #commit_id = b.commit(opts.tag, wd=wd, push=False, allow_duplicates=True, wd_index=wd_index, wd_testruns=wd_testruns)

    if push:
        wd.push_all()

    if tmpdir_created: shutil.rmtree(tmpdir)

    return commit_id

def commit_repo_logs(b, log_src, opts=None):
    '''
    Commit logs from local path log_src. Scans for the following logs:
    - <log_src>/<vm_name>/<hexsha_prefix>/<hexsha>/{README.txt,gdb.log,gdb.sum}

    Log files can also be compressed with .xz.
    '''
    global cmdline_args
    if opts is None: opts = b.opts(cmdline_args)

    # TODO: Turn this into a command line option. Or we should be able to
    # just give the subdirectory and have the traversal look one level down.
    # Allows us to commit in small slices when we want to babysit the process.
    #restrict = "CentOS"
    restrict = None

    # for uncompressed logfiles:
    tmpdir = tempfile.mkdtemp()

    # XXX Purely to have a progress bar:
    n_logdirs = 0
    known_logdirs = set()
    skipping = opts.skip_until is not None
    for osver, test_sha, testdir in traverse_logs(log_src, restrict=restrict):
        if skipping and testdir.endswith(opts.skip_until):
            skipping = False
        if skipping: continue

        year_month = None
        if opts.timeslice is not None and os.path.isfile(os.path.join(testdir,'year_month.txt')):
            with open(os.path.join(testdir,'year_month.txt'),'r') as f:
                year_month = f.read().strip()
        if opts.timeslice is not None and year_month not in opts.timeslice:
            continue # won't be processed
        if not opts.rebuild and \
           os.path.isfile(os.path.join(testdir,'BUNSEN_COMMIT')):
            continue # won't be processed
        n_logdirs += 1
        known_logdirs.add(testdir)

    progress = tqdm(iterable=None, desc="Committing GDB testlogs",
                    total=n_logdirs, leave=False, unit='dir')
    total_dirs = 0
    new_dirs = 0
    new_runs = 0

    if profiler is not None: profiler.enable()
    # XXX: I experimented with separate wd's per branch but this adds nothing.
    wd = b.checkout_wd()
    #wd_index = b.checkout_wd(postfix="index")
    #wd_testruns = b.checkout_wd(postfix="testruns")
    skipping = opts.skip_until is not None
    last_osver = None
    for osver, test_sha, testdir in traverse_logs(log_src, restrict=restrict):
        if osver != last_osver:
            if not is_osver(osver):
                print("WARNING: unknown osver directory '{}'".format(osver), file=sys.stderr)
            print("Now processing:", osver)
            last_osver = osver

        if skipping and testdir.endswith(opts.skip_until):
            skipping = False
        if skipping: continue

        # XXX Check log directory for a BUNSEN_COMMIT file as a quick
        # way to avoid making duplicate commits that doesn't require
        # hashing files:
        if not opts.rebuild and \
           os.path.isfile(os.path.join(testdir,'BUNSEN_COMMIT')):
            # don't update progress
            total_dirs += 1
            continue

        # XXX Check log directory for a year_month.txt file as
        # a hack to speed up committing slices of the buildbot
        # history (avoid parsing logs that won't be
        # committed). These year_month.txt files are generated
        # by a separate script.
        year_month = None
        if os.path.isfile(os.path.join(testdir,'year_month.txt')):
            with open(os.path.join(testdir,'year_month.txt'),'r') as f:
                year_month = f.read().strip()
        if opts.timeslice is not None and year_month not in opts.timeslice:
            # don't update progress
            total_dirs += 1
            continue

        commit_id = commit_logs(b, wd, opts=opts, push=False,
                                testdir=testdir, tmpdir=tmpdir,
                                year_month=year_month, osver=osver,
                                *os.listdir(testdir))

        if commit_id is None:
            if testdir in known_logdirs:
                progress.update(n=1)
            total_dirs += 1

        # XXX Create BUNSEN_COMMIT file to mark logs as committed:
        if commit_id is not None:
            with open(os.path.join(testdir,"BUNSEN_COMMIT"), 'w') as f:
                f.write(commit_id)
        # TODO: Delay this 'till just before the final 'git push'.

        if profiler is not None:
            profiler.disable()
            s = io.StringIO()
            ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
            ps.print_stats(10)
            print(s.getvalue())
            profiler.enable()
        # XXX The counting here is not as complex as with SystemTap:
        new_runs += 1
        progress.update(n=1)
        total_dirs += 1; new_dirs += 1

        # XXX Paranoia to ensure tempfiles don't accumulate.
        #shutil.rmtree(tmpdir)
        #tmpdir = tempfile.mkdtemp()

        # XXX Incremental pushing support
        if opts.push_every != -1 and new_runs % opts.push_every == 0:
            wd.push_all()
            #wd_index.push_all()
            #wd_testruns.push_all()
            wd.destroy()
            #wd_index.destroy()
            #wd_testruns.destroy()
            wd = b.checkout_wd()
            #wd_index = b.checkout_wd(postfix="index")
            #wd_testruns = b.checkout_wd(postfix="testruns")

    if profiler is not None:
        profiler.disable()

    # TODO: Add an option to test parser performance across a log
    # collection by skipping the commit+push steps.

    wd.push_all()
    #wd_index.push_all()
    #wd_testruns.push_all() # XXX this failed requring manual fixup
    #wd.destroy() # TODO: enable, control with a command line option

    shutil.rmtree(tmpdir)

    progress.close()
    print("Added {} new testruns from {} directories of {} total" \
          .format(new_runs, new_dirs, total_dirs))

b = Bunsen()
if __name__=='__main__':
    opts = b.cmdline_args(sys.argv, info=info, args=cmdline_args,
                          required_args=['raw_logs'])
    opts.timeslice = opts.get_list('timeslice')
    commit_repo_logs(b, opts.raw_logs, opts=opts)
