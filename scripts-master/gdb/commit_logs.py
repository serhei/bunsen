#!/usr/bin/env python3
# WIP -- Commit a set of GDB test logs to a Bunsen git repo. Some
# assembly required.
usage = "commit_logs.py <local_path>"

# This assumes the format of the public GDB buildbot data:
# - https://gdb-buildbot.osci.io/results/
# - https://gdb-build.sergiodj.net/results/
#
# In each testrun's directory, a BUNSEN_COMMIT directory is created to
# mark the testrun as committed. By default, testruns with an already
# existing BUNSEN_COMMIT are skipped (see 'rebuild' option below).

# TODO: You *probably* want to run unxz on the log files before adding
# them to the repo. Adding xz'ed log files still works but will cause
# the Git deduplication to be less efficient. This is high on my list
# of things to fix.

# TODO: Suppress spurious progress bar and printing when used for cron jobs.

# TODO: Suggested options:
# - project tag for the created logs
tag = 'gdb'
# - enable to ignore BUNSEN_COMMIT files. Bunsen should still prevent
#   duplication of testlog data, but the process of checking will be
#   slower.
rebuild = True
# - enable to only commit testruns whose year_month tag belongs to
#   this set. This requires gdb_label_year_month.py to be run first in
#   order to add year_month.txt to the buildbot raw log directories.
#timeslice = {'2019-09'}
timeslice = None
# - enable to push + recreate all working directories every few logs
#push_every = 250
push_every = None
# - enable to skip all testruns until you reach a certain log
#   directory (whose path ends in skip_until). This is handy if your
#   commit_logs process was interrupted, but assumes os.listdir()
#   ordering is stable.
#skip_until = 'Fedora-x86_64-native-extended-gdbserver-m64/a0/a051e2f3e0c1cedf4be0e1fedcd383fd203c769c'
skip_until = None

import sys
from bunsen import Bunsen, Testrun

# TODO: wrap in a Bunsen API e.g. start_progress(), Bunsen.progress(),
# Bunsen.end_progress(), etc.?
from tqdm import tqdm

# TODO: add a command line option to enable/disable profiler
import cProfile, pstats, io
profiler = cProfile.Profile()
#profiler = None

import os

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

def is_testdir(testdir):
    if not os.path.isfile(os.path.join(testdir,'README.txt')):
        return False
    if find_file_or_xz(testdir,'gdb.log') is None:
        return False
    if find_file_or_xz(testdir,'gdb.sum') is None:
        return False
    return True

def commit_logs(b, log_src):
    '''
    Commit logs from local path log_src. Scans for the following logs:
    - <log_src>/<vm_name>/<hexsha_prefix>/<hexsha>/{README.txt,gdb.log,gdb.sum}

    Log files can also be compressed with .xz.
    '''
    # TODO: Turn this into a command line option. Or we should be able to
    # just give the subdirectory and have the traversal look one level down.
    # Allows us to commit in small slices when we want to babysit the process.
    #restrict = "CentOS"
    restrict = None

    # XXX Purely to have a progress bar:
    n_logdirs = 0
    skipping = skip_until is not None
    for logdir in os.listdir(log_src):
        if restrict is not None and not logdir.startswith(restrict): continue
        logdir = os.path.join(log_src, logdir)
        if not os.path.isdir(logdir): continue
        for bigdir in os.listdir(logdir):
            bigdir = os.path.join(logdir, bigdir)
            if not os.path.isdir(bigdir): continue
            # TODO: also check if bigdir is a testdir
            for testdir in os.listdir(bigdir):
                test_sha = testdir
                testdir = os.path.join(bigdir, testdir)
                if skipping and testdir.endswith(skip_until):
                    skipping = False
                if skipping: continue
                if not os.path.isdir(testdir): continue
                if not is_testdir(testdir): continue

                year_month = None
                if timeslice is not None and os.path.isfile(os.path.join(testdir,'year_month.txt')):
                    with open(os.path.join(testdir,'year_month.txt'),'r') as f:
                        year_month = f.read().strip()
                if timeslice is not None and year_month not in timeslice:
                    continue # won't be processed
                if not rebuild and \
                   os.path.isfile(os.path.join(testdir,'BUNSEN_COMMIT')):
                    continue # won't be processed
                n_logdirs += 1

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
    skipping = skip_until is not None
    for logdir in os.listdir(log_src):
        osver = logdir # name of toplevel logdir
        if restrict is not None and not osver.startswith(restrict): continue
        if not is_osver(osver):
            print("WARNING: unknown osver directory '{}'".format(osver), file=sys.stderr)
        # if rebuild:
        #     wd.git.gc() # XXX attempt to clean up frequently
        #     wd.push_all() # XXX attempt to clean up frequently
        print("Now processing:", osver)
        logdir = os.path.join(log_src, logdir)
        if not os.path.isdir(logdir): continue
        for bigdir in os.listdir(logdir):
            # if rebuild:
            #     wd.git.gc() # XXX more aggressive attempt to clean up
            #     wd.push_all() # XXX more aggressive attempt to clean up
            bigdir = os.path.join(logdir, bigdir)
            if not os.path.isdir(bigdir): continue
            # TODO: also check if bigdir is a testdir
            for testdir in os.listdir(bigdir):
                test_sha = testdir
                testdir = os.path.join(bigdir, testdir)
                if skipping and testdir.endswith(skip_until):
                    skipping = False
                if skipping: continue
                if not os.path.isdir(testdir): continue
                if not is_testdir(testdir): continue

                # XXX Check log directory for a BUNSEN_COMMIT file as a quick
                # way to avoid making duplicate commits that doesn't require
                # hashing files:
                if not rebuild and \
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
                if timeslice is not None and year_month not in timeslice:
                    # don't update progress
                    total_dirs += 1
                    continue

                for logfile in os.listdir(testdir):
                    if logfile == 'BUNSEN_COMMIT': continue # don't add to commit
                    if logfile == 'year_month.txt': continue # don't add to commit
                    if logfile.startswith('index.html'): continue # don't add to commit
                    # TODO: Consider excluding xfail.gz, xfail.table.gz
                    logpath = os.path.join(testdir, logfile)
                    if os.path.isdir(logpath): continue # don't add to commit
                    # TODO (IMPORTANT!): uncompress xz,gz files? -- git
                    # deduplication chewing on .xz may be a big source
                    # of slowdown and the resulting repo format is
                    # inconvenient:
                    b.add_testlog(logpath)
                testrun = Testrun()
                all_cases = []
                gdb_README = os.path.join(testdir, 'README.txt')
                gdb_sum = os.path.join(testdir, 'gdb.sum') # XXX parser autodetects .xz
                gdb_log = os.path.join(testdir, 'gdb.log') # XXX parser autodetects .xz
                testrun = parse_README(testrun, gdb_README)
                testrun = parse_dejagnu_sum(testrun, gdb_sum, all_cases=all_cases)
                testrun = annotate_dejagnu_log(testrun, gdb_log, all_cases)
                if testrun is None:
                    b.reset_all()
                    total_dirs += 1
                    continue
                testrun.osver = osver

                b.add_testrun(testrun)

                if testrun.year_month is None:
                    print("WARNING: skipped {} due to missing year_month"\
                          .format(testdir))
                    b.reset_all()
                    progress.update(n=1) # XXX was included in n_logdirs
                    total_dirs += 1
                    continue

                # XXX To avoid huge working copies, use branch_extra to split testruns branches by source buildbot:
                commit_id = b.commit(tag, wd=wd, push=False, allow_duplicates=False, branch_extra=testrun.osver)
                #commit_id = b.commit(tag, wd=wd, push=False, allow_duplicates=True, wd_index=wd_index, wd_testruns=wd_testruns)

                # XXX Create BUNSEN_COMMIT file to mark logs as committed:
                with open(os.path.join(testdir,"BUNSEN_COMMIT"), 'w') as f:
                    f.write(commit_id)

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

                # XXX Incremental pushing support
                if push_every is not None and new_runs % push_every == 0:
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

    progress.close()
    print("Added {} new testruns from {} directories of {} total" \
          .format(new_runs, new_dirs, total_dirs))

b = Bunsen()
if __name__=='__main__':
    log_src = b.cmdline_args(sys.argv, 1, usage=usage)
    if ':' in log_src:
        host, _sep, log_src = log_src.partition(':')
        print("Log repo downloading is currently not supported!")
        exit(1)

    commit_logs(b, log_src)