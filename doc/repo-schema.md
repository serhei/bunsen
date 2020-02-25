# Bunsen repository format

A valid Bunsen repo is a directory containing the following:
- `bunsen.git`, a bare Git repo laid out according to a format described below.
- `config`, a configuration file in INI format.
- TODOXXX Optional scripts.

## Projects

The Bunsen Git repo `bunsen.git` stores testruns for one or more projects. For example, a Bunsen Git repo for GDB test results would contain a project named `gdb`. 

We may want to separate testruns into different projects according to different software codebases being tested, or according to different test result origins for the same software codebase.

For example, we could store GDB test results originating from a buildbot in `gdb-buildbot`, GDB test results submitted manually by contributors in `gdb-misc`, and so forth.

## Bunsen Git repo layout and index format

Each testrun in the Bunsen Git repo is uniquely identified by a `bunsen_commit_id`, which is the hexsha of a commit in the `bunsen` Git repo.

For each testrun in a project `<project>`, the Bunsen Git repo stores data as follows:
- On branch `<project>/testlogs-<year>-<month>`, commit `<bunsen_commit_id>`: the original DejaGNU `.log` and `.sum` files (together with any additional test logs). The commit message includes a brief JSON summary of the testrun.
- On branch `index`, latest commit, within the file `<project>-<year>-<month>.json`: a brief JSON summary of the testrun.
- On branch `<project>/testruns-<year>-<month>`, latest commit, within the file `<project>-<bunsen_commit_id>.json`: a full JSON representation of the testrun.

### JSON representation of testruns

TODOXXX Format for JSON summary, esp. required fields:
- `year_month`

TODOXXX Add code to validate testrun.

TODOXXX Format for full JSON representation.

TODOXXX Format for other JSON artefacts: diffs, 2nd-order diffs, etc.

### Procedure for adding a testrun

Consider the following:
- TODOXXX Case 1: testlogs and testrun is not present in the repo.
- TODOXXX Case 2: identical testlogs are present, but testrun is different.
- TODOXXX Case 3: identical testlogs and testrun are present.

### Procedure for updating a testrun

TODOXXX

### Procedure for deleting a testrun

TODOXXX Since old Git commits are not deleted, data must be marked as obsolete.

## Configuration file

TODOXXX

## Utilities for repo management

- TODOXXX Garbage-collect updated/deleted testruns. Options: create a new repo and copy surviving testruns to it OR use git-rewrite-branch technology.
- TODOXXX Copy testruns from one repo to another. Options: additionally, redo the parsing procedure AND/OR move testruns to a different project.
- ...
