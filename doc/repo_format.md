# Bunsen repository format

A Bunsen repository is a directory containing the following:
- `config`, a configuration file in INI format.
- `scripts`, `scripts-*`: optional directories containing analysis scripts.
- `cache`, a directory with cached analysis results. <!-- TODO -->
- `bunsen.git`, a bare Git repo laid out according to the format described below.
- `bunsen.lock`, a lockfile controlling access to `bunsen.git`. <!-- TODO -->

TODO: terminology note in systemtap-2018-06, 'tag' is systemtap-2018-06-extra-label, 'project' is systemtap, 'year_month' is 2018-06, 'extra_label' is extra-label

TODO: terminology -- hunt down 'testsuite run'
TODO: test results, log files, testsuite runs

TODO: summarize the maintenance procedures in a different doc

TODO: switch to RST docs??

## `config`

... TODO ...

## `scripts`, `scripts-*`

... TODO ...

## `bunsen.git`

The Bunsen Git repo `bunsen.git` includes branches which store the original log files and branches which store JSON index files. Each set of log files added to the Bunsen repo results also results in one or more *testrun* entries being added to the JSON index files.

### Projects

Every testrun in the Bunsen Git repo belongs to a named *project*. We may want to store testruns in different projects when they were produced by testing different software codebases, or when the test result data for the same software codebase was obtained from different origins.

For example:
- A Bunsen Git repo for [GDB test results](https://gdb-buildbot.osci.io/) might store testruns belonging to a project named `gdb`.
- A Bunsen Git repo storing test results from the [GCC Jenkins server](<!--TODO-->) might store testruns belonging to different projects corresponding different components of the GCC toolchain, e.g. `gcc`, `g++`, `ld`, and so forth.
- A Bunsen Git repo for the SystemTap project might include a `systemtap` project for completed testsuite runs from the SystemTap Buildbot, a `systemtap-contrib` project for testsuite runs provided by testers in the community, and a `systemtap-crash` project for SystemTap Buildbot testsuite runs that resulted in a kernel crash (and are therefore incomplete).

### Testsuite Runs

Each set of test results in the Bunsen Git repo is uniquely identified by a `bunsen_commit_id`, which is the hexsha of a commit in the `bunsen` Git repo.

For each testrun in a project `<project>`, the Bunsen Git repo stores data as follows:
- **On branch `<project>/testlogs-<year>-<month>`, commit `<bunsen_commit_id>`:** the original test log files (e.g. DejaGNU `.log` and `.sum`). <!-- TODO The commit message includes a brief JSON summary of the testrun. -->
- **On branch `index`, latest commit, within the file `<project>-<year>-<month>.json`:** a brief JSON summary of the testrun.
- **On branch `<project>/testruns-<year>-<month>[-<extra_label>]`, latest commit, within the file `<project>-<bunsen_commit_id>.json`:** a full JSON representation of the testrun.
  * `<extra_label>` is an *optional* tag which can be used to split a large testruns branch into several smaller branches, in order to limit the size of a checked-out working copy of the Bunsen Git repo. The exact content of `<extra_label>` is not important (for example, we could generate `<extra_label>` based on the `arch` and `osver` fields to store testruns split by architecture and OS/distribution).

### Testrun Representation

The JSON summary of a testrun is a JSON dict. The following fields are required:
- `bunsen_version`: The version of Bunsen used to generate this testrun.
- `bunsen_commit_id`: The hexsha of the commit storing the test log files.
- `bunsen_testlogs_branch`: The name of the branch storing the test log files.
- `bunsen_testruns_branch`: The name of the branch storing the full representation of this testrun. <!-- TODO: determines project, year_month, extra_label -->

The following additional fields may be included:
- `related_testruns_branches`: The names of branches storing related testruns in other projects. <!-- TODOXXX add to docstring -->
- `source_commit_id` <!--TODO WAS `source_commit`-->
- `timestamp`: Usually, this stores the date and time of the testsuite run. If the date and time could not be obtained, the parser may populate this field with any of: the timestamp of the source commit being tested, the date and time that the testsuite run was downloaded, or the date and time that the testsuite was added to the Bunsen repository.
- `version`: The version of the software codebase being tested.
- `origin_host`: Hostname or other identifier for the machine where these test results were obtained.
- `pass_count`, `fail_count`: Number of passing and failing testcases, respectively.
- `problems`: Only included if the testrun failed validation by the parser. Documents any missing/unknown important fields. This allows log files that could not be parsed correctly to be added to the repository and parsed again later.

All other fields in the testrun summary are considered to describe the system configuration, <!--TODO--> and will be treated in that sense by Bunsen analysis scripts. Typical configuration fields are `arch` and `osver` giving the hardware architecture and operating system distribution under which the test results were obtained. For software codebases with complex dependencies, it may be necessary to include additional configuration fields such as `kernel_version`, `gcc_version`, `elfutils_version`.

The full JSON representation of a testrun is a JSON dict containing the same fields as the JSON summary, plus a field `testcases` containing an array of JSON dicts representing testcases.

... TODO ...

...
