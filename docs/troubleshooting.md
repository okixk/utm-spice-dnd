# Troubleshooting

## Port missing or inaccessible

Inside the guest:

```sh
readlink -f /dev/virtio-ports/com.utmapp.dnd.0
stat /dev/virtio-ports/com.utmapp.dnd.0
getfacl /dev/virtio-ports/com.utmapp.dnd.0
```

The expected access rule is:

```text
SUBSYSTEM=="virtio-ports", ATTR{name}=="com.utmapp.dnd.0", MODE="0660", TAG+="uaccess"
```

The ACL is granted dynamically to the active local logind seat. An SSH session alone does not guarantee that ACL. Log out and back in, or reboot the guest, after installing the rule.

## Helper status

```sh
systemctl --user status utm-dnd-guest.service
journalctl --user -u utm-dnd-guest.service -f
```

The service intentionally waits and retries when the virtio port is not yet present. It does not require root.

## Files arrive in Downloads

This is the safe fallback when the helper is unavailable, target inspection fails, or the target is unsupported. Confirm that the GNOME Shell extension is active:

```sh
gnome-extensions info utm-dnd-target@utmapp.dev
```

Remote Nautilus locations, search/recent/trash views, directories, and ambiguous targets are not moved into arbitrary paths.

On Windows, verify the interactive task, SPICE processes, and exact port path:

```powershell
Get-ScheduledTask -TaskName 'UTM DnD Guest'
Get-Process vdservice,vdagent,utm-dnd-guest
& "$env:LOCALAPPDATA\Programs\UTM DnD Guest\utm-dnd-guest.exe" --probe-port
Get-Content "$env:LOCALAPPDATA\UTM DnD Guest\Logs\utm-dnd-guest.log" -Tail 50
```

Exit code 2 from `--probe-port` means the custom port is absent/unavailable; exit code 5 means access was denied. Do not change broad device ACLs. `explorer-tabs-ambiguous` means Windows exposed multiple different tab paths for the same frame HWND, so Downloads was chosen intentionally.

## Development app networking errors

The ad-hoc UTM development app may not have the entitlements of the installed UTM build. Try QEMU user networking for development VM connectivity. This is separate from SPICE DnD, which does not use SSH or guest IP networking.
