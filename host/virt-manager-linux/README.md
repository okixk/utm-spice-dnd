# Linux host development integration

This directory contains the Linux-host integration for virt-manager, virt-viewer,
libvirt, QEMU, and SPICE. It is a development build: it does not replace the
distribution's virt-manager or spice-gtk packages and it does not change a VM
until `enable-vm.sh` is run for that VM by name.

The semantic protocol and Linux GNOME guest component are shared with the macOS
UTM frontend. The compatibility port remains `com.utmapp.dnd.0` for protocol
version 1 even though the protocol is now frontend-neutral.

## Why the patch is in spice-gtk

virt-manager 5.1.0 embeds `SpiceClientGtk.Display` directly. It does not
intercept file drops. spice-gtk 0.42 already registers `text/uri-list` on that
widget and, in `drag_data_received_callback()` in `src/spice-widget.c`, starts
the normal `spice_main_channel_file_copy_async()` transfer. The same source file
already has the private `transform_input()` and `spice_display_get_scaling()`
logic needed to map a GTK drop point through host scale, display scaling,
letterboxing, and the selected monitor area.

The Linux integration therefore extends the layer that already owns the drop,
coordinates, and payload call. It does not reimplement file transfer in Python
or add a second payload transport. A private spice-gtk build is consumed by an
unmodified source virt-manager checkout. The same spice-gtk build is intended
to work with virt-viewer, so target-aware behavior is not tied to virt-manager's
console wrapper.

## Development bases

The initial port targets the exact versions found on the development host:

| Component | Development base |
| --- | --- |
| Host OS | Ubuntu 26.04.1 LTS, x86_64, kernel 7.0.0-31-generic |
| Desktop | GNOME Shell 50.1 on Wayland |
| virt-manager | 5.1.0; upstream commit `eb4898b19e550af19daea49ae5ed15d2d70a2fc4`; Ubuntu package `1:5.1.0-1` |
| spice-gtk | 0.42; upstream commit `f04479c16f0969fb394ebe74b6eff74e560a42f0`; Ubuntu package `0.42-4build1` |
| virt-viewer | 11.0 source base `8d8923d259b79c7c9ad3560a49be81dc0f38b88b`; not initially installed on the development host |
| libvirt | 12.0.0; Ubuntu package `12.0.0-1ubuntu5.3` |
| QEMU | 10.2.1; Ubuntu package `1:10.2.1+ds-1ubuntu3.2` |
| PyGObject | 3.56.2; Python 3.14.4 |
| GTK used by virt-manager | GTK 3.24.52 |

The installed virt-manager `viewers.py` and `console.py` were byte-for-byte
matched to the listed upstream virt-manager commit before selecting the base.

## Install the host development build

From the repository root:

```sh
./host/virt-manager-linux/install.sh
```

The installer builds into `host/virt-manager-linux/.build/`, with separate
`src`, `build`, `prefix`, and `packages` areas, and creates these launchers:

```sh
./host/virt-manager-linux/run-virt-manager.sh
./host/virt-manager-linux/run-virt-viewer.sh VM_NAME
```

The launchers select the private spice-gtk libraries and the locally extracted
usbredir/libusb runtime libraries. This keeps USB support usable on a clean host
without installing those packages into `/usr`. The installer must not copy
patched files into `/usr`, overwrite the distribution's Python files, or replace
the normal desktop launcher. Keep the terminal output: it identifies the source
revisions, prefix, and launcher used for later diagnostics.

Run `run-virt-manager.sh` instead of `/usr/bin/virt-manager` for a target-aware
test. Close any already-running distro virt-manager process first;
otherwise its single-instance behavior can focus the old process rather than
load the development libraries.

The normal distribution program remains available and is the correct baseline
viewer for before/after comparisons.

## Enable one VM

Shut down the selected VM, then run:

```sh
./host/virt-manager-linux/enable-vm.sh VM_NAME
```

The configuration tool is scoped to the named persistent QEMU/KVM domain. It
verifies SPICE graphics, the standard `com.redhat.spice.0` agent channel, and a
virtio-serial controller; explicitly enables SPICE file transfer; and adds this
device only when it is absent:

```xml
<channel type='spiceport'>
  <source channel='com.utmapp.dnd.0'/>
  <target type='virtio' name='com.utmapp.dnd.0'/>
</channel>
```

Before redefining the persistent domain, the tool creates a timestamped XML
backup and prints the proposed change. It refuses a running domain when a
configuration change is required, validates the resulting domain XML, and is
idempotent. Review its diff before booting the VM.

The corresponding QEMU form is allocated dynamically by libvirt and resembles:

```text
-chardev spiceport,id=charchannelN,name=com.utmapp.dnd.0
-device {"driver":"virtserialport",...,"chardev":"charchannelN",...,"name":"com.utmapp.dnd.0"}
```

IDs, bus addresses, and virtio-serial port numbers are intentionally left to
libvirt. In the guest, the target name produces:

```text
/dev/virtio-ports/com.utmapp.dnd.0
```

Do not add a TCP listener, SSH dependency, monitor-based application protocol,
or a user-visible arbitrary UNIX socket.

## Install and verify the guest

Inside the active GNOME guest, from a checkout of this repository:

```sh
./guest/linux-gnome/install.sh
systemctl --user status utm-dnd-guest.service
getfacl /dev/virtio-ports/com.utmapp.dnd.0
journalctl --user -u utm-dnd-guest.service -n 50 --no-pager
```

The active local user should receive a logind `uaccess` ACL. The journal should
eventually contain `control port connected`. The guest helper is the same one
used with UTM; there is no virt-manager-specific target resolver.

## Development test procedure

First record the baseline using unmodified distro virt-manager. Drag a regular
host file from the graphical file manager into the VM console and determine
whether it reaches Downloads through spice-gtk's existing transfer path. Repeat
a representative baseline with unmodified virt-viewer when it is installed.

Then start the development viewer and perform real desktop drags—not synthetic
API-only transfer calls—for all of the following:

1. Guest Desktop.
2. Nautilus showing Documents.
3. Nautilus showing a nested normal local directory.
4. An unsupported guest application, expecting Downloads.
5. Multiple files to one resolved directory.
6. A Unicode filename.
7. A zero-byte file.
8. A duplicate destination filename, expecting a non-overwriting name.
9. Helper stopped, expecting ordinary Downloads fallback.
10. Helper restarted, expecting target-aware behavior again.
11. Guest agent unavailable.
12. VM restart and viewer reconnect.
13. Metadata timeout.
14. Malformed guest response.
15. Transfer cancellation.

For each successful payload transfer, compare hashes on both sides:

```sh
sha256sum /path/to/host-file
sha256sum /path/to/final/guest-file
```

Record the final guest path, host hash, guest hash, viewer, transfer ID, and
relevant logs. Do not mark a case passed solely because the host API returned
success.

### Current evidence status

The Ubuntu 26.04 GNOME/Wayland guest `ubuntu-2604` was exercised with real
Nautilus drags on 2026-09-09 and 2026-09-12. Distro virt-manager and unmodified
virt-viewer first confirmed the existing spice-gtk payload path to Downloads.
The private patched spice-gtk build then passed the complete practical matrix
in virt-manager, including exact Desktop/Documents/nested-directory targeting,
multiple files, Unicode, zero-byte and duplicate files, helper and agent
failures, reconnect, timeout, malformed response, and active cancellation.
Representative target-aware Desktop and Downloads-fallback drops also passed
in virt-viewer. Every completed payload listed in the report has matching host
and guest SHA-256 evidence.

The exact environment, XML/QEMU/device/port proof, hashes, failure injection,
and test-suite results are recorded in [LIVE-TEST.md](LIVE-TEST.md). Two protocol
v1 limitations remain: fallback is intentionally withheld if a peer never
acknowledges cancellation, and same-name/same-size payload association is not
cryptographically or spice-vdagent-ID bound.

## Disable one VM

With the named VM shut down:

```sh
./host/virt-manager-linux/disable-vm.sh VM_NAME
```

The disable operation removes only the channel whose type, source channel, and
virtio target identify `com.utmapp.dnd.0`. It does not remove the normal SPICE
agent channel, disable ordinary file transfer, or rewrite unrelated devices.
Review the printed diff and retained XML backup.

## Uninstall the host development build

First disable each VM where the dedicated channel is no longer wanted. Then:

```sh
./host/virt-manager-linux/uninstall.sh
```

The uninstaller refuses to traverse the isolated build tree when it contains a
mount point. Unmount that tree first; unrelated mounted data is never removed.

Uninstall removes only the isolated development build and launchers created by
the installer. It does not remove distro packages or silently edit domains.
Guest removal is separate:

```sh
./guest/linux-gnome/uninstall.sh
```

## Security boundaries

- The named port carries bounded metadata, never payload bytes or commands.
- The host supplies only basenames, sizes, display geometry, and coordinates;
  it cannot select a guest filesystem path.
- The unprivileged guest helper resolves and validates the destination.
- JSON version, type, fields, UUID, bounds, names, sizes, and frame length are
  strictly validated; the maximum frame is 64 KiB.
- Existing destination files are never silently overwritten.
- Missing metadata falls back directly. Late or malformed metadata falls back
  after the cancellation barrier is acknowledged; an unresponsive accepted
  peer fails closed as documented in the live-test limitations.
- Port access is `uaccess` for the active graphical user, not world-writable and
  not a root daemon.

## Upstream direction

The smallest reusable upstream split is:

1. spice-gtk: a generic, cancellable/deferred file-drop hook that exposes the
   mapped framebuffer point and resumes the existing file-copy path.
2. A consumer of that hook: the frontend-neutral semantic metadata handshake.
3. virt-manager and virt-viewer: ideally no feature-specific transfer code;
   only packaging or opt-in integration if the shared API requires it.
4. libvirt: no new feature is currently indicated; existing `spiceport`,
   `spicevmc`, and `<filetransfer>` XML are sufficient.

No upstream pull request has been submitted. The implementation was tested on
the `feature/virt-manager-qemu` branch; final commit and push state is reported
separately from this development guide.
