# Target-aware SPICE drag-and-drop control protocol v1

This protocol is frontend-neutral. UTM on macOS, a patched spice-gtk viewer on
Linux, or another compatible SPICE frontend can act as the host sender. The
version 1 SPICE port remains named `com.utmapp.dnd.0` solely for compatibility
with installed guests; the name does not make the protocol UTM-specific.

The control transport is UTF-8, newline-delimited JSON on that named SPICE
port. Each frame is limited to 64 KiB including its newline. Readers buffer
partial frames, accept multiple frames in one read, reject malformed and
oversized frames, and reset partial input when the port disconnects. File
contents never travel over this channel.

## Successful transfer

The host sends `drop` before starting the standard SPICE file transfer:

```json
{"type":"drop","version":1,"transferId":"UUID","display":0,"x":640,"y":400,"framebufferWidth":1280,"framebufferHeight":800,"files":[{"name":"test.txt","size":1234}]}
```

Coordinates are top-left-origin pixels in the selected SPICE framebuffer.
`display` identifies the SPICE display, not a host monitor. The frontend must
map the final widget drop point through scaling, letterboxing, scrolling, and
HiDPI transforms before creating this message. The Shell extension converts
the framebuffer point to GNOME logical stage coordinates using guest monitor
geometry.

The guest replies with `ready` only after resolving the semantic target and
recording a baseline snapshot of XDG Downloads:

```json
{"type":"ready","version":1,"transferId":"UUID","target":{"kind":"nautilus","uri":"file:///home/user/Documents","confidence":"high"}}
```

The host starts the existing asynchronous SPICE file-copy operation only after
a valid, matching `ready`. `target` is an informational account of the guest's
decision; it is never a destination instruction from the host.

After all expected files have arrived and have either been moved safely to the
resolved directory or retained in Downloads, the guest sends:

```json
{"type":"complete","version":1,"transferId":"UUID"}
```

Completion is a two-sided barrier. The host does not release the serialized
viewer-session transfer slot until both the ordinary SPICE payload operation
has completed and the matching guest `complete` has arrived. Either event can
arrive first. This prevents a later transfer from being confused with files
still being observed for the current transfer.

## Cancellation and ordinary fallback

Before payload transfer starts, a rejected, malformed, or timed-out metadata
exchange is disarmed with:

```json
{"type":"cancel","version":1,"transferId":"UUID"}
```

The guest clears matching active metadata, leaves any already received files
in Downloads, and acknowledges that state change:

```json
{"type":"cancelled","version":1,"transferId":"UUID"}
```

Cancellation is also a two-sided barrier. A host may downgrade that drop to an
ordinary SPICE transfer to Downloads only after both its asynchronous `cancel`
write has succeeded and the matching `cancelled` acknowledgement has arrived;
the order of those events is immaterial. A successful transport write alone
does not prove that the guest processed the cancellation.

If a semantic payload operation itself fails, the host uses the same barrier
to disarm guest state but does not start a second payload transfer.

When the named port is absent or not open at the beginning of a drop, there is
no armed semantic state and the frontend can immediately use ordinary SPICE
file transfer to Downloads.

There is one deliberate fail-closed limitation: if the port was open but the
guest does not acknowledge cancellation, the host cannot safely know whether
a destination remains armed. It therefore does not release a fallback payload,
poisons the current viewer session against further drops, and requires a viewer
reconnect plus guest-helper restart. The same fail-closed rule applies to an
active control-port disconnect, a failed cancel write, or a missing completion
acknowledgement. Sending a fallback despite this uncertainty could redirect it
to stale guest state.

## Errors

A guest that can associate a validation or resolution failure with a transfer
may reply:

```json
{"type":"error","version":1,"transferId":"UUID","error":"description"}
```

The host treats a matching pre-payload error like other metadata failure: it
performs the cancellation barrier before ordinary fallback. Diagnostic errors
that cannot be correlated to the active `transferId` do not authorize payload
release.

Host-provided paths are not accepted. Only basenames and sizes are metadata; the guest resolves and validates the destination.

## Validation limits

- Protocol version must be exactly `1`; unknown versions and message types are rejected.
- `transferId` must be a UUID.
- Display is an integer from 0 through 31.
- Coordinates and framebuffer dimensions must be finite, positive where applicable, and the point must be inside the framebuffer.
- A drop contains 1 through 100 files.
- Each file has exactly a UTF-8 basename (maximum 255 bytes) and a non-negative signed 64-bit size.
- Duplicate JSON members, non-finite numeric literals, excessive nesting, NULs,
  invalid UTF-8, unknown fields, and wrong JSON types are rejected.
- `drop`, `cancel`, `ready`, `cancelled`, and `complete` use exact schemas.
  A valid `ready.target` contains `kind`, `uri`, and `confidence`, with only the
  documented optional diagnostic fields.
- Destination paths and commands are not host-to-guest protocol fields.

The guest accepts only one active semantic transfer. The Linux viewer also
serializes semantic and ordinary file drops across all displays in one SPICE
session. A matching `cancel` disarms the guest transfer; a non-matching
`transferId` cannot cancel, complete, or release another transfer. Disconnecting
the guest port clears buffered partial input and active metadata before its
reconnect loop resumes.
