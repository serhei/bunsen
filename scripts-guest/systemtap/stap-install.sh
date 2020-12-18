#!/bin/bash
# Install SystemTap, e.g.
# $ git clone git://sourceware.org/git/bunsen.git bunsen
# $ time ./bunsen-internal/scripts-guest/systemtap/stap-install.sh --arch32=i686
#
# Or, via curl:
# $ sudo bash -c 'curl https://sourceware.org/git/?p=bunsen.git;a=blob_plain;f=scripts-guest/systemtap/stap-install.sh;hb=HEAD | bash -'

# Default arguments for testing:
CHECKOUT_DIR=/opt/stap-checkout
ARCH32=

usage()
{
    echo "Usage: $(basename "$0") [--checkout-location <path>] [--arch32 <arch>]"
    exit 2
}

PARSED_ARGUMENTS=$(getopt -a -n stap-install -o l: --long checkout-location:,arch32: -- "$@")
VALID_ARGUMENTS=$?
if [ "$VALID_ARGUMENTS" != "0" ]; then
    usage
fi

eval set -- "$PARSED_ARGUMENTS"
while :
do
    case $1 in
        -l | --checkout-location) CHECKOUT_DIR=$2; shift 2;;
        --arch32) ARCH32=$2; shift 2;;
        --) shift; break;;
        *) echo "Unexpected option: $1"; usage;;
    esac
done

ARCH64=`uname -m`
if [ -n "$ARCH32" ]; then
    # TODOXXX: Automatically identify arch32.
    # XXX: Newer distros exclude the 32-bit packages anyways.
    case $ARCH64 in
        x86_64) ARCH32=i686;;
        ppc64le) ARCH32=ppc;;
        aarch64) ARCH32=armv7hl;;
        s390x) ARCH32=s390;;
        *) echo "WARNING: Unknown arch $ARCH64, will skip 32-bit testing."
           ARCH32=unknown;;
    esac
fi

YUM=yum
command -v $YUM >/dev/null 2>&1 || YUM=dnf
$YUM group install -y "Development Tools"
$YUM install -y yum-utils git vim screen kernel-debug kernel-debug-devel elfutils-devel
$YUM install -y expect dejagnu
$YUM install -y python-devel python-virtualenv
$YUM install -y kernel-rt-debug kernel-rt-debug-devel
$YUM install -y kernel-headers
$YUM install -y gcc-x86_64-linux-gnu
$YUM install -y libstdc++-devel.x86_64 # rawhide thing missed by yum-builddep?
$YUM install -y libgcc.$ARCH32 glibc-devel.$ARCH32 libstdc++-devel.$ARCH32

mkdir -p $(dirname $CHECKOUT_DIR)
git clone git://sourceware.org/git/systemtap.git $CHECKOUT_DIR
pushd $CHECKOUT_DIR
# TODO: Could probably skip the following as debuginfo is ensured by stap-test
# and could rely on debuginfod.elfutils.org rather than debuginfo packages:
#
# debuginfo-install -y kernel || dnf debuginfo-install -y kernel
# debuginfo-install -y kernel-rt || dnf debuginfo-install -y kernel-rt
# debuginfo-install -y kernel-debug || dnf debuginfo-install -y kernel-debug
# debuginfo-install -y kernel-rt-debug || dnf debuginfo-install -y kernel-rt-debug
# debuginfo-install -y coreutils || dnf debuginfo-install -y coreutils
# $YUM install -y kernel-debuginfo
# bash ./stap_prep
dnf builddep -y systemtap.spec || yum-builddep -y systemtap.spec
popd
