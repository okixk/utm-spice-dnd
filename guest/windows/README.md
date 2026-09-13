# Windows guest helper

This component adds target-aware placement for Windows guests while retaining the existing SPICE VDAgent payload path and protocol version 1. The helper is a self-contained .NET 8 `win-arm64` or `win-x64` GUI executable. It runs unelevated in the logged-in user's interactive session through Task Scheduler; it is not a LocalSystem service and does not use SSH at runtime.

## Requirements

- Windows 10 or newer on ARM64 or x64;
- UTM Windows Guest Tools with the Red Hat VirtIO Serial driver, `vdservice`, and interactive `vdagent`;
- a patched UTM host exposing `com.utmapp.dnd.0`;
- ordinary SPICE file transfer working before this helper is installed.

The observed Windows VDAgent staging folder is the current user's Downloads known folder. The helper resolves Desktop and Downloads through `SHGetKnownFolderPath`; no user profile path is hardcoded.

## Build

Install a .NET 8 SDK on the Mac or another development machine. Visual Studio and the .NET runtime are not required in the guest:

```powershell
dotnet publish src/UtmDndGuest/UtmDndGuest.csproj `
  -c Release -r win-arm64 --self-contained true `
  -o artifacts/win-arm64
```

Use `win-x64` instead for an x64 guest. The project publishes a PE GUI subsystem executable, so normal logon startup has no console window.

The dependency-free Windows test runner is built and run with:

```powershell
dotnet publish tests/UtmDndGuest.Tests/UtmDndGuest.Tests.csproj `
  -c Release -r win-arm64 --self-contained true `
  -p:PublishSingleFile=true -o artifacts/tests-win-arm64
artifacts/tests-win-arm64/UtmDndGuest.Tests.exe
```

## Install

Run as the intended desktop user. Elevation is only needed if local Task Scheduler policy requires it:

```powershell
.\install.ps1
```

For a separately staged binary:

```powershell
.\install.ps1 -BinaryPath C:\path\to\utm-dnd-guest.exe
```

The installer verifies Windows/architecture, `vdservice`, `vdagent`, the signed VirtIO Serial driver, and user access to `\\.\Global\com.utmapp.dnd.0`. It never changes device ACLs. It installs only:

- `%LOCALAPPDATA%\Programs\UTM DnD Guest\utm-dnd-guest.exe`;
- scheduled task `\UTM DnD Guest`, triggered at logon with an interactive token and limited run level.

Runtime logs are written to `%LOCALAPPDATA%\UTM DnD Guest\Logs\utm-dnd-guest.log`.

Task Scheduler is used because Explorer COM objects, the shell desktop, `WindowFromPoint`, and per-monitor coordinates belong to the interactive session. A service in session 0 cannot resolve these targets correctly.

## Resolution and safety

At startup the helper declares Per-Monitor-V2 awareness both in its manifest and with `SetProcessDpiAwarenessContext`. Framebuffer coordinates are scaled into the selected monitor's physical rectangle before `WindowFromPoint` is called.

The shell desktop is recognized from the live Win32 ancestry (`Progman`/`WorkerW`, `SHELLDLL_DefView`, and `SysListView32`) and resolves to `FOLDERID_Desktop`. Explorer targets require a `CabinetWClass`/`ExploreWClass` root and exactly one matching `Shell.Application.Windows()` entry by root HWND; the path comes from `Document.Folder.Self.Path`. If Windows 11 exposes more than one tab entry for the frame, the helper reports `explorer-tabs-ambiguous` and uses Downloads instead of guessing the inactive or active tab.

Only existing writable directories on fixed/removable local drives are accepted. UNC, device, namespace, reparse-point, and Windows/Program Files/ProgramData destinations are rejected. The host cannot submit a destination or command.

Before `ready`, the helper records a Downloads baseline using volume/file IDs. It then matches only a new normal file with the expected basename (including VDAgent's `name (N).ext` convention), expected size, fresh creation time, and stable size/write time. Transfers are serialized. Unmatched files stay in Downloads. Moves on one volume use a no-overwrite filesystem move; cross-volume placement uses an exclusive copy, flush, length check, then source deletion. Destination collisions become `name (1).ext`, `name (2).ext`, and so on.

## Uninstall

```powershell
.\uninstall.ps1
```

Uninstall removes the scheduled task and installed executable. It does not remove UTM/SPICE Guest Tools, VirtIO drivers, received user files, or the runtime log.

## Current limits

- host to guest only;
- regular files only;
- local fixed/removable Explorer folders only;
- one semantic transfer at a time;
- staging association is intentionally conservative but is not cryptographically bound to a SPICE task;
- an ambiguous Windows 11 tab mapping falls back to Downloads;
- multi-monitor ordering uses primary monitor first, then top/left order and needs broader SPICE multi-display testing.
