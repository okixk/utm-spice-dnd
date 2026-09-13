#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PREFIX="$SCRIPT_DIR/.build/prefix"
RUNTIME_LIB="$PREFIX/lib/runtime"
VIEWER="$SCRIPT_DIR/.build/virt-viewer-root/usr/bin/virt-viewer"

if [ ! -x "$VIEWER" ] || [ ! -f "$PREFIX/lib/libspice-client-gtk-3.0.so.5" ]; then
    printf 'error: development build missing; run %s/install.sh first\n' "$SCRIPT_DIR" >&2
    exit 1
fi
for soname in libusbredirhost.so.1 libusbredirparser.so.1 libusb-1.0.so.0; do
    if [ ! -f "$RUNTIME_LIB/$soname" ]; then
        printf 'error: private runtime library is missing: %s; run %s/install.sh again\n' \
            "$soname" "$SCRIPT_DIR" >&2
        exit 1
    fi
done

export LD_LIBRARY_PATH="$PREFIX/lib:$RUNTIME_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export SPICE_DEBUG="${SPICE_DEBUG:-1}"
exec "$VIEWER" "$@"
