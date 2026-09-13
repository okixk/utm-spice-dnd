#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
BUILD_ROOT="$SCRIPT_DIR/.build"

if [ ! -d "$BUILD_ROOT" ]; then
    printf 'No isolated development build exists at %s.\n' "$BUILD_ROOT"
    exit 0
fi

case $BUILD_ROOT in
    "$SCRIPT_DIR/.build") ;;
    *) printf 'error: refusing unexpected build path: %s\n' "$BUILD_ROOT" >&2; exit 1 ;;
esac

find "$BUILD_ROOT" -depth -delete
printf 'Removed isolated development build %s.\n' "$BUILD_ROOT"
printf '%s\n' 'No distro package, VM definition, or guest component was changed.'
