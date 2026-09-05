#!/bin/bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd "$script_dir/../.." && pwd)
build_root="$project_root/.build"
dist_root="$project_root/dist"
utm_root="$build_root/UTM"
cocoa_root="$build_root/CocoaSpice"
source_packages="$build_root/SourcePackages"
derived_data="$build_root/DerivedData"
output_app="$dist_root/UTM-SPICE-DnD.app"

utm_revision=048ca7498ea3a374439149d51739d94c5300bcda
cocoa_revision=ff3fb176c8b7ff13acf9e3832347ec7ffab2fa1c

fail() {
    echo "build.sh: $*" >&2
    exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "macOS is required"
[[ "$(uname -m)" == "arm64" ]] || fail "Apple Silicon arm64 is required for this initial build"

for command_name in git xcodebuild swift xcrun rsync codesign ditto; do
    command -v "$command_name" >/dev/null 2>&1 || fail "missing prerequisite: $command_name"
done

xcode-select -p >/dev/null 2>&1 || fail "Xcode command-line tools are not selected"

rm -rf "$build_root" "$dist_root"
mkdir -p "$build_root" "$dist_root"

clone_revision() {
    local url=$1
    local destination=$2
    local revision=$3
    git clone --filter=blob:none --no-checkout "$url" "$destination"
    git -C "$destination" checkout --detach "$revision"
}

echo "Cloning UTM $utm_revision"
clone_revision https://github.com/utmapp/UTM.git "$utm_root" "$utm_revision"
echo "Cloning CocoaSpice $cocoa_revision"
clone_revision https://github.com/utmapp/CocoaSpice.git "$cocoa_root" "$cocoa_revision"

echo "Applying CocoaSpice patch"
git -C "$cocoa_root" apply --check "$script_dir/patches/CocoaSpice.patch"
git -C "$cocoa_root" apply "$script_dir/patches/CocoaSpice.patch"

echo "Applying UTM patch"
git -C "$utm_root" apply --check "$script_dir/patches/UTM.patch"
git -C "$utm_root" apply "$script_dir/patches/UTM.patch"

echo "Building CocoaSpice"
swift build --package-path "$cocoa_root" -c debug

sysroot_destination="$utm_root/sysroot-macOS-arm64"
if [[ -n "${UTM_SYSROOT_DIR:-}" ]]; then
    [[ -d "$UTM_SYSROOT_DIR" ]] || fail "UTM_SYSROOT_DIR is not a directory: $UTM_SYSROOT_DIR"
    echo "Using UTM dependency sysroot from UTM_SYSROOT_DIR"
    mkdir -p "$sysroot_destination"
    rsync -a --delete "$UTM_SYSROOT_DIR/" "$sysroot_destination/"
else
    command -v brew >/dev/null 2>&1 || fail "UTM_SYSROOT_DIR is not set and Homebrew is required by UTM's dependency builder; install the documented UTM build prerequisites or set UTM_SYSROOT_DIR"
    echo "Building UTM's macOS arm64 dependency sysroot"
    echo "This follows UTM's scripts/build_dependencies.sh and may take a while."
    (
        cd "$utm_root"
        ./scripts/build_dependencies.sh -p macos -a arm64
    )
fi

[[ -d "$sysroot_destination/share/qemu" ]] || fail "UTM dependency sysroot is missing share/qemu"

echo "Resolving UTM package dependencies"
xcodebuild -resolvePackageDependencies \
    -project "$utm_root/UTM.xcodeproj" \
    -scheme macOS \
    -clonedSourcePackagesDirPath "$source_packages"

cocoa_checkout="$source_packages/checkouts/CocoaSpice"
[[ -d "$cocoa_checkout" ]] || fail "Xcode did not create the CocoaSpice package checkout"

echo "Installing patched CocoaSpice into isolated SwiftPM checkout"
rsync -a --delete --exclude='.git' "$cocoa_root/" "$cocoa_checkout/"

echo "Building UTM macOS arm64 development application"
xcodebuild -quiet \
    -project "$utm_root/UTM.xcodeproj" \
    -scheme macOS \
    -configuration Debug \
    -sdk macosx \
    -arch arm64 \
    -clonedSourcePackagesDirPath "$source_packages" \
    -disableAutomaticPackageResolution \
    -derivedDataPath "$derived_data" \
    CODE_SIGNING_ALLOWED=NO \
    build

product_app=$(find "$derived_data/Build/Products" -type d -path '*/Debug/UTM.app' -print -quit)
[[ -n "$product_app" && -d "$product_app" ]] || fail "UTM.app was not produced"

ditto "$product_app" "$output_app"
codesign --force --deep --sign - "$output_app" >/dev/null

echo
echo "Build complete: $output_app"
echo "The installed /Applications/UTM.app was not modified."
