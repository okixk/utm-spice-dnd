#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
BUILD_ROOT="$SCRIPT_DIR/.build"
PACKAGES="$BUILD_ROOT/packages"
SYSROOT="$BUILD_ROOT/sysroot"
SOURCES="$BUILD_ROOT/src"
BUILD_DIR="$BUILD_ROOT/build/spice-gtk-0.42"
PREFIX="$BUILD_ROOT/prefix"
RUNTIME_LIB="$PREFIX/lib/runtime"
VIEWER_ROOT="$BUILD_ROOT/virt-viewer-root"

SPICE_ARCHIVE=spice-gtk_0.42.orig.tar.xz
SPICE_SHA256=9380117f1811ad1faa1812cb6602479b6290d4a0d8cc442d44427f7f6c0e7a58
SPICE_URL=http://archive.ubuntu.com/ubuntu/pool/universe/s/spice-gtk/$SPICE_ARCHIVE
VIRTMAN_ARCHIVE=virt-manager_5.1.0.orig.tar.xz
VIRTMAN_SHA256=ccfc44b6c1c0be8398beb687c675d9ea4ca1c721dfb67bd639209a7b0dec11b1
VIRTMAN_URL=http://archive.ubuntu.com/ubuntu/pool/universe/v/virt-manager/$VIRTMAN_ARCHIVE
VIEWER_DEB=virt-viewer_11.0-4_amd64.deb
VIEWER_SHA256=58fdd849369cfc80c2d393911ed2478436c94fdeb4728bb57ae196f892dca30f
VIEWER_URL=http://archive.ubuntu.com/ubuntu/pool/universe/v/virt-viewer/$VIEWER_DEB

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

assert_unmounted_tree() {
    directory=$1
    if ! python3 - "$directory" <<'PY'
import os
import re
import sys


def decode_mount_path(field):
    return re.sub(
        rb"\\([0-7]{3})",
        lambda match: bytes((int(match.group(1), 8),)),
        field,
    )


root = os.fsencode(os.path.realpath(sys.argv[1]))
prefix = root + (b"" if root == b"/" else b"/")

try:
    with open("/proc/self/mountinfo", "rb") as mountinfo:
        for line in mountinfo:
            fields = line.split(b" - ", 1)[0].split()
            if len(fields) < 5:
                raise ValueError("malformed mountinfo record")
            mount_path = decode_mount_path(fields[4])
            if mount_path == root or mount_path.startswith(prefix):
                raise SystemExit(1)
except (OSError, ValueError):
    raise SystemExit(2)
PY
    then
        fail "refusing mounted path or nested mount under: $directory"
    fi
}

[ ! -L "$BUILD_ROOT" ] || fail "refusing symlinked build root: $BUILD_ROOT"
mkdir -p "$BUILD_ROOT"
SCRIPT_REAL=$(realpath -e "$SCRIPT_DIR")
BUILD_ROOT_REAL=$(realpath -e "$BUILD_ROOT")
[ "$BUILD_ROOT_REAL" = "$SCRIPT_REAL/.build" ] || \
    fail "build root resolves outside the host development directory"

for command in apt-get cc curl dpkg-architecture dpkg-deb find meson ninja patch pkg-config python3 realpath sha256sum tar; do
    command -v "$command" >/dev/null 2>&1 || fail "required command is missing: $command"
done

assert_unmounted_tree "$BUILD_ROOT_REAL"

case $(dpkg --print-architecture 2>/dev/null || true) in
    amd64) ;;
    *) fail "this development installer currently supports Ubuntu/Debian amd64 only" ;;
esac

mkdir -p "$PACKAGES" "$SYSROOT" "$SOURCES" "$BUILD_ROOT/build" "$PREFIX" "$VIEWER_ROOT"

download() {
    url=$1
    output=$2
    expected=$3
    if [ ! -f "$output" ]; then
        curl --fail --location --proto '=http,https' --tlsv1.2 --output "$output" "$url"
    fi
    actual=$(sha256sum "$output" | awk '{print $1}')
    [ "$actual" = "$expected" ] || fail "checksum mismatch for $output"
}

reset_dir() {
    directory=$1
    case $directory in
        "$BUILD_ROOT"/*) ;;
        *) fail "refusing to clear path outside $BUILD_ROOT: $directory" ;;
    esac
    [ ! -L "$directory" ] || fail "refusing symlinked build directory: $directory"
    mkdir -p "$directory"
    resolved=$(realpath -e "$directory")
    case $resolved in
        "$BUILD_ROOT_REAL"/*) ;;
        *) fail "refusing to clear resolved path outside $BUILD_ROOT_REAL: $resolved" ;;
    esac
    assert_unmounted_tree "$resolved"
    find "$resolved" -xdev -mindepth 1 -delete
}

# Ubuntu's runtime is left untouched. These packages are downloaded and
# unpacked as headers/linker symlinks only when the corresponding development
# metadata is absent from the host.
LOCAL_DEBS="python3-six libspice-protocol-dev libusbredirparser-dev libusbredirhost-dev libusbredirparser1t64 libusbredirhost1t64 libusb-1.0-0-dev libusb-1.0-0"
pkg-config --exists gtk+-3.0 || LOCAL_DEBS="$LOCAL_DEBS libgtk-3-dev libgtk-3-0t64 libatk-bridge2.0-dev libatk1.0-dev libatk1.0-0t64 libatspi2.0-dev libdbus-1-dev libxres-dev libxtst-dev"
pkg-config --exists libjpeg || LOCAL_DEBS="$LOCAL_DEBS libjpeg-dev libjpeg8-dev libjpeg8 libjpeg-turbo8-dev libjpeg-turbo8"

reset_dir "$SYSROOT"
for package in $LOCAL_DEBS; do
    if ! find "$PACKAGES" -maxdepth 1 -name "${package}_*.deb" -print -quit | grep -q .; then
        (cd "$PACKAGES" && apt-get download "$package")
    fi
    package_file=$(find "$PACKAGES" -maxdepth 1 -type f -name "${package}_*.deb" -print -quit)
    [ -n "$package_file" ] || fail "downloaded package is missing: $package"
    actual_package=$(dpkg-deb -f "$package_file" Package)
    [ "$actual_package" = "$package" ] || \
        fail "unexpected package in cache for $package: $actual_package"
    dpkg-deb -x "$package_file" "$SYSROOT"
done
if [ -f "$SYSROOT/usr/include/$(dpkg-architecture -qDEB_HOST_MULTIARCH)/jconfig.h" ]; then
    cp "$SYSROOT/usr/include/$(dpkg-architecture -qDEB_HOST_MULTIARCH)/jconfig.h" "$SYSROOT/usr/include/jconfig.h"
fi

download "$SPICE_URL" "$PACKAGES/$SPICE_ARCHIVE" "$SPICE_SHA256"
download "$VIRTMAN_URL" "$PACKAGES/$VIRTMAN_ARCHIVE" "$VIRTMAN_SHA256"
download "$VIEWER_URL" "$PACKAGES/$VIEWER_DEB" "$VIEWER_SHA256"

reset_dir "$SOURCES"
tar -xf "$PACKAGES/$SPICE_ARCHIVE" -C "$SOURCES"
tar -xf "$PACKAGES/$VIRTMAN_ARCHIVE" -C "$SOURCES"
patch -p1 -d "$SOURCES/spice-gtk-0.42" < "$SCRIPT_DIR/patches/spice-gtk-0.42-semantic-dnd.patch"

reset_dir "$BUILD_DIR"
reset_dir "$PREFIX"

MULTIARCH=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
LOCAL_PKGCONFIG="$SYSROOT/usr/lib/$MULTIARCH/pkgconfig:$SYSROOT/usr/share/pkgconfig"
SYSTEM_PKGCONFIG="/usr/lib/$MULTIARCH/pkgconfig:/usr/share/pkgconfig"
LOCAL_INCLUDES="-I$SYSROOT/usr/include -I$SYSROOT/usr/include/spice-1 -I$SYSROOT/usr/include/libusb-1.0 -I$SYSROOT/usr/include/gtk-3.0 -I$SYSROOT/usr/include/atk-1.0 -I$SYSROOT/usr/include/at-spi2-atk/2.0 -I$SYSROOT/usr/include/at-spi-2.0 -I$SYSROOT/usr/lib/$MULTIARCH/gtk-3.0/include"

env \
    PKG_CONFIG_PATH="$LOCAL_PKGCONFIG:$SYSTEM_PKGCONFIG" \
    PYTHONPATH="$SYSROOT/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}" \
    CFLAGS="$LOCAL_INCLUDES${CFLAGS:+ $CFLAGS}" \
    LDFLAGS="-L$SYSROOT/usr/lib/$MULTIARCH${LDFLAGS:+ $LDFLAGS}" \
    meson setup "$BUILD_DIR" "$SOURCES/spice-gtk-0.42" \
        --prefix "$PREFIX" --libdir lib --buildtype debugoptimized \
        -Dgtk=enabled -Dintrospection=disabled -Dvapi=disabled \
        -Dusbredir=enabled -Dsmartcard=disabled -Dpolkit=disabled \
        -Dlibcap-ng=disabled -Dwebdav=disabled -Dgtk_doc=disabled \
        -Degl=enabled -Dsasl=disabled -Dopus=disabled -Dlz4=disabled \
        -Dbuiltin-mjpeg=false

env PYTHONPATH="$SYSROOT/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}" \
    meson compile -C "$BUILD_DIR"
env PYTHONPATH="$SYSROOT/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}" \
    meson test -C "$BUILD_DIR" --print-errorlogs
env PYTHONPATH="$SYSROOT/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}" \
    meson install -C "$BUILD_DIR"

# Keep the runtime half of locally extracted build dependencies private too.
# The development launchers prepend this directory, so USB redirection does not
# accidentally depend on equivalent packages already being installed in /usr.
mkdir -p "$RUNTIME_LIB"
for soname in libusbredirhost.so.1 libusbredirparser.so.1 libusb-1.0.so.0; do
    source_library="$SYSROOT/usr/lib/$MULTIARCH/$soname"
    [ -f "$source_library" ] || fail "private runtime library is missing: $source_library"
    cp -L -- "$source_library" "$RUNTIME_LIB/$soname"
done

reset_dir "$VIEWER_ROOT"
dpkg-deb -x "$PACKAGES/$VIEWER_DEB" "$VIEWER_ROOT"

[ -x "$SOURCES/virt-manager-5.1.0/virt-manager" ] || fail "virt-manager source launcher is missing"
[ -x "$VIEWER_ROOT/usr/bin/virt-viewer" ] || fail "virt-viewer development binary is missing"
[ -f "$PREFIX/lib/libspice-client-gtk-3.0.so.5" ] || fail "private spice-gtk library is missing"
for soname in libusbredirhost.so.1 libusbredirparser.so.1 libusb-1.0.so.0; do
    [ -f "$RUNTIME_LIB/$soname" ] || fail "private runtime library is missing: $soname"
done

printf '%s\n' \
    "Installed isolated Linux-host development build." \
    "  spice-gtk source: 0.42 / f04479c16f0969fb394ebe74b6eff74e560a42f0" \
    "  virt-manager source: 5.1.0 / eb4898b19e550af19daea49ae5ed15d2d70a2fc4" \
    "  virt-viewer binary: Ubuntu 11.0-4 (source base 8d8923d259b79c7c9ad3560a49be81dc0f38b88b)" \
    "  private prefix: $PREFIX" \
    "No file under /usr was modified."
