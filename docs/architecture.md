# Architecture

## Host semantic path

Finder supplies local file URLs to the QEMU Metal display's `VMMetalView`. The view validates that every item is a readable regular file and registers the file URL pasteboard type without changing guest mouse handling.

`VMDisplayQemuMetalWindowController` maps the AppKit point through the Metal backing drawable, rendered viewport scale, letterbox origin, and framebuffer Y orientation. It creates a UUID transfer ID and sends only file basenames, sizes, display index, framebuffer dimensions, and coordinates through `UTMSpiceIO`.

`UTMSpiceIO` owns the private CocoaSpice `CSPort` named `com.utmapp.dnd.0`. It frames messages as UTF-8 newline-delimited JSON, enforces the 64 KiB limit, validates matching replies, and waits briefly for `ready` before starting the payload transfer.

QEMU creates the port with a `virtserialport` and `spiceport` chardev. Linux exposes it as `/dev/virtio-ports/com.utmapp.dnd.0`.

## Guest semantic path

`utm-dnd-guest` runs in the graphical user's systemd user service and reads the dedicated port. It asks the GNOME Shell extension to inspect the live target at the guest framebuffer coordinate.

- A genuine desktop background resolves through the XDG Desktop user directory.
- A Nautilus window is identified through its GTK window object path and mapped through Nautilus's public `OpenWindowsWithLocations` property.
- Unsupported, ambiguous, remote, non-local, or non-writable targets resolve to XDG Downloads.

The helper returns `ready` only after target validation and a Downloads inode baseline are established.

## Payload path

After `ready`, CocoaSpice calls the existing `spice_main_channel_file_copy_async()` API. `spice-vdagent` writes the files to its normal receive directory. The helper waits for stable, newly-created matching inodes and safely links or copies them into the resolved destination without overwriting existing files.

## Layering

CocoaSpice exposes generic `CSFileTransfer` and `CSPort` APIs. It does not know about GNOME, Nautilus, Desktop directories, or UTM's semantic protocol. UTM owns host UI, coordinates, transfer sequencing, and protocol replies. Guest Tools own target resolution and final placement.

## Lifetime and threading

SPICE GLib operations run on CocoaSpice's `CSMain` context. AppKit and application callbacks run on the macOS main queue. `CSFileTransfer` retains the main channel, cancellable, callback state, and URLs until exactly-once completion. Security-scoped URL access is held until the asynchronous GIO read completes. Port writes retain their `NSData` through the GLib write callback.
