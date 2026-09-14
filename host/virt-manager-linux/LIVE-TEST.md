# Linux host live-test report

This report records the development-host run on 2026-09-09 and 2026-09-12.
All drag cases below used real Nautilus pointer drags into graphical consoles;
an API return alone was not accepted as success.

## Revisions and environment

The project checkout was
`https://github.com/okixk/utm-spice-dnd.git`, branch
`feature/virt-manager-qemu`, based on project commit
`f23890a1df25a43d99a3d3c15409810feac93360`. The captured implementation was
committed as `bd00069` and pushed to the feature branch without merging main.

| Component | Tested version/base |
| --- | --- |
| Host | Ubuntu 26.04.1 LTS, kernel 7.0.0-31-generic, x86_64 |
| Host session | GNOME Shell 50.1, Wayland |
| virt-manager | Ubuntu `1:5.1.0-1`; upstream `eb4898b19e550af19daea49ae5ed15d2d70a2fc4` |
| virt-viewer | locally extracted Ubuntu 11.0-4; source base `8d8923d259b79c7c9ad3560a49be81dc0f38b88b` |
| spice-gtk | Ubuntu `0.42-4build1`; private patched base `f04479c16f0969fb394ebe74b6eff74e560a42f0` |
| libvirt | 12.0.0 (`12.0.0-1ubuntu5.3`) |
| QEMU | 10.2.1 (`1:10.2.1+ds-1ubuntu3.2`) |
| Python/GI | Python 3.14.4, PyGObject 3.56.2 |
| GTK | 3.24.52 |
| Guest | Ubuntu 26.04.1 LTS, GNOME Shell 50.1, Wayland |

virt-manager was the distribution package. Its relevant installed sources
matched the stated upstream revision. virt-viewer was absent initially and was
run from an isolated local extraction. No distro-managed file under `/usr` was
modified.

## Baseline and layer choice

Source inspection and live baselines showed that virt-manager embeds
`SpiceClientGtk.Display` without intercepting URI-list drops. spice-gtk owns the
drop coordinates and immediately calls
`spice_main_channel_file_copy_async()`. Unmodified distro virt-manager copied
`baseline.txt` to guest Downloads with matching SHA-256
`250211f1268a25de7c6b6703d2094a71f961cf9c572ec8adb477cd631cc25a9f`.
Unmodified virt-viewer copied the same payload to `baseline (1).txt` with the
same hash.

The patch therefore lives in the private spice-gtk build. It defers, but does
not duplicate, the existing standard SPICE payload transfer. No
virt-manager-specific Python transfer code was added.

## VM transport evidence

Only the named `ubuntu-2604` QEMU/KVM domain was enabled. Its persistent XML
contains:

```xml
<graphics type='spice' ...>
  <filetransfer enable='yes'/>
</graphics>
<channel type='spiceport'>
  <source channel='com.utmapp.dnd.0'/>
  <target type='virtio' name='com.utmapp.dnd.0'/>
</channel>
```

The live QEMU process contained:

```text
-chardev spiceport,id=charchannel2,name=com.utmapp.dnd.0
-device {"driver":"virtserialport","bus":"virtio-serial0.0","nr":3,"chardev":"charchannel2","id":"channel2","name":"com.utmapp.dnd.0"}
```

`disable-agent-file-xfer` was absent. While either patched viewer was attached,
libvirt reported the channel target `state='connected'`; after viewer exit it
correctly returned to `disconnected`. The guest resolved
`/dev/virtio-ports/com.utmapp.dnd.0` to `/dev/vport2p3`. Its ACL granted user
`oki` `rw-` and other `---`; both the user helper and spice-vdagent were active,
and the helper journal contained `control port connected`.

## Coordinate and handshake design

The implementation reuses spice-gtk's monitor-aware input transform. It first
maps the GTK widget point through host widget scaling, display scaling,
letterboxing and the active monitor area, then sends integer guest framebuffer
coordinates together with display ID and framebuffer dimensions. Unit cases
cover exact scaling, letterbox rejection, HiDPI-equivalent ratios, scrolling,
fullscreen-sized allocations, bounds, and non-zero monitor origins.

Protocol v1 sends one length-prefixed JSON `drop` frame (maximum 64 KiB) with a
UUID transfer ID, mapped display geometry, and validated file basenames/sizes.
Only a strict matching `ready` releases the ordinary SPICE file transfer. The
guest returns a compact matching `complete` after it has safely placed all
files. Timeout, malformed response, payload error, or cancellation enters a
matching `cancel`/`cancelled` barrier before another drop can use the named
port. The guest, never the host, resolves the destination.

## Real-drag matrix

| Case | Viewer and observed result | SHA-256 evidence |
| --- | --- | --- |
| Desktop | virt-manager -> `~/Desktop/desktop-target.txt`; resolver reported Desktop background | `d2eb8edf3e67835349a52052c9900c94fc66424a35ab4faa1a1912ec674c932a` both |
| Documents | virt-manager -> `~/Documents/documents-target.txt`; Nautilus URI | `31575a90d8e533a4a208998c457b76d065cd17422ba6f97ef5d7eb32bd15b104` both |
| Nested directory | virt-manager -> `~/Documents/Nested/nested-target.txt` | `b720217967256f9d39265c4adb526b83c76e23c57ddb63b7a1476ad62e64f3c7` both |
| Unsupported app | virt-manager/Calculator -> `~/Downloads/unsupported-target.txt` | `a34b148a0015bc95887f97d48e5ffefc7d180c45dfeea2a94e09f2e8a9c26d04` both |
| Multiple files | one drag -> Nested; `multi-a (1).txt` safely renamed and `multi-b.txt` | `cab30368d3d048043c6b25aea4a2c0ae1094b752e394bc4c243aafcdccd4cfc8` and `1319f0f00ff6738f736165d705d23d41d7b6c5929e8c1041ccdd04c810e7c004`, each both |
| Unicode | `Grüezi_日本_🚀.txt` -> Documents | `d16db5f9059322d0de128f99a7252d1c900c3d4a53bcd9cb7488c5ed9ecf22fe` both |
| Zero byte | zero-byte file -> Downloads | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` both |
| Existing name | original `collision.txt` retained; incoming -> `collision (1).txt` | original `1a80a4076747a269471812b8af65e01a82a68ff047103b250c01ae9794429bb4`; incoming `0a32fedeedbb958a75a830f39a7db4fd05138ffb87ce56cf7a47d0919ed9ff08` both |
| Helper stopped | ordinary spice-vdagent fallback -> Downloads | `ec0d1d0c2b5e7068581cdf4ed56d58f55ff9efd561baac85b7ebf5c228fc493a` both |
| Helper restarted | target-aware drop restored -> Desktop | `490fd33831c53cf9b5abb63f8ee3b74f4a13c5095fb0abdbd7b31d7a6fb6dffb` both |
| Guest agent unavailable | metadata became ready, payload failed; matching cancellation completed; no partial destination file | expected failure passed |
| VM restart/reconnect | domain restarted; device, ACL, helper, agent, QEMU channel and subsequent DnD reverified | passed |
| Metadata timeout | delayed responder crossed 5 s deadline; host cancelled, received acknowledgement, then copied to Downloads | `00ad4b69ca271d9d5ce4c965b923b0f0f14172dbc47e1faf3118a6b0c7294b05` both |
| Malformed response | responder sent malformed JSON; host cancelled, received acknowledgement, then copied to Downloads | `a99d452be68cadc1f5723600ae45fd901f80a40e015cfcac0529048e2d992394` both |
| Active cancellation | 1 GiB transfer interrupted by agent disconnect; cancellation acknowledged; no final or partial destination remained | source `49bc20df15e412a64472421e13fe86ff1c5165e18b2afccf160d4dc19fe68a14`; expected cancellation passed |

The patched virt-viewer additionally completed a target-aware Desktop drop of
`virt-viewer-documents.txt` with host/guest hash
`23710117d2d0caa21763200bedc0f02be602aeba7d3de6274b77053723763bc1`.
Attempts made while an unsupported/topmost guest layout was active correctly
went to Downloads. A deliberate Calculator drop of
`virt-viewer-fallback.txt` reached Downloads with matching hash
`c1cb000a1d0ac91c358704ad4aedf34f397edcc5c9c7890a6ba0b3ff111fff55`.
Exact Documents targeting was proven in virt-manager, not separately claimed
for virt-viewer.

## Automated verification

- Host Python suite: 57 passed.
- Guest Python suite: 59 passed.
- Combined measured branch coverage: 81%.
- Patched spice-gtk Meson suite: 13 passed.
- `git diff --check`: clean at report update time.

The isolated installer completed from a clean build directory. Its launchers
loaded the private spice-gtk and staged usbredir/libusb runtime rather than
distro libraries. `enable-vm.sh` created an XML backup, printed a secret-safe
managed projection diff, added no duplicate on repeat operation, and verified
the defined XML. A live tooling cycle was also performed while only
`ubuntu-2604` was shut off: `disable-vm.sh` removed only the exact DnD channel
and created a mode-0600 backup; `enable-vm.sh` restored it and created another
mode-0600 backup. The inactive XML before disable and after re-enable was
identical. After restart, the same IP, device, ACL, helper/agent status,
connected journal event, and QEMU spiceport/virtserialport arguments were
reverified. `uninstall.sh` removes only the isolated host build, not packages or
domain XML.

## Remaining limitations

1. Safe fallback requires a matching `cancelled` acknowledgement once a peer
   has accepted metadata. If that peer hangs forever after `ready`, the host
   intentionally fails closed instead of releasing an ordinary transfer that
   could be mis-associated with stale guest state. Thus the requested ordinary
   Downloads fallback cannot be guaranteed for a non-acknowledging peer.
2. Protocol v1 associates incoming payloads by validated basename, size and
   temporal state. A concurrent same-name/same-size decoy is not bound by a
   digest, nonce, or spice-vdagent transfer ID. Protocol v2 should add such an
   identity while keeping `com.utmapp.dnd.0` migration compatibility.
3. Multiple-monitor mapping is unit-tested against spice-gtk geometry but was
   not exercised with a physical multi-monitor SPICE guest in this run.
4. The VM XML controller has a small unavoidable check/define concurrency
   window; post-define semantic verification detects an unexpected result.
5. The feature remains a source-built development patch. No upstream PR was
   submitted.

The recommended upstream split is a generic cancellable/deferred drop API plus
coordinate exposure in spice-gtk, with the frontend-neutral semantic policy as
its consumer. virt-manager and virt-viewer should need no duplicated transfer
implementation. Existing libvirt spiceport and file-transfer XML are adequate.
