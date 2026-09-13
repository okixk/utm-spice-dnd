# Target-aware SPICE file drag and drop

This experimental project adds VMware-like, target-aware host-to-guest file
drops to QEMU/SPICE frontends while retaining the standard SPICE file-transfer
protocol for payload bytes.

Supported host integrations live side by side:

- `host/utm-macos/`: Finder to a patched UTM/CocoaSpice development build;
- `host/virt-manager-linux/`: a Linux file manager to a patched, isolated
  spice-gtk development build used by source virt-manager and virt-viewer.

Both frontends use the same Linux GNOME guest component and version 1 semantic
protocol. For wire compatibility, its dedicated SPICE port is still named
`com.utmapp.dnd.0`; new code and documentation treat the protocol itself as
frontend-neutral.

Drag one or more regular files onto:

- the guest desktop to target the guest XDG Desktop directory;
- a Nautilus window showing a normal local directory to target that exact
  directory;
- an unsupported or ambiguous guest target to use XDG Downloads safely.

Existing destination files are not overwritten.

## Architecture

```text
host file manager
  -> frontend display widget and framebuffer coordinate mapping
  -> SPICE named port: com.utmapp.dnd.0
  -> unprivileged guest helper
  -> GNOME Shell / Nautilus target inspection
  <- matching ready response

host file URLs
  -> existing spice_main_channel_file_copy_async()
  -> spice-vdagent
  -> XDG Downloads staging
  -> safe guest-side final placement
```

The control port carries bounded metadata only. File contents always use
ordinary asynchronous SPICE transfer. The host never supplies a destination
path and cannot ask the guest to execute a command.

See [architecture](docs/architecture.md) and [protocol](docs/protocol.md) for
the detailed layering and validation rules.

## Status

The macOS integration was initially tested with UTM 4.7.5 on Apple Silicon and
an Ubuntu ARM64 GNOME Wayland guest.

The Linux integration targets Ubuntu 26.04.1, virt-manager 5.1.0, spice-gtk
0.42, libvirt 12.0.0, and QEMU 10.2.1. Source inspection and libvirt/QEMU XML
validation are complete. Real graphical file-manager drops, final guest path
checks, SHA-256 comparisons, injected failure modes, and the virt-viewer run
must be recorded before the Linux port is described as live-tested. Consult the
current branch's test report rather than treating planned test cases as passes.

This is not an official UTM, virt-manager, virt-viewer, spice-gtk, or libvirt
release.

## Requirements

Guest requirements for either host:

- Linux with a systemd user manager;
- GNOME Shell, initially Ubuntu GNOME on Wayland;
- Nautilus for current-folder targeting;
- Python 3, `spice-vdagent`, and `gnome-extensions`;
- a virtio port exposed as `/dev/virtio-ports/com.utmapp.dnd.0`.

Host requirements depend on the frontend:

- macOS: Apple Silicon, Xcode command-line tools, Swift, Git, UTM/QEMU with
  SPICE;
- Linux: QEMU/KVM domain managed by libvirt, SPICE graphics, virt-manager,
  GTK 3/spice-gtk development prerequisites, and Git/build tools documented in
  the [Linux host guide](host/virt-manager-linux/README.md).

## Install

### Linux host with virt-manager

From the repository root:

```sh
./host/virt-manager-linux/install.sh
./host/virt-manager-linux/enable-vm.sh VM_NAME
./guest/linux-gnome/install.sh        # run this line inside the guest
```

The VM must be shut down when its persistent XML needs to change. The host
installer creates an isolated build under
`host/virt-manager-linux/.build/`; it does not overwrite distribution-managed
files in `/usr`. Launch the development viewers with:

```sh
./host/virt-manager-linux/run-virt-manager.sh
./host/virt-manager-linux/run-virt-viewer.sh VM_NAME
```

See the [Linux host guide](host/virt-manager-linux/README.md) for source bases,
backup behavior, XML, verification, and the required live-test matrix.

### macOS host with UTM

```sh
./host/utm-macos/build.sh
./guest/linux-gnome/install.sh        # run this line inside the guest
```

The host build is separate from `/Applications/UTM.app`. See the
[macOS host guide](host/utm-macos/README.md) for the dependency sysroot and
development-app limitations.

## Guest verification

In the active graphical guest session:

```sh
systemctl --user status utm-dnd-guest.service
gnome-extensions info utm-dnd-target@utmapp.dev
getfacl /dev/virtio-ports/com.utmapp.dnd.0
journalctl --user -u utm-dnd-guest.service -n 50 --no-pager
```

The helper should log `control port connected`. Its narrowly scoped udev rule
grants `uaccess` to the active local user; the helper does not run as root and
the device is not world-writable. A reboot or logout/login can be required after
the first installation.

## Fallback and staging

The project leaves spice-vdagent's normal receive directory unchanged. Files
first arrive in XDG Downloads. The helper records a pre-transfer inode baseline
and matches only newly created, stable regular files with the expected basename
and size before safely placing them in the resolved directory. Unmatched files
remain in Downloads.

If the metadata port is absent or closed before a drop, no semantic state can
have been armed and the host immediately uses ordinary SPICE transfer. If a
late or invalid handshake may already have armed guest state, the host first
sends `cancel` and waits for the matching `cancelled` acknowledgement before
releasing that same ordinary transfer to Downloads. A missing cancellation
acknowledgement fails closed and requires a viewer reconnect plus guest-helper
restart; releasing a payload while stale target state may exist would be unsafe.

## Security model

- no SSH, TCP listener, QEMU monitor, or arbitrary host-visible socket as the
  runtime application protocol;
- no host-provided guest destination and no arbitrary guest command execution;
- strict versioned JSON schema, UUID matching, coordinate and size bounds, a
  64 KiB frame limit, and malformed-message rejection;
- one active semantic transfer, acknowledged cancellation, and fail-closed
  handling when guest state cannot be proven disarmed;
- unprivileged per-user guest helper with logind `uaccess`;
- collision-safe final naming without silent overwrite.

## Known limitations

- host to guest only;
- Linux GNOME guest target-awareness only;
- regular files only, with no directory recursion;
- Downloads is temporary SPICE staging;
- staging association is not cryptographically tied to a SPICE transfer task;
- an open but non-responsive helper that never acknowledges cancellation cannot
  safely receive the ordinary fallback in protocol v1;
- multi-display, mixed-DPI, and broad GNOME-version coverage require more live
  testing;
- no user-facing progress window yet;
- no Windows guest, guest-to-host, SSH/SFTP, or native Wayland data-device
  injection.

Linux-specific test status and limitations are tracked separately because a
source-inspection result is not a substitute for a real desktop drag and final
guest hash verification.

## Uninstall

Linux host and VM configuration:

```sh
./host/virt-manager-linux/disable-vm.sh VM_NAME
./host/virt-manager-linux/uninstall.sh
```

Guest component:

```sh
./guest/linux-gnome/uninstall.sh
```

macOS host build removal is documented in
[host/utm-macos/uninstall.md](host/utm-macos/uninstall.md).

## Development and upstreaming

Exact source revisions, checks, and evidence standards are in
[docs/development.md](docs/development.md). Linux viewer behavior belongs in
spice-gtk because that shared widget already owns GTK drop reception,
framebuffer mapping, and the standard file-copy call. A clean upstream design
would expose a generic deferred-drop/mapped-coordinate hook in spice-gtk and
keep frontend-specific policy out of virt-manager. Existing libvirt channel and
file-transfer XML are sufficient.

Do not submit upstream changes or push the feature branch until the live matrix
passes. Do not merge it into `main` automatically.

## License and attribution

Project-authored guest tooling, scripts, tests, and documentation are licensed
under the GNU Affero General Public License, version 3.0 only. See
[LICENSE](LICENSE). UTM and CocoaSpice retain their upstream Apache 2.0
licensing and copyright headers inside the patches; spice-gtk remains LGPL 2.1.
See [NOTICE.md](NOTICE.md) for component attribution.
