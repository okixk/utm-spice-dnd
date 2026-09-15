#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
BUILD_ROOT="$SCRIPT_DIR/.build"

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

if [ ! -d "$BUILD_ROOT" ]; then
    printf 'No isolated development build exists at %s.\n' "$BUILD_ROOT"
    exit 0
fi

[ ! -L "$BUILD_ROOT" ] || fail "refusing symlinked build root: $BUILD_ROOT"
for command in find python3 realpath rmdir; do
    command -v "$command" >/dev/null 2>&1 || fail "required command is missing: $command"
done

SCRIPT_REAL=$(realpath -e "$SCRIPT_DIR")
BUILD_ROOT_REAL=$(realpath -e "$BUILD_ROOT")
[ "$BUILD_ROOT_REAL" = "$SCRIPT_REAL/.build" ] || \
    fail "build root resolves outside the host development directory"
assert_unmounted_tree "$BUILD_ROOT_REAL"

find "$BUILD_ROOT_REAL" -xdev -mindepth 1 -delete
rmdir -- "$BUILD_ROOT_REAL"
printf 'Removed isolated development build %s.\n' "$BUILD_ROOT"
printf '%s\n' 'No distro package, VM definition, or guest component was changed.'
