# Bunsen Tutorial #1: Create the Repo, Add Logs Manually, Show Testruns, Analyze Test Results

Bunsen is an experimental toolkit that can compress and analyze a collection of
test results from a DejaGNU testsuite. This tutorial shows how to get started
with Bunsen. We'll use some example test results from the GDB project.

## Create the Repository

Download Bunsen and install required Python libraries:

    git clone git://sourceware.org/git/bunsen.git bunsen-gdb
    cd bunsen-gdb
    pip3 install --user -r requirements.txt

Initialize a Bunsen repository:

    ./bunsen.py init

The command `./bunsen.py init` creates a Bunsen repository in the hidden folder
`.bunsen` under `bunsen-gdb/`.

Next, check out the Git repository of project we will be testing. Bunsen will
use this Git repository to check the list of commits in the project:

    git clone git://sourceware.org/git/binutils-gdb.git upstream-binutils-gdb

Next edit the configuration file `.bunsen/config`:

    nano .bunsen/config

Within `.bunsen/config`, configure the following settings:

    [core]
    	project=gdb
    [project "gdb"]
    	source_repo=/path/to/upstream-binutils-gdb
    	gitweb_url=https://sourceware.org/git/?p=binutils-gdb.git
    [bunsen-upload]
    	manifest=README.txt,gdb.sum*,gdb.log*
        commit_module=gdb.commit_logs

- `project` is a tag identifying the default project for which we are storing
  test results. It can be convenient to store different types of test results
  under different project names. For example, for SystemTap testing we store
  normal test results under a project named `systemtap` and test results where
  the testsuite did not finish running all of the way through under a project
  named `systemtap-incomplete`.
- `source_repo` gives the path to where you previously checked out the
  `binutils-gdb` Git repository.
- `gitweb_url` gives the URL of a website showing the contents of the
  `binutils-gdb` repository. Bunsen will link to this from the HTML analysis
  output.
- `manifest` lists the possible names of the test result log files that will be
  added to the Bunsen repository.
- `commit_module` identifies the module that will be used to process the test
  result log files and add them to the Bunsen repository. In this case we will
  use `scripts-main/gdb/commit_logs.py`.

*Optional:* Because Bunsen needs an up-to-date copy of the project's Git
repository, you have to run `git pull` periodically. One way to do that is to
define a Cron job. (On Fedora you need to install the `cronie` package for
this.) Run `crontab -e` to open your Crontab file and add the following line:

    @hourly cd /path/to/upstream-binutils-gdb && git pull

## Download and Add Test Results

*Alternative 1:* If you don't want to go through the *very long* process of adding
test results, you can set up Bunsen with a pre-made repository of GDB test
results:

    mv .bunsen/config oldconfig
    rm -rf .bunsen # DELETES ANY EXISTING DATA
    git clone --bare https://github.com/serhei/bunsen-gdb-sample-data .bunsen/bunsen.git
    ./bunsen.py init
    mv oldconfig .bunsen/config

*Alternative 2:* (If you want to try building a large repository yourself.)
For this example, we'll use `wget` to download some test results from the [old
GDB buildbot](https://gdb-buildbot.osci.io) I saved at
http://51.15.49.203/results/ and use them to build a repository. (Below,
we'll learn how to send test results directly to Bunsen from a machine running
the GDB testsuite.)

Start by downloading the test results:

    mkdir test-results && pushd test-results
    time wget -r --no-parent -nH --cut-dirs=2 -A '*.tbz' http://51.15.49.203/results/
    tar xvjf Fedora-i686.tbz
    tar xvjf Fedora-x86_64-m64.tbz
    tar xvjf Fedora-x86_64-native-extended-gdbserver-m64.tbz
    tar xvjf Fedora-x86_64-native-gdbserver-m64.tbz
    popd

Use the `+gdb/commit_logs` script to add the results to the repo:

    time ./bunsen.py +gdb/commit_logs ./test-results

You can also use the `bunsen-add.py` script to commit a single set of results:

    tar xvzf - test-results/Fedora-i686/80/809a0c354b97bbbcacbd99808f0e328b39614a8f/gdb.* test-results/Fedora-i686/80/809a0c354b97bbbcacbd99808f0e328b39614a8f/README.txt | ./bunsen-add.py tar=-

To find and add the test results, `bunsen add` will use the script we defined
earlier in `.bunsen/config`: `scripts-main/gdb/commit_logs.py`. The
`commit_logs.py` script, in turn, uses the DejaGNU log parser in
`scripts-main/gdb/parse_dejagnu.py`.

After being added, the test results are stored in a Git repository
`.bunsen/bunsen.git`. Bunsen includes a collection of scripts to extract and
analyze these results under `scripts-main/`.

## Show Test Results

List the test results you just added:

    ./bunsen.py +list_commits | less

You'll see output like this:

    commit_id: 117eb594228cf5447e49475e4fb33480c1f717a7 
    summary: [gdb/testsuite] Fix gdb.base/break-interp.exp timeout with check-read1
    * 2019-08 603f226... pass_count=53242 fail_count=303 arch=i686 osver=Fedora-i686
    * 2019-08 f406d0a... pass_count=55064 fail_count=411 arch=x86_64 osver=Fedora-x86_64-m32
    * 2019-08 3f5a9b4... pass_count=55436 fail_count=1288 arch=x86_64 osver=Fedora-x86_64-m64
    * 2019-08 0fe3a16... pass_count=51782 fail_count=241 arch=aarch64 osver=Ubuntu-Aarch64-m64
    
    commit_id: ed5913402bd4d50e342d4350ee5e4662d98a3947 
    summary: [gdb/testsuite] Fix gdb.base/signals.exp timeout with check-read1
    * 2019-08 03e8361... pass_count=49724 fail_count=375 arch=i686 osver=Fedora-i686
    * 2019-08 298b16c... pass_count=54978 fail_count=399 arch=x86_64 osver=Fedora-x86_64-m32
    * 2019-08 7113054... pass_count=55431 fail_count=1291 arch=x86_64 osver=Fedora-x86_64-m64
    * 2019-08 d9aa671... pass_count=51781 fail_count=244 arch=aarch64 osver=Ubuntu-Aarch64-m64

The `commit_id` and `summary` fields identify a commit in the `binutils-gdb`
repo. Below each commit is a list of test results from when that commit was
tested on different hardware architectures and distributions. Each set of test
results is prefixed by its `bunsen_commit_id`, a hexadecimal string. This is the
commit ID under which the test results are stored in `.bunsen/bunsen.git`. For
example, the `bunsen_commit_id` of when "[gdb/testsuite] Fix
gdb.base/break-interp.exp timeout with check-read1" was tested on
`Ubuntu-Aaarch64` is `0fe3a16`.

Show the log files from a particular set of test results:

    ./bunsen.py +show_logs 0fe3a16 | less
    ./bunsen.py +show_logs 0fe3a16 gdb.sum | less

The first command shows all files from that set of test results,
the second command shows only `gdb.sum`.

## Generate `grid_view` Analysis

To visualize a large set of test results, we use the `+grid_view` script.
It displays a grid of results for a range of commits. To generate a grid
view for all of the commits in a particular range, use the `+list_commits`
output to pick a starting ('baseline') and ending ('latest') commit
and invoke the `+grid_view` analysis with the following command:

    ./bunsen.py +grid_view latest=fdd5026 baseline=6d5554a >results-summary-big.html

The results can be viewed in a web browser:

    firefox ./results-summary.html

The grid is chronologically ordered with earlier commits on the right. If you
see a commit for which a lot of testcases started failing, you can click on the
commit ID in the header of the table to see the details.

You can produce a clickable view whose results include the particular subtests
which failed by adding the `show_subtests` option:

    ./bunsen.py +grid_view latest=fdd5026 baseline=6d5554a show_subtests=yes >results-summary-big.html

In the resulting file, you can click on cells in the grid that contain failures
to see the list of subtests which failed.

Note that an HTML file produced with `key=yes` will be much larger. If you want
a smaller file, you could specify a particular subset of the testcases with the
`key` option:

    ./bunsen.py +grid_view latest=fdd5026 baseline=6d5554a show_subtests=yes key='*non-ldr*' >results-summary-slice.html

This will output a grid of results only for the testcases whose name contains
the string 'non-ldr'.

For a more complete example of `+grid_view` output see [these examples from the
SystemTap project](http://51.15.49.203/bunsen-examples/).

## Set up the Web Server to accept test results

To assemble a Bunsen repository with results from your own testing of GDB,
the most convenient way is to set up a web server that will accept
test results from your different test machines.

    dnf install lighttpd
    cp bunsen-lighttpd.conf.example bunsen-lighttpd.conf
    nano bunsen-lighttpd.conf

In the file `bunsen-lighttpd.conf`, replace `/path/to/bunsen-checkout`
to the location where you checked out Bunsen. Then launch the web server:

    lighttpd -D -f ./bunsen-lighttpd.conf

This launches a web server on port 8013 which allows test results to be uploaded
via CGI. On the test machine, after you are done testing GDB, you can send your
results to Bunsen with `curl`:

     tar cvzf - gdb.log gdb.sum README.txt | curl -X POST -F project=gdb -F 'tar=@-' http://<location of your server>:8013/bunsen-upload.py

**TODO**: This method also requires a way to identify the commit ID of the
`binutils-gdb` commit being tested. You can provide this in a file `README.txt`
with the following format:

    === README ===

    Logs for: <id of the commit your were testing>

Generate this file and include it in the uploaded test results.
