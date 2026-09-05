# UTM semantic drag-and-drop control protocol v1

The transport is UTF-8 newline-delimited JSON over the SPICE port named `com.utmapp.dnd.0`. The limit is 64 KiB per message including the newline. Readers buffer partial messages and process multiple messages from one read. An unterminated message exceeding the limit closes and reconnects the port.

UTM sends a `drop` message before starting the standard SPICE transfer:

```json
{"type":"drop","version":1,"transferId":"UUID","display":0,"x":640,"y":400,"framebufferWidth":1280,"framebufferHeight":800,"files":[{"name":"test.txt","size":1234}]}
```

Coordinates are top-left-origin pixels in the selected SPICE framebuffer. The Shell extension converts these to GNOME logical stage coordinates using the monitor geometry.

The helper replies with `ready` only after target resolution and a baseline snapshot of Downloads:

```json
{"type":"ready","version":1,"transferId":"UUID","target":{"kind":"nautilus","uri":"file:///home/user/Documents","confidence":"high"}}
```

UTM starts `spice_main_channel_file_copy_async()` only after `ready`. It may send `cancel` if the SPICE operation fails. The helper emits `complete` or `error` status messages for diagnostics.

Host-provided paths are not accepted. Only basenames and sizes are metadata; the guest resolves and validates the destination.

## Validation limits

- Protocol version must be exactly `1`; unknown versions and message types are rejected.
- `transferId` must be a UUID.
- Display is an integer from 0 through 31.
- Coordinates and framebuffer dimensions must be finite, positive where applicable, and the point must be inside the framebuffer.
- A drop contains 1 through 100 files.
- Each file has exactly a UTF-8 basename (maximum 255 bytes) and a non-negative signed 64-bit size.
- Unknown object fields are rejected. Destination paths and commands are not protocol fields.

Only one semantic transfer is active at a time. A matching `cancel` disarms it; already received unmatched files remain safely in Downloads. Disconnecting the port clears buffered partial input and active metadata before reconnecting.
