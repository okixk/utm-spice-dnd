# Target-aware macOS Finder → Linux and Windows drag and drop for UTM/QEMU

This experimental project adds VMware-like target-aware file drops to a macOS UTM/QEMU VM using SPICE.

Drag a regular file from Finder onto:

- the empty Ubuntu desktop → the guest Desktop directory;
- the background of a Nautilus window → that window's current local directory;
- the Windows shell desktop → the Windows Desktop known folder;
- a Windows File Explorer window → that window's current local directory;
- an unsupported or ambiguous target → the guest Downloads directory.

The host-to-guest payload remains the standard SPICE file-transfer protocol. A small dedicated SPICE/virtio serial port carries only versioned drop metadata and readiness/status messages.

## What it does

- macOS Finder file drops onto UTM's QEMU Metal display;
- one or multiple regular files per drop;
- Desktop and local Nautilus folder targeting on GNOME;
- Desktop and local File Explorer targeting on Windows;
- safe duplicate-name handling without overwriting existing files;
- Downloads fallback when target inspection or the control port is unavailable;
- strict, bounded newline-delimited JSON control protocol;
- cancellation and reconnect handling;
- no SSH or network transport at runtime.

## Status and tested environment

This is an experimental developer integration, initially tested with UTM 4.7.5 on Apple Silicon, an Ubuntu ARM64 GNOME Wayland guest, and Windows 11 Pro ARM64 25H2 build 26200.9445. It is not an official UTM plugin or binary release.

The host side is distributed as patches against UTM and CocoaSpice rather than as copies of either upstream repository. The guest side is a small prototype Guest Tools component.

## Architecture

```text
Finder
  → VMMetalView
  → UTM framebuffer coordinate mapping
  → UTMSpiceIO
  → CocoaSpice CSPort
  → com.utmapp.dnd.0
  → utm-dnd-guest
  → GNOME Shell / Nautilus or Windows Shell target inspection
  ← ready

Host file URLs
  → CocoaSpice CSFileTransfer
  → spice_main_channel_file_copy_async()
  → spice-vdagent
  → Downloads staging
  → safe final placement
```

See [docs/architecture.md](docs/architecture.md) and [docs/protocol.md](docs/protocol.md).

## Requirements

Host:

- macOS on Apple Silicon;
- Xcode command-line tools, `xcodebuild`, Swift, and Git;
- a source checkout of the required upstream dependencies is fetched by the build script;
- a QEMU/SPICE UTM VM.

Linux guest:

- Linux with a systemd user manager;
- GNOME Shell, preferably Wayland;
- Nautilus for current-folder targeting;
- Python 3, `spice-vdagent`, and `gnome-extensions`;
- a QEMU virtio port named `com.utmapp.dnd.0`.

Windows guest:

- Windows 10 or later on ARM64 or x64;
- UTM Windows Guest Tools with VirtIO Serial, `vdservice`, and interactive `vdagent`;
- the same QEMU virtio port named `com.utmapp.dnd.0`.

## Host installation

From this repository on an Apple Silicon Mac:

```sh
./host/utm-macos/build.sh
```

The script clones the exact UTM and CocoaSpice base revisions, applies the patches, provisions UTM's native macOS dependency sysroot (or uses `UTM_SYSROOT_DIR` if supplied), resolves UTM's other Swift packages into an isolated workspace, substitutes the patched CocoaSpice checkout there, and builds:

```text
dist/UTM-SPICE-DnD.app
```

The automatic dependency path follows UTM's own dependency builder and may require the native tools documented by UTM, including Homebrew-provided tools. Dependencies are not installed globally by this project. See [host/utm-macos/README.md](host/utm-macos/README.md) for the optional sysroot override.

The development app is separate from the normal installation. `/Applications/UTM.app` is never modified. An ad-hoc/development build may not have Apple's normal UTM networking entitlements. Some VM configurations may therefore need QEMU user networking instead of vmnet shared networking. The DnD feature itself uses SPICE and is independent of IP networking.

See [host/utm-macos/README.md](host/utm-macos/README.md) and [host/utm-macos/uninstall.md](host/utm-macos/uninstall.md).

## Linux guest installation

Run inside the logged-in GNOME guest:

```sh
./guest/linux-gnome/install.sh
```

The installer places the helper, user systemd unit, and Shell extension in the user's home directory. It installs one narrowly scoped udev rule with sudo:

```udev
SUBSYSTEM=="virtio-ports", ATTR{name}=="com.utmapp.dnd.0", MODE="0660", TAG+="uaccess"
```

The helper never runs as root and the port is never made world-writable. A reboot, or logout/login after the first installation, may be required for the udev ACL and Shell extension to become active.

## Windows guest installation

Publish the self-contained helper for the guest architecture and run the installer as the intended desktop user:

```powershell
dotnet publish guest/windows/src/UtmDndGuest/UtmDndGuest.csproj `
  -c Release -r win-arm64 --self-contained true `
  -o guest/windows/artifacts/win-arm64
guest/windows/install.ps1
```

The installer creates a limited, interactive logon task under the current user. It does not install a service, alter VirtIO device ACLs, or require .NET/Visual Studio in the guest. See [guest/windows/README.md](guest/windows/README.md).

## Usage

1. Install the matching guest component and ensure its user service/task is active.
2. Start the patched UTM development build.
3. Start a QEMU/SPICE VM with the dedicated port configured.
4. Drag regular files from Finder onto the guest display.

Directories, remote Finder URLs, and special files are rejected as a whole drop before transfer begins.

## Fallback and staging

The guest keeps the existing spice-vdagent receive directory unchanged. Files first arrive in the guest Downloads directory. The helper records a pre-transfer file-identity baseline and matches only new regular files with the expected basename, size, creation time, and stability interval before moving them. Existing files are never overwritten.

This association is robust for the prototype but is not cryptographically tied to an individual SPICE transfer task. Unmatched files remain in Downloads.

## Known limitations

- macOS UTM frontend only;
- Linux GNOME and Windows guest target-awareness only;
- host → guest only;
- regular files only, no directory recursion;
- Downloads is temporary SPICE staging;
- staging association is not cryptographically tied to a SPICE task;
- multi-display behavior needs broader testing;
- GNOME API compatibility needs broader distro/version testing;
- Windows 11 Explorer frames with tabs that expose multiple different paths for one HWND fall back to Downloads instead of guessing the selected tab;
- no user-facing progress window yet;
- no iOS, Apple Virtualization.framework, guest → host, SSH/SFTP, or native Wayland data-device injection.

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md). Useful checks include:

```sh
systemctl --user status utm-dnd-guest.service
gnome-extensions info utm-dnd-target@utmapp.dev
ls -l /dev/virtio-ports/com.utmapp.dnd.0
getfacl /dev/virtio-ports/com.utmapp.dnd.0
```

The helper requires the active graphical user session. SSH-only sessions do not necessarily receive a logind `uaccess` ACL.

For Windows, inspect the limited interactive task and per-user log:

```powershell
Get-ScheduledTask -TaskName 'UTM DnD Guest'
Get-Content "$env:LOCALAPPDATA\UTM DnD Guest\Logs\utm-dnd-guest.log" -Tail 50
```

## Uninstall

```sh
./guest/linux-gnome/uninstall.sh
```

or on Windows:

```powershell
guest/windows/uninstall.ps1
```

This does not remove `spice-vdagent` or unrelated user files. Host builds can be removed with the instructions in [host/utm-macos/uninstall.md](host/utm-macos/uninstall.md).

## Development

The exact source revisions, patch workflow, tests, and live-test notes are in [docs/development.md](docs/development.md). Do not edit generated build directories or upstream source repositories as part of a normal contribution; update the patches and repeat clean-clone validation.

## Upstream status

This project is a focused prototype intended to support discussion with UTM and CocoaSpice upstream. It does not claim to be production-ready Guest Tools packaging. In particular, the guest installation, GNOME compatibility matrix, and Downloads association strategy need further review.

## License and attribution

The newly authored guest tooling, scripts, tests, and documentation in this repository are licensed under the GNU Affero General Public License, version 3.0 only. See [LICENSE](LICENSE). UTM and CocoaSpice retain their upstream Apache 2.0 licensing and copyright headers inside the patches; spice-gtk remains LGPL 2.1. See [NOTICE.md](NOTICE.md) for the component-by-component attribution.
