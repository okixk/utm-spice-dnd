# Development and validation

## Reproduce the host patches

The patches are intended for these exact clean bases:

```sh
git clone https://github.com/utmapp/UTM.git
git -C UTM checkout --detach 048ca7498ea3a374439149d51739d94c5300bcda
git -C UTM apply host/utm-macos/patches/UTM.patch

git clone https://github.com/utmapp/CocoaSpice.git
git -C CocoaSpice checkout --detach ff3fb176c8b7ff13acf9e3832347ec7ffab2fa1c
git -C CocoaSpice apply host/utm-macos/patches/CocoaSpice.patch
```

`build.sh` performs this in an isolated `.build/` directory and arranges the patched CocoaSpice checkout for UTM's SwiftPM build without changing UTM's pinned package resolution file. UTM also requires its native dependency sysroot. The script builds it with UTM's own `scripts/build_dependencies.sh` when no `UTM_SYSROOT_DIR` is supplied; this may require the native tools documented by UTM and can take substantial time. A compatible prebuilt/local sysroot can be supplied explicitly with `UTM_SYSROOT_DIR=/path/to/sysroot-macOS-arm64`.

## Guest checks

```sh
python3 -m py_compile guest/linux-gnome/guest/utm_dnd_guest.py
python3 -m unittest discover -s guest/linux-gnome/tests -v
node --check guest/linux-gnome/gnome-shell-extension/utm-dnd-target@utmapp.dev/extension.js
bash -n guest/linux-gnome/install.sh guest/linux-gnome/uninstall.sh host/utm-macos/build.sh
```

The guest protocol uses strict version 1 schemas, a 64 KiB frame limit, bounded file count/name/size fields, no host-supplied destination path, and one active semantic transfer at a time.

## Linux host automated checks

Run the same Python coverage gate used by CI:

```sh
coverage erase
coverage run --branch --source=host/virt-manager-linux,guest/linux-gnome/guest \
  -m unittest discover -s host/virt-manager-linux/tests
coverage run --append --branch --source=host/virt-manager-linux,guest/linux-gnome/guest \
  -m unittest discover -s guest/linux-gnome/tests
coverage report --omit='*/tests/*' --fail-under=80
```

CI also checks Python, JavaScript, and shell syntax, then dry-applies the Linux
host patch to the checksum-pinned spice-gtk 0.42 source archive. These checks do
not replace real desktop drag tests.

## Live test model

Use an Ubuntu GNOME guest with the helper installed and the dedicated port ACL active. Verify host logs and guest journal contain the same transfer ID, then verify final paths and SHA-256 hashes. Do not include VM disks, private logs, screenshots, SSH credentials, or compiled applications in commits.
