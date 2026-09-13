# Architecture

## Shared contract

The project has two host frontends and one guest implementation. Each host owns
its native drag event and display-coordinate conversion, but both use the same
control port, message schema, guest target resolver, and SPICE payload path.

```text
macOS Finder -> UTM/CocoaSpice ---------+
                                          +-> com.utmapp.dnd.0 -> guest helper
Linux file manager -> spice-gtk Display -+                         |
                                                                    v
                                      standard SPICE file copy -> Downloads
                                                                    |
                                                                    v
                                               safe target-aware placement
```

`com.utmapp.dnd.0` is retained as the protocol v1 wire name for compatibility.
It does not imply that the shared guest helper depends on UTM.

## macOS host path

Finder supplies local file URLs to the QEMU Metal display's `VMMetalView`. The
view validates that every item is a readable regular file and registers the file
URL pasteboard type without changing guest mouse handling.

`VMDisplayQemuMetalWindowController` maps the AppKit point through the Metal
backing drawable, rendered viewport scale, letterbox origin, and framebuffer Y
orientation. It creates a UUID transfer ID and sends only file basenames, sizes,
display index, framebuffer dimensions, and coordinates through `UTMSpiceIO`.

`UTMSpiceIO` owns the private CocoaSpice `CSPort`. SPICE GLib operations run on
CocoaSpice's `CSMain` context; UI callbacks run on the macOS main queue.

## Linux host path

### Existing execution path

virt-manager 5.1.0 creates the SPICE session in
`virtManager/details/viewers.py`, receives a `DisplayChannel`, and embeds a
`SpiceClientGtk.Display` directly. There is no virt-manager file-drop handler or
file-transfer-disable path in front of the widget.

spice-gtk 0.42 owns the GTK drag destination in `src/spice-widget.c`:

- the widget registers `text/uri-list` in the drag-destination setup;
- `drag_data_received_callback()` parses the URI list and immediately calls
  `spice_main_channel_file_copy_async()`;
- the callback receives the final GTK widget coordinates, but upstream 0.42
  ignores them;
- private `transform_input()` and `spice_display_get_scaling()` already map
  widget input into the selected guest display/monitor.

The baseline architecture therefore already transfers payloads in spice-gtk.
The Linux port changes this shared layer only. An unmodified source
virt-manager, and later virt-viewer, load the isolated patched library and
typelib through development launchers.

### Coordinate conversion

The metadata coordinate is not copied directly from the GTK callback. The
spice-gtk mapping accounts for:

- the GTK widget's logical coordinate system and host scale factor;
- the actual SpiceDisplay allocation;
- aspect-preserving guest display scaling and `only-downscale` behavior;
- black bars/letterbox origin;
- the selected SPICE monitor rectangle within the display surface;
- guest framebuffer width and height.

A point in padding or outside the selected monitor is not presented as a valid
guest target. A valid point is emitted as top-left-origin integer framebuffer
pixels together with its display identifier and framebuffer dimensions. The
same mapping must remain correct in windowed, fullscreen, scrolled, and HiDPI
configurations. Unit tests cover the pure mapping cases; mixed-DPI and multiple
SPICE monitors still require live evidence.

### Deferred drop and fallback

The patched callback validates all dropped `GFile` objects as readable regular
local files, creates a transfer UUID, maps the final point, and looks for the
named `SpicePortChannel`. If the port is available, it sends `drop` metadata and
defers the existing file-copy call until a strict, matching `ready` response.

If the control port is absent or closed before metadata is sent, the callback
calls the same existing `spice_main_channel_file_copy_async()` path without
semantic placement and spice-vdagent leaves the payload in Downloads. If a
malformed, mismatched, or late reply follows a successful metadata write, the
viewer first sends `cancel` and requires the matching `cancelled`
acknowledgement. Only then does it release the ordinary transfer. If the guest
never acknowledges cancellation, protocol v1 fails closed and requires a viewer
reconnect plus guest-helper restart because stale target state cannot safely be
excluded. No parallel payload protocol exists.

The transfer remains asynchronous and cancellable. A failed or cancelled
payload can send matching `cancel` metadata when the control connection is
available, but cancellation never authorizes deletion or replacement of an
unrelated guest file.

## Libvirt and QEMU channel

The persistent libvirt domain contains:

```xml
<channel type='spiceport'>
  <source channel='com.utmapp.dnd.0'/>
  <target type='virtio' name='com.utmapp.dnd.0'/>
</channel>
```

It also retains the standard vdagent device:

```xml
<channel type='spicevmc'>
  <target type='virtio' name='com.redhat.spice.0'/>
</channel>
```

SPICE graphics explicitly enables its ordinary file-transfer feature:

```xml
<graphics type='spice' autoport='yes'>
  <filetransfer enable='yes'/>
</graphics>
```

An omitted `<filetransfer>` is also enabled by libvirt's default, while
`enable='no'` generates `disable-agent-file-xfer=on`. The configuration tool
makes the intended state explicit.

libvirt translates the semantic channel to a QEMU `spiceport` chardev connected
to a `virtserialport` named `com.utmapp.dnd.0`. The QEMU client-facing SPICE
session exposes a correspondingly named `SpicePortChannel`, and Linux exposes
the virtio target name as `/dev/virtio-ports/com.utmapp.dnd.0`. Libvirt selects
device IDs, addresses, and port numbers.

## Guest semantic path

`utm-dnd-guest` runs in the graphical user's systemd user service and reads the
dedicated port. It asks the GNOME Shell extension to inspect the live target at
the supplied guest framebuffer coordinate.

- A genuine desktop background resolves through the XDG Desktop user directory.
- A Nautilus window is identified through its GTK window object path and mapped
  through Nautilus's public `OpenWindowsWithLocations` property.
- Unsupported, ambiguous, remote, non-local, or non-writable targets resolve to
  XDG Downloads.

The helper returns `ready` only after target validation and a Downloads inode
baseline are established. It is unaware of which host frontend sent the
metadata.

## Payload and final placement

After `ready`, the host starts the existing
`spice_main_channel_file_copy_async()` operation. spice-vdagent writes into its
normal XDG Downloads destination. The helper watches for stable, newly created
regular inodes matching the declared basenames and sizes and safely links or
copies them into the resolved destination. It never overwrites an existing
name; unmatched payloads remain in Downloads.

This staging association is deliberately conservative but is not
cryptographically bound to an individual SPICE transfer task. That remains a
documented limitation.

## Trust and privilege boundaries

Control metadata is untrusted on both sides. Frames and schemas are bounded;
UUIDs must match; paths and commands are not accepted protocol fields. The
guest independently resolves and validates local writable targets. The guest
helper runs as the active graphical user and receives device access via logind
`uaccess`, never as a root service.

Neither host uses SSH, a TCP application listener, an arbitrary UNIX socket, or
QEMU monitor commands for runtime drag-and-drop metadata.

## Upstream layering

The reusable primitive belongs in spice-gtk: a generic deferred file-drop API
or signal exposing its already-mapped framebuffer point and a controlled way to
resume the existing file-copy operation. virt-manager and virt-viewer should
not each duplicate SPICE payload handling or private display math. The semantic
consumer can remain outside spice-gtk if upstream does not want project-specific
policy in the widget.

No new libvirt feature appears necessary. Existing `spiceport`, `spicevmc`, and
SPICE `<filetransfer>` support express the required VM devices.
