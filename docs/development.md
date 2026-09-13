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

Windows ARM64 (use `win-x64` for x64):

```powershell
dotnet build guest/windows/src/UtmDndGuest/UtmDndGuest.csproj -c Release -r win-arm64 --self-contained true
dotnet publish guest/windows/tests/UtmDndGuest.Tests/UtmDndGuest.Tests.csproj -c Release -r win-arm64 --self-contained true -p:PublishSingleFile=true -o artifacts/tests-win-arm64
artifacts/tests-win-arm64/UtmDndGuest.Tests.exe
```

`tests/UtmDndGuest.PortFaultPeer` is a development-only interactive test peer for malformed-reply and connected-timeout fallback tests. It is not installed by `install.ps1`.

The guest protocol uses strict version 1 schemas, a 64 KiB frame limit, bounded file count/name/size fields, no host-supplied destination path, and one active semantic transfer at a time.

## Live test model

Use an Ubuntu GNOME guest or Windows interactive desktop with the matching helper installed. Verify host and guest logs contain the same transfer ID, then verify final paths and SHA-256 hashes. On Windows also exercise Explorer tabs, helper absence, malformed/timeout peers, reboot, and VDAgent cancellation. Do not include VM disks, private logs, screenshots, SSH credentials, or compiled applications in commits.
