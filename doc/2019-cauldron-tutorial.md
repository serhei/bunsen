## Getting Started -- Demo on GDB Test Result Data

Use the following commands to clone a pre-constructed sample
collection of test results from the GDB project:

    $ git clone git://sourceware.org/git/bunsen.git bunsen-gdb-demo
    $ cd bunsen-gdb-demo
    $ git checkout cauldron2019-demo
    $ git clone --bare https://github.com/serhei/bunsen-gdb-sample-data .bunsen/bunsen.git
    $ ./bunsen.py init

Obtain a copy of the `binutils-gdb` repo for analysis scripts that
refer to the project's commit history:

    $ git clone git://sourceware.org/git/binutils-gdb.git

Analysis scripts are located under `./scripts-main/` and invoked
with `./bunsen.py run +name_of_script args...`.

For example, to list all testruns in the collection:

    $ ./bunsen.py run +list_runs

This command invokes `scripts-main/list_runs.py` on the `bunsen`
collection located at `.bunsen/bunsen.git`.

Each testrun in the `bunsen` collection is identified by its
`bunsen_commit_id`, the hexsha of a commit in the `bunsen` Git
repo. The Git repo stores the following data for each testrun:

- On branch `gdb/testlogs-<year>-<month>`, commit `<hexsha>`: the
  original DejaGNU `.log` and `.sum` files. The commit message
  contains a brief JSON summary of the testrun (e.g. pass/fail count,
  architecture, version).
- On branch `index`, latest commit, within file
  `gdb-<year>-<month>.json`: a brief JSON summary of the testrun.
- On branch `gdb/testruns-<year>-<month>-<extra>`, latest commit,
  within file `gdb-<hexsha>.json`: a full JSON representation of the
  testrun (including detailed pass/fail information and line
  coordinates of each testcase in the original logs).

Here `<year>-<month>` is the date the testrun was performed, `<extra>`
is an additional cookie used to separate testruns files into several
branches and reduce Git working copy size. The name of the `testruns`
branch is specified by the brief JSON summary's
'bunsen_testruns_branch' field.

You can clone the `bunsen` Git repo for manual inspection with
`./bunsen.py checkout-wd`. But it's more convenient to run analysis
scripts to summarize the information.

For example, to list which commits in the `binutils-gdb` repo were
tested:

    $ ./bunsen.py run +list_commits ./binutils-gdb gdb

For each `binutils-gdb` commit that was tested, the output of
`+list_commits` includes several testruns:

    fdd5026 RISC-V: Force linker error exit after unresolvable reloc.
    * 2019-08 341fae762ec4e2c366a3ca36f35495e2f794cb55 53242 pass 456 fail
    {"source_commit": "fdd502691f8b893e321f19260464831f9726c5d4", "source_branch": "master", "pass_count": 53242, "fail_count": 456, "arch": "i686", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Fedora-i686", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Fedora-i686", "bunsen_commit_id": "341fae762ec4e2c366a3ca36f35495e2f794cb55"}
    * 2019-08 6082b723658ced456a82b1385b890c7dfbd2a709 55117 pass 409 fail
    {"source_commit": "fdd502691f8b893e321f19260464831f9726c5d4", "source_branch": "master", "pass_count": 55117, "fail_count": 409, "arch": "x86_64", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Fedora-x86_64-m32", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Fedora-x86_64-m32", "bunsen_commit_id": "6082b723658ced456a82b1385b890c7dfbd2a709"}
    * 2019-08 cfe98a2a91c2f2ab918e03f22dba02480146a8b4 55383 pass 1297 fail
    {"source_commit": "fdd502691f8b893e321f19260464831f9726c5d4", "source_branch": "master", "pass_count": 55383, "fail_count": 1297, "arch": "x86_64", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Fedora-x86_64-m64", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Fedora-x86_64-m64", "bunsen_commit_id": "cfe98a2a91c2f2ab918e03f22dba02480146a8b4"}
    * 2019-08 a69ec69d768ccc6a7d1117b9531f9dedd65a5b0e 48655 pass 374 fail
    {"source_commit": "fdd502691f8b893e321f19260464831f9726c5d4", "source_branch": "master", "pass_count": 48655, "fail_count": 374, "arch": "armhf", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Ubuntu-ARMhf-m32", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Ubuntu-ARMhf-m32", "bunsen_commit_id": "a69ec69d768ccc6a7d1117b9531f9dedd65a5b0e"}
    * 2019-08 149ed94060fadd396a029847736a162f947ba60b 51759 pass 190 fail
    {"source_commit": "fdd502691f8b893e321f19260464831f9726c5d4", "source_branch": "master", "pass_count": 51759, "fail_count": 190, "arch": "aarch64", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Ubuntu-Aarch64-m64", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Ubuntu-Aarch64-m64", "bunsen_commit_id": "149ed94060fadd396a029847736a162f947ba60b"}
    
    47a536d Remove "\nError: " suffix from nat/fork-inferior.c:trace_start_error warning message
    * 2019-08 445470177832ed9d6fad93a213ce26ff5b0f4747 53403 pass 296 fail
    {"source_commit": "47a536d940d2f2bccfec51539b857da06ebc429e", "source_branch": "master", "pass_count": 53403, "fail_count": 296, "arch": "i686", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Fedora-i686", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Fedora-i686", "bunsen_commit_id": "445470177832ed9d6fad93a213ce26ff5b0f4747"}
    * 2019-08 4b1262198311aab313188812527952a9cfdd8b04 55115 pass 408 fail
    {"source_commit": "47a536d940d2f2bccfec51539b857da06ebc429e", "source_branch": "master", "pass_count": 55115, "fail_count": 408, "arch": "x86_64", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Fedora-x86_64-m32", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Fedora-x86_64-m32", "bunsen_commit_id": "4b1262198311aab313188812527952a9cfdd8b04"}
    * 2019-08 d1911a937552aad77f73972fa1cb840a12b061f6 55386 pass 1294 fail
    {"source_commit": "47a536d940d2f2bccfec51539b857da06ebc429e", "source_branch": "master", "pass_count": 55386, "fail_count": 1294, "arch": "x86_64", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Fedora-x86_64-m64", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Fedora-x86_64-m64", "bunsen_commit_id": "d1911a937552aad77f73972fa1cb840a12b061f6"}
    * 2019-08 e1b25d4c1289fa661536c0cac2483ff535095f2a 48640 pass 378 fail
    {"source_commit": "47a536d940d2f2bccfec51539b857da06ebc429e", "source_branch": "master", "pass_count": 48640, "fail_count": 378, "arch": "armhf", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Ubuntu-ARMhf-m32", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Ubuntu-ARMhf-m32", "bunsen_commit_id": "e1b25d4c1289fa661536c0cac2483ff535095f2a"}
    * 2019-08 f61d8b207fd414c6165cbcee028cf28f77c3f84b 51754 pass 190 fail
    {"source_commit": "47a536d940d2f2bccfec51539b857da06ebc429e", "source_branch": "master", "pass_count": 51754, "fail_count": 190, "arch": "aarch64", "version": "8.3.50.20190830-git", "year_month": "2019-08", "osver": "Ubuntu-Aarch64-m64", "bunsen_testlogs_branch": "gdb/testlogs-2019-08", "bunsen_testruns_branch": "gdb/testruns-2019-08-Ubuntu-Aarch64-m64", "bunsen_commit_id": "f61d8b207fd414c6165cbcee028cf28f77c3f84b"}
    
    ...

Suppose we want to compare the test results for `binutils-gdb` commits
`47a536d` and `fdd5026`. We can use `+diff_runs` to compare two
individual testruns by specifying their `bunsen_commit_id`s:

    $ ./bunsen.py run +diff_runs 4454701 341fae7

That said, commits `47a536d` and `fdd5026` were each tested on 5
different machines. Comparing the results one testrun at a time will
be inconvenient. Instead, we can summarize the differences between all
testruns (comparing matching configurations where available, and
showing each regression only once) with the `+diff_commits` script:

    $ ./bunsen.py run +diff_commits 47a536d fdd5026

## Included Analysis Scripts

*Note on command line arguments:* All testruns in a `bunsen`
collection are tagged with a project name (for example, `gdb` is the
project name for all testruns in the sample GDB test results
collection) and a `bunsen` commit id. The `bunsen` commit id uniquely
identifies the testrun, and is the commit id of the commit containing
the testrun's original test logs within the `bunsen` Git repo.

Viewing test results:
- `+list_runs [<project>]` lists all testruns (or all testruns under
  `project`).
- `+list_commits <source_repo> [<project>]` lists the testruns (or the
  testruns under `project`) for each commit in the `master` branch of
  the Git repo `source_repo`.

Comparing test results:
- `+diff_runs <baseline_id> <testrun_id>` compares the testrun
  `testrun_id` against the baseline testrun `baseline_id`. (Testruns
  are identified by their `bunsen` commit id.)
- (*Work in progress.*) `+diff_commits <source_repo> <baseline_commit> <commit>`
  compares testruns for `commit` in `source_repo` against
  testruns for the baseline commit `baseline_commit` and summarizes
  regressions.

(*Work in progress.*) Examples of other analyses:
- `+when_failed <source_repo> <project> <key>` walks the history of
  the `master` branch of the Git repo `source_repo`. For every commit,
  `+when_failed` compares testruns under <project> with testruns for
  the parent commit, then prints a summary of how test results changed
  for test cases whose name contains `key`.

## Adapting to Your Own Project's Needs

(*Work in progress.*) This functionality is not yet complete. Some
assembly required.

My goal is to write a 'batteries included' DejaGNU test log indexing
script that can be pointed at a directory of test results to generate
a reasonable `bunsen` collection with minimal adaptations, if any. But
DejaGNU test log formats can vary and it will take more time and
effort to develop code that handles these variations.

Right now, if you want to use `bunsen` analyses on your own project's
test results, you will need to write your own DejaGNU test log
parser. Two example parsers are included,
`scripts-main/gdb/parse_dejagnu.py` (389 lines) and
`scripts-main/systemtap/parse_dejagnu.py` (432 lines). These parsers
provide a good template to follow and illustrate some of the concerns
that arise when parsing DejaGNU files.

You will also need to write a script that invokes your parser on a
repository of test logs and uses the `bunsen.py` library to assemble
an indexed `bunsen` collection. `scripts-main/gdb/commit_logs.py`
(281 lines) provides an example of how this is done.
