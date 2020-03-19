# Bunsen repository format

A valid Bunsen repo is a directory containing the following:
- `bunsen.git`, a bare Git repo laid out according to a format described below.
- `config`, a configuration file in INI format.
- TODO: `cache`, a directory with cached analysis results.
- TODO: A lockfile controlling write access to `bunsen.git`.
- TODO: Directories named `scripts`,`scripts-*` containing analysis scripts.

## Projects

The Bunsen Git repo `bunsen.git` stores testruns for one or more projects. For example, a Bunsen Git repo for GDB test results would contain a project named `gdb`. 

We may want to separate testruns into different projects according to different software codebases being tested, or according to different test result origins for the same software codebase.

For example, we could store GDB test results originating from a buildbot in `gdb-buildbot`, GDB test results submitted manually by contributors in `gdb-misc`, and so forth.

## Bunsen Git repo layout and index format

Each testrun in the Bunsen Git repo is uniquely identified by a `bunsen_commit_id`, which is the hexsha of a commit in the `bunsen` Git repo.

For each testrun in a project `<project>`, the Bunsen Git repo stores data as follows:
- On branch `<project>/testlogs-<year>-<month>`, commit `<bunsen_commit_id>`: the original DejaGNU `.log` and `.sum` files (together with any additional test logs). The commit message includes a brief JSON summary of the testrun.
- On branch `index`, latest commit, within the file `<project>-<year>-<month>.json`: a brief JSON summary of the testrun.
- On branch `<project>/testruns-<year>-<month>[-<tag>]`, latest commit, within the file `<project>-<bunsen_commit_id>.json`: a full JSON representation of the testrun.
  * `<tag>` is an *optional* tag which can be used to split a large testruns branch into several smaller branches, in order to limit the size of a checked-out working copy of the Bunsen Git repo. The exact content of `<tag>` is not important (for example, we could generate `<tag>` based on the `arch` and `osver` fields).

### JSON representation of testruns

The JSON summary of a testrun is a JSON dict. The following fields are required:
- `year_month`: the year and month when the test was carried out, e.g. `2019-09`. (If the test logs do not provide an exact timestamp, it's possible to substitute the year and month when the source commit was created, or simply the year and month when the testrun was added to the Bunsen repo.)
- `bunsen_testlogs_branch`: the name of the branch containing the original test logs for this testrun.
- `bunsen_testruns_branch`: the name of the branch containing the full JSON representation of this testrun.
- `bunsen_commit_id`: hexsha of the commit under `bunsen_testlogs_branch` containing the original test logs.
- `bunsen_version`: the version of Bunsen used to generate this testrun.

In addition, the following fields are usually present in a JSON summary of a testrun:
- `timestamp`: the date and time when the test was carried out. (If the test logs do not provide an exact timestamp, it's possible to substitute the time when the source commit was created, or simply the time when the testrun was added to the Bunsen repo.)
- `version`: version (usually, major version and commit id) of the software being tested.
- `source_commit`: commit id of the software being tested.
- `source_branch`: the name of the branch containing `source_commit`.
- `arch`: hardware architecture of the machine which generated this testrun.
- `osver`: operating system version for the machine which generated this testrun.
- `origin_host`: hostname or other identifier for the machine which generated this testrun.
- `pass_count`: number of passing testcases.
- `fail_count`: number of failing testcases.

In addition:
- `problems`: only present if the testrun was not validated by the parser. Documents any missing/unknown important fields. This allows testruns that are not yet correctly handled by the parser to be added to the repo and fixed later.

TODO: Certain fields are *configuration fields* which identify the software configuration being tested. Describe how to distinguish these fields from non-configuration metadata such as `pass_count`. Additional configuration fields, e.g. `kernel_version`, `elfutils_version`, are added where relevant.

TODOXXX Add `bunsen.py` code to validate required fields in a testrun or testcase.

The full JSON representation of a testrun is a JSON dict containing the same fields as the JSON summary, plus a field `testcases` containing an array of JSON dicts representing testcases.

The following fields are required in the JSON representation of a testcase:
- TODO `name`
- TODO `outcome`

In addition, the following fields are usually present in the JSON representation of a testcase:
- TODO `subtest`
- TODO `origin_log`
- TODO `origin_sum`
- TODOXXX

TODOXXX Describe the Cursor type and its textual representation.

TODO: Certain analysis scripts can produce other JSON artefacts, including diffs (between different testruns), 2nd-order diffs, and cached analysis data. Document the details in the corresponding scripts.

### Procedure for adding a testrun

We commit the original testlogs as well as a JSON representation of the testrun. If the parsing ran into problems, we still commit a JSON representation, but add a `problems` field documenting any problems.

Several possible cases to handle, depending on what's alread in the repo:

- *Testlogs and testrun JSON are not present in the repo*. Commit new testlog and testrun files. Create a commit appending the JSON summary of the testrun to `<project>-<year>-<month>.json` under the `index` branch.

- *Identical testlogs are already present in the repo, but the testrun JSON is not present.* TODO Leave testlogs branch unchanged. Commit new testrun file. Create a commit appending the JSON summary of the testrun to `<project>-<year>-<month>.json` under the `index` branch. (TODO Be sure to consider what happens if the `year-month` for the testlogs has changed.)

- *Identical testlogs are already present in the repo, but the testrun JSON has changed.* TODO Leave testlogs branch unchanged. Create a commit replacing the testrun file in the testruns branch. Create a commit replacing the JSON summary of the testrun under `<project>-<year>-<month>.json` under the `index` branch. (TODO Be sure to consider what happens if the `year-month` for the testlogs has changed. Note that the JSON summary in the original commit message in the testlogs branch becomes obsolete.)

- *Identical testlogs and testrun JSON are already present in the repo.* Leave testlogs branch unchanged -- this makes adding a testrun an idempotent operation.

### Procedure for updating a testrun

See '*Identical testlogs are already present in the repo, but the testrun JSON has changed*' in the previous section.

### Procedure for deleting a testrun

TODOXXX Since old Git commits are not deleted, data must be marked as obsolete, and the repo must be garbage-collected later.

## Configuration file

TODOXXX

## Utilities for repo management

- TODOXXX Garbage-collect updated/deleted testruns. Options: create a new repo and copy surviving testruns to it OR use git-rewrite-branch technology.
- TODOXXX Copy testruns from one repo to another. Options: additionally, redo the parsing procedure AND/OR move testruns to a different project.
- ...
