#!/bin/bash

# SystemTap testing automation suited for manual or buildbot invocation
set -o xtrace

# usage test_stap.sh tag path/to/stap/checkout desired-version path/to/logs
if [ $# -ne 4 ]; then
    echo usage test_stap.sh tag path/to/stap/checkout desired-version path/to/logs
    echo - path/to/stap/checkout should be a Git checkout of systemtap
    echo - desired-version should be a branch, revision, or tag to check out
    echo - path/to/logs indicates where to save the logs
    exit 127
fi
TAG=$1
STAPPATH=$2
DESIRED_VERSION=$3
LOGPATH=$4

STAP_GIT=`realpath $STAPPATH || readlink -e $STAPPATH`
STAP_BUILD=$STAP_GIT/stap_build
STAP_INSTALL=$STAP_GIT/stap_install
#rm -rf $STAP_BUILD # will be preserved instead
rm -rf $STAP_INSTALL
mkdir -p $STAP_BUILD
mkdir -p $STAP_INSTALL

# (0) preserve previous build directory in case a run aborts
rm -rf $STAP_GIT/stap_build.backup
cp -r $STAP_BUILD $STAP_GIT/stap_build.backup

# (1) update RPM/DPKGs and check out correct version of systemtap
pushd $STAP_GIT
FOUND_DEBUGINFO=probably
# only with newer elfutils:
#export DEBUGINFOD_URLS=https://debuginfod.systemtap.org/ # XXX old initial test server
export DEBUGINFOD_URLS="https://debuginfod.elfutils.org/"
export DEBUGINFOD_TIMEOUT=300
if [[ -z $(which debuginfod-find) ]] || ! debuginfod-find debuginfo $(eu-readelf -n /boot/vmlinuz-`uname -r` | grep Build.ID | awk '{print $3}'); then
    FOUND_DEBUGINFO=
    echo debuginfo not found, enabling stap-prep and debuginfo-install
    # XXX clear DEBUGINFOD_URLS so that stap-prep will use debuginfo-install
    export DEBUGINFOD_URLS=
    ./stap-prep
fi

if [[ -n $(which debuginfod-find) && -n $(which rpm) && -n $FOUND_DEBUGINFO ]]; then
    yum erase -y kernel-debuginfo
fi
# only on RPM distros, without debuginfod:
FOUND_DEBUGINFO=$(rpm -qa | grep kernel-debuginfo-$(uname -r))
if [[ -z $(which debuginfod-find) && -n $(which rpm) && -z $FOUND_DEBUGINFO ]]; then
	# TODO: erase old kernel-debuginfo if disk space is low
	echo Found debuginfo is newer than running kernel, rebooting....
	dnf upgrade || yum upgrade
	reboot || /usr/sbin/reboot || /usr/sbin/shutdown -r now
fi
# when rawhide kernel-devel was getting broken (missing autoconf.h)
# XXX the problem was later determined to be out-of-date makefiles;
# see systemtap commit 21109b093
if [[ -n $(which rpm) && -n $(rpm -V --nomtime kernel-devel kernel-debug-devel) ]]; then
    yum -y remove kernel-devel
    yum -y install kernel-devel
    # TODOXXX: Separate out kernel-debug-devel verification & reinstall.
    # Mostly we get spurious reinstalls of kernel-devel when debug-devel is not even present.
    #yum -y remove kernel-debug-devel
    #yum -y install kernel-debug-devel
    # reboot if kernel-devel is still outdated
    rpm -V --nomtime kernel-devel || reboot || /usr/sbin/reboot || /usr/sbin/shutdown -r now
    # TODO: only activate if kernel-debug-devel is actually installed
    #rpm -V --nomtime kernel-debug-devel || reboot || /usr/sbin/reboot || /usr/sbin/shutdown -r now
fi
# also need to reboot if kernel-devel is newer than the running kernel
RPM_UNAME=`uname -r`
RPM_KERNEL_DEVEL=kernel-devel
if [[ "$RPM_UNAME" == *+debug ]];
then
    RPM_KERNEL_DEVEL=kernel-debug-devel
    RPM_UNAME=${RPM_UNAME/+debug/}
fi
if [[ -n $(which rpm) && -z $(rpm -qa | grep $RPM_KERNEL_DEVEL-$RPM_UNAME) ]];
then
    echo No $RPM_KERNEL_DEVEL package matching running kernel, update+reboot....
    dnf upgrade || yum upgrade
    reboot || /usr/sbin/reboot || /usr/sbin/shutdown -r now
fi
# TODO: Could do the same for kernel-debuginfo only if we ran stap-prep....

git checkout .
# TODOXXX: older distros don't have these git options ??
git pull --ff-only
git checkout $DESIRED_VERSION
#git checkout fche/uprobes-rework
git pull --ff-only

# for the sake of completeness
if [[ -n $(which rpm) ]];
then
	dnf builddep -y systemtap.spec || yum-builddep -y systemtap.spec
fi
popd

# (2) build it
pushd $STAP_BUILD
make distclean
# TODO: skip the dejazilla step entirely?
# for older revisions than 44da61d78: $STAP_GIT/configure --prefix=$STAP_INSTALL --enable-dejazilla
$STAP_GIT/configure --prefix=$STAP_INSTALL --enable-dejazilla=http://web.elastic.org/~dejazilla/upload.php
make all || exit 1
make install || exit 1
# TODO: try smoketest first?
# TODO: dmesg -w is not available on RHEL6
dmesg -wH >testsuite/systemtap.dmesg &
DMESG_PID=$!
# TODO: verify that we won't kill something random if dmesg fails to start
TEST_FAILED=0
make installcheck RUNTESTFLAGS="-v" || TEST_FAILED=1
#service systemtap stop # encountered residual problems on a RHEL6-32 configuration
popd

# (3) collect the logs
LOGTAG=$TAG.`uname -r`.`date -Iminutes` # TODO: does not play well with scp
# TODO: LOGTAG includes stap version
$STAP_INSTALL/bin/stap-report >$LOGPATH/stap-report.$LOGTAG
cp $STAP_BUILD/testsuite/systemtap.log $LOGPATH/systemtap.log.$LOGTAG
cp $STAP_BUILD/testsuite/systemtap.sum $LOGPATH/systemtap.sum.$LOGTAG
cp $STAP_BUILD/testsuite/systemtap.dmesg $LOGPATH/systemtap.dmesg.$LOGTAG

# (4) OPTIONAL -- send the logs to bunsen-push
BUNSEN_URL=
if [[ -n $BUNSEN_URL ]];
then
	tar cvzf - $LOGPATH/* | curl -X POST -F 'project=systemtap' -F 'tar=@-' $BUNSEN_URL
fi
# TODOXXX Save the output so we know the logs don't need to be retried.
# TODO exit1 if the upload failed?

# exit cleanly
echo looking for dmesg
ps aux | grep dmesg
if [[ -z $( ps aux | grep dmesg | grep $DMESG_PID ) ]] ; then
    echo not found
else
    kill -9 $DMESG_PID
fi
if [ $TEST_FAILED -ne 0 ]; then
       exit 1
fi       
exit 0
