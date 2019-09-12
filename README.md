# `bunsen`

`bunsen` indexes and stores a collection of DejaGNU test result files in a Git repo. The Git repo generated by `bunsen` contains an index in JSON format to speed up querying and test result analysis. For test result analysis, `bunsen` includes a Python library and a collection of analysis scripts.

When storing test result data, Git's de-duplication produces an impressive compression factor. For example, a collection of 8757 testruns from https://gdb-buildbot.osci.io/results/ takes up 24GB when the log files are individually compressed with `xz`, but only 3.5GB when the files are packed into a Git repo using `bunsen`.

This is an early release of the `bunsen` codebase, meant to act as a demo for my [lightning talk at GNU Cauldron 2019](https://gcc.gnu.org/wiki/cauldron2019#cauldron2019talks.Compact_storage_and_analysis_of_DejaGNU_test_logs). The codebase is intended as a starting point towards automatic regression detection and analysis for free software projects whose testing setup generates a large volume of test results across different system configurations.

## Prerequisites

`bunsen` requires Git, Python 3 and the following libraries:

    $ pip3 install --user tqdm # see https://github.com/tqdm/tqdm
    $ pip3 install --user GitPython # see https://github.com/gitpython-developers/GitPython
    $ pip3 install --user dateparser # see https://github.com/scrapinghub/dateparser

## Getting Started -- Demo on GDB Test Result Data

Use the following commands to clone a pre-constructed sample collection of test results from the GDB project:

    $ git clone https://github.com/serhei/bunsen.git bunsen-gdb-demo
    $ cd bunsen-gdb-demo
    $ git clone --bare https://github.com/serhei/bunsen-gdb-sample-data .bunsen/bunsen.git
    $ ./bunsen.py init

Obtain a copy of the `binutils-gdb` repo for analysis scripts that refer to the project's commit history:

    $ git clone git://sourceware.org/git/binutils-gdb.git

Analysis scripts are located under `./scripts-master/` and invoked with `./bunsen.py run +name_of_script args...`.

For example, to list all testruns in the collection:

    $ ./bunsen.py run +list_runs

This command invokes `scripts-master/list_runs.py` on the `bunsen` collection located at `.bunsen/bunsen.git`.

To list which commits in the `binutils-gdb` repo were tested:

    $ ./bunsen.py run +list_commits ./binutils-gdb gdb

... *check back later for more details* ...

## Included Analysis Scripts

*Note on command line arguments:* All testruns in a `bunsen` collection are tagged with a project name (for example, `gdb` is the project name for all testruns in the sample GDB test results collection) and a `bunsen` commit id. The `bunsen` commit id uniquely identifies the testrun, and is the commit id of the commit containing the testrun's original test logs within the `bunsen` Git repo.

Viewing test results:
- `+list_runs [<project>]` lists all testruns (or all testruns under `project`).
- `+list_commits <source_repo> <project>` lists the testruns (or the testruns under `project`) for each commit in the `master` branch of the Git repo `source_repo`.

Comparing test results:
- `+diff_runs <baseline_id> <testrun_id>` compares the testrun `testrun_id` against the baseline testrun `baseline_id`. (Testruns are identified by their `bunsen` commit id.)

(*Work in progress.*) Examples of other analyses:
- `+when_failed <source_repo> <project> <key>` walks the history of the `master` branch of the Git repo `source_repo`. For every commit, `+when_failed` compares testruns under <project> with testruns for the parent commit, then prints a summary of how test results changed for test cases whose name contains `key`.

## Adapting to Your Own Project's Needs

(*Work in progress.*) This functionality is not yet complete. Some assembly required.

My goal is to write a 'batteries included' DejaGNU test log indexing script that can be pointed at a directory of test results to generate a reasonable `bunsen` collection with minimal adaptations, if any. But DejaGNU test log formats can vary and it will take more time and effort to develop code that handles these variations.

Right now, if you want to use `bunsen` analyses on your own project's test results, you will need to write your own DejaGNU test log parser. Two example parsers are included, `scripts-master/gdb/parse_dejagnu.py` (389 lines) and `scripts-master/systemtap/parse_dejagnu.py` (432 lines). These parsers provide a good template to follow and illustrate some of the concerns that arise when parsing DejaGNU files.

You will also need to write a script that invokes your parser on a repository of test logs and uses the `bunsen.py` library to assemble an indexed `bunsen` collection. `scripts-master/gdb/commit_logs.py` (281 lines) provides an example of how this is done.
