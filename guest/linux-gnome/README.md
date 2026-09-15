# Linux GNOME guest tools

This component provides semantic target resolution for the UTM SPICE DnD integration.

The helper runs as the logged-in user. It reads versioned metadata from `/dev/virtio-ports/com.utmapp.dnd.0`, asks the GNOME Shell extension which semantic target is under the supplied framebuffer point, maps Nautilus GTK window object paths through the public `org.freedesktop.FileManager1.OpenWindowsWithLocations` property, and replies with a validated target. Received files are initially written by the existing `spice-vdagent` into XDG Downloads and are then safely moved.

Install from the repository root:

```sh
./guest/linux-gnome/install.sh
```

The only root operation installs the exact `com.utmapp.dnd.0` udev rule. The
installer refuses to replace a differing rule at the managed path. The
uninstaller removes that rule only while its contents still match this
project. The helper is never a root daemon and does not use SSH.
