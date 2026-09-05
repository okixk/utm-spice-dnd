#!/usr/bin/python3

import argparse
import errno
import json
import math
import os
import re
import selectors
import shutil
import signal
import stat
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Optional

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
MAX_FILES = 100
DEFAULT_PORT = "/dev/virtio-ports/com.utmapp.dnd.0"
SHELL_BUS = "com.utmapp.Dnd.Target1"
SHELL_PATH = "/com/utmapp/Dnd/Target"
SHELL_INTERFACE = "com.utmapp.Dnd.Target1"
FILE_MANAGER_BUS = "org.freedesktop.FileManager1"
FILE_MANAGER_PATH = "/org/freedesktop/FileManager1"
FILE_MANAGER_INTERFACE = "org.freedesktop.FileManager1"
POLL_INTERVAL = 0.20
PORT_RETRY_INTERVAL = 1.0
STABLE_INTERVAL = 0.60
TRANSFER_TIMEOUT = 180.0
PORT_WRITE_TIMEOUT = 3.0


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ExpectedFile:
    name: str
    size: int


@dataclass
class CandidateState:
    size: int
    mtime_ns: int
    stable_since: float


@dataclass
class TransferSession:
    transfer_id: str
    files: list[ExpectedFile]
    destination: Path
    target: dict[str, Any]
    baseline: set[tuple[int, int]]
    started_wall_ns: int
    deadline: float
    matched: set[tuple[int, int]] = field(default_factory=set)
    candidates: dict[tuple[int, int], CandidateState] = field(default_factory=dict)
    moved: list[dict[str, Any]] = field(default_factory=list)


def normalized_name(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def validate_basename(name: Any) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ProtocolError("invalid file name")
    if name in (".", "..") or PurePath(name).name != name or "/" in name:
        raise ProtocolError("file name must be a basename")
    if len(name.encode("utf-8")) > 255:
        raise ProtocolError("file name is too long")
    return name


def validate_drop_message(message: Any) -> tuple[str, int, float, float, float, float, list[ExpectedFile]]:
    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    if message.get("type") != "drop":
        raise ProtocolError("unsupported message type")
    if message.get("version") != PROTOCOL_VERSION or isinstance(message.get("version"), bool):
        raise ProtocolError("unsupported protocol version")
    if set(message) != {
        "type", "version", "transferId", "display", "x", "y",
        "framebufferWidth", "framebufferHeight", "files",
    }:
        raise ProtocolError("invalid drop message fields")

    try:
        transfer_id = str(uuid.UUID(message["transferId"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ProtocolError("invalid transferId") from error

    display = message.get("display")
    if not isinstance(display, int) or isinstance(display, bool) or not 0 <= display <= 31:
        raise ProtocolError("invalid display")

    values = []
    for key in ("x", "y", "framebufferWidth", "framebufferHeight"):
        value = message.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ProtocolError(f"invalid {key}")
        values.append(float(value))
    x, y, width, height = values
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x >= width or y >= height:
        raise ProtocolError("drop coordinates are outside the framebuffer")

    raw_files = message.get("files")
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= MAX_FILES:
        raise ProtocolError("invalid files list")
    files = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise ProtocolError("invalid file entry")
        if set(raw_file) != {"name", "size"}:
            raise ProtocolError("invalid file entry fields")
        name = validate_basename(raw_file.get("name"))
        size = raw_file.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= (1 << 63) - 1:
            raise ProtocolError("invalid file size")
        files.append(ExpectedFile(name=name, size=size))

    return transfer_id, display, x, y, width, height, files


def validate_cancel_message(message: Any) -> str:
    if not isinstance(message, dict) or message.get("type") != "cancel":
        raise ProtocolError("unsupported message type")
    if message.get("version") != PROTOCOL_VERSION or isinstance(message.get("version"), bool):
        raise ProtocolError("unsupported protocol version")
    if set(message) != {"type", "version", "transferId"}:
        raise ProtocolError("invalid cancel message fields")
    try:
        return str(uuid.UUID(message["transferId"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ProtocolError("invalid transferId") from error


def name_matches_received(expected: str, candidate: str) -> bool:
    expected_normalized = normalized_name(expected)
    candidate_normalized = normalized_name(candidate)
    if candidate_normalized == expected_normalized:
        return True
    path = PurePath(expected_normalized)
    stem = path.stem
    suffix = path.suffix
    pattern = rf"^{re.escape(stem)} \([1-9][0-9]*\){re.escape(suffix)}$"
    return re.fullmatch(pattern, candidate_normalized) is not None


def duplicate_name(name: str, number: int) -> str:
    path = PurePath(name)
    return f"{path.stem} ({number}){path.suffix}"


def xdg_directory(kind: GLib.UserDirectory, fallback: str) -> Path:
    value = GLib.get_user_special_dir(kind)
    return Path(value if value else Path.home() / fallback)


def is_forbidden_destination(path: Path) -> bool:
    forbidden = (Path("/dev"), Path("/proc"), Path("/sys"), Path("/run"))
    return any(path == root or root in path.parents for root in forbidden)


class TargetResolver:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def _fallback(self, reason: str, diagnostic: Optional[dict[str, Any]] = None) -> tuple[Path, dict[str, Any]]:
        target = {
            "kind": "fallback",
            "uri": Gio.File.new_for_path(str(xdg_directory(GLib.UserDirectory.DIRECTORY_DOWNLOAD, "Downloads"))).get_uri(),
            "confidence": "low",
            "reason": reason,
        }
        if diagnostic:
            target["diagnostic"] = diagnostic
        return self._validate_path(
            xdg_directory(GLib.UserDirectory.DIRECTORY_DOWNLOAD, "Downloads"),
            target,
        )

    def _validate_path(self, path: Path, target: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        try:
            resolved = path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ProtocolError(f"destination cannot be resolved: {error}") from error
        if is_forbidden_destination(resolved):
            raise ProtocolError("destination is in a forbidden filesystem location")
        if not resolved.is_dir() or not os.access(resolved, os.W_OK | os.X_OK):
            raise ProtocolError("destination is not a writable directory")

        gio_file = Gio.File.new_for_path(str(resolved))
        try:
            info = gio_file.query_filesystem_info("filesystem::remote", None)
            if info.has_attribute("filesystem::remote") and info.get_attribute_boolean("filesystem::remote"):
                raise ProtocolError("remote filesystems are not supported")
        except GLib.Error as error:
            raise ProtocolError(f"cannot inspect destination filesystem: {error.message}") from error

        target = dict(target)
        target["uri"] = gio_file.get_uri()
        return resolved, target

    def _inspect_shell(
        self,
        display: int,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> dict[str, Any]:
        result = self.bus.call_sync(
            SHELL_BUS,
            SHELL_PATH,
            SHELL_INTERFACE,
            "InspectDrop",
            GLib.Variant("(idddd)", (display, x, y, width, height)),
            GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE,
            1500,
            None,
        )
        payload = result.unpack()[0]
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ProtocolError("invalid Shell target response")
        return value

    def _nautilus_locations(self) -> dict[str, list[str]]:
        result = self.bus.call_sync(
            FILE_MANAGER_BUS,
            FILE_MANAGER_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", (FILE_MANAGER_INTERFACE, "OpenWindowsWithLocations")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            1500,
            None,
        )
        value = result.unpack()[0]
        if isinstance(value, GLib.Variant):
            value = value.unpack()
        return value

    def resolve(
        self,
        display: int,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> tuple[Path, dict[str, Any]]:
        try:
            inspected = self._inspect_shell(display, x, y, width, height)
        except (GLib.Error, json.JSONDecodeError, ProtocolError) as error:
            return self._fallback(f"shell-inspection-failed: {error}")

        if inspected.get("kind") == "desktop":
            target = {"kind": "desktop", "confidence": inspected.get("confidence", "high"), "diagnostic": inspected}
            try:
                return self._validate_path(xdg_directory(GLib.UserDirectory.DIRECTORY_DESKTOP, "Desktop"), target)
            except ProtocolError as error:
                return self._fallback(f"desktop-unavailable: {error}", inspected)

        app_identity = f"{inspected.get('appId', '')} {inspected.get('wmClass', '')}".lower()
        if inspected.get("kind") == "window" and "nautilus" in app_identity:
            window_path = inspected.get("gtkWindowPath")
            if not isinstance(window_path, str) or not window_path:
                return self._fallback("nautilus-window-has-no-gtk-object-path", inspected)
            try:
                locations = self._nautilus_locations()
            except GLib.Error as error:
                return self._fallback(f"nautilus-dbus-failed: {error.message}", inspected)
            uris = locations.get(window_path, []) if isinstance(locations, dict) else []
            if len(uris) != 1:
                return self._fallback("nautilus-window-location-is-ambiguous", inspected)
            uri = uris[0]
            if not isinstance(uri, str) or not uri.startswith("file://"):
                return self._fallback("nautilus-location-is-not-local", inspected)
            gio_file = Gio.File.new_for_uri(uri)
            path = gio_file.get_path()
            if not path:
                return self._fallback("nautilus-location-has-no-local-path", inspected)
            target = {"kind": "nautilus", "confidence": "high", "diagnostic": inspected}
            try:
                return self._validate_path(Path(path), target)
            except ProtocolError as error:
                return self._fallback(f"nautilus-location-rejected: {error}", inspected)

        return self._fallback(inspected.get("reason", "unsupported-window"), inspected)


class GuestHelper:
    def __init__(self, port_path: Path, debug: bool = False):
        self.port_path = port_path
        self.debug = debug
        self.downloads = xdg_directory(GLib.UserDirectory.DIRECTORY_DOWNLOAD, "Downloads").resolve()
        self.resolver = TargetResolver(debug=debug)
        self.active: Optional[TransferSession] = None
        self.running = True
        self.fd: Optional[int] = None
        self.read_buffer = bytearray()

    def log(self, message: str) -> None:
        print(f"utm-dnd-guest: {message}", flush=True)

    def send(self, message: dict[str, Any]) -> None:
        if self.fd is None:
            return
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            self.log("refusing oversized response")
            return
        view = memoryview(encoded)
        deadline = time.monotonic() + PORT_WRITE_TIMEOUT
        while view and self.fd is not None:
            try:
                written = os.write(self.fd, view)
                view = view[written:]
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.log("control port write timed out")
                    self.close_port()
                    return
                time.sleep(0.01)
            except OSError as error:
                self.log(f"port write failed: {error}")
                self.close_port()
                return

    def close_port(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None
        self.read_buffer.clear()
        self.active = None

    def snapshot_downloads(self) -> set[tuple[int, int]]:
        identities = set()
        try:
            for entry in os.scandir(self.downloads):
                try:
                    info = entry.stat(follow_symlinks=False)
                    identities.add((info.st_dev, info.st_ino))
                except OSError:
                    continue
        except OSError as error:
            raise ProtocolError(f"cannot inspect Downloads: {error}") from error
        return identities

    def handle_message(self, message: Any) -> None:
        try:
            if isinstance(message, dict) and message.get("type") == "cancel":
                transfer_id = validate_cancel_message(message)
                if self.active and transfer_id == self.active.transfer_id:
                    self.log(f"transfer {transfer_id} cancelled by host; received files remain in Downloads")
                    self.active = None
                return

            transfer_id, display, x, y, width, height, files = validate_drop_message(message)
            if self.active:
                raise ProtocolError("another semantic transfer is active")
            destination, target = self.resolver.resolve(display, x, y, width, height)
            baseline = self.snapshot_downloads()
            self.active = TransferSession(
                transfer_id=transfer_id,
                files=files,
                destination=destination,
                target=target,
                baseline=baseline,
                started_wall_ns=time.time_ns(),
                deadline=time.monotonic() + TRANSFER_TIMEOUT,
            )
            expected_summary = [{"name": item.name, "size": item.size} for item in files]
            self.log(
                f"drop version={PROTOCOL_VERSION} id={transfer_id} files="
                f"{json.dumps(expected_summary, ensure_ascii=False, separators=(',', ':'))}"
            )
            self.log(
                f"target id={transfer_id} display={display} guest=({x:.1f},{y:.1f}) "
                f"kind={target.get('kind')} uri={target.get('uri')} confidence={target.get('confidence')}"
            )
            if self.debug:
                self.log(f"target diagnostic={json.dumps(target.get('diagnostic', {}), ensure_ascii=False)}")
            self.send({"type": "ready", "version": PROTOCOL_VERSION, "transferId": transfer_id, "target": target})
            self.log(f"ready version={PROTOCOL_VERSION} id={transfer_id}")
        except ProtocolError as error:
            transfer_id = None
            if isinstance(message, dict):
                try:
                    transfer_id = str(uuid.UUID(message.get("transferId")))
                except (TypeError, ValueError):
                    pass
            self.log(f"rejected control message: {error}")
            response = {"type": "error", "version": PROTOCOL_VERSION, "error": str(error)}
            if transfer_id:
                response["transferId"] = transfer_id
            self.send(response)

    def process_input(self, data: bytes) -> None:
        self.read_buffer.extend(data)
        if len(self.read_buffer) >= MAX_MESSAGE_BYTES and b"\n" not in self.read_buffer:
            self.log("dropping oversized control message")
            self.close_port()
            return
        while b"\n" in self.read_buffer:
            raw, _, remainder = self.read_buffer.partition(b"\n")
            self.read_buffer = bytearray(remainder)
            if not raw:
                continue
            if len(raw) + 1 > MAX_MESSAGE_BYTES:
                self.log("rejected oversized control message")
                continue
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self.log(f"invalid JSON: {error}")
                self.send({"type": "error", "version": PROTOCOL_VERSION, "error": "invalid JSON"})
                continue
            self.handle_message(message)

    def scan_candidates(self, session: TransferSession, expected: ExpectedFile) -> list[tuple[Path, os.stat_result]]:
        matches = []
        try:
            entries = list(os.scandir(self.downloads))
        except OSError as error:
            self.log(f"cannot scan Downloads: {error}")
            return matches
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            identity = (info.st_dev, info.st_ino)
            if identity in session.baseline or identity in session.matched:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected.size:
                continue
            # GIO/spice-vdagent can preserve the source mtime. The inode ctime is
            # local to the guest and therefore reflects creation in Downloads.
            if info.st_ctime_ns + 2_000_000_000 < session.started_wall_ns:
                continue
            if not name_matches_received(expected.name, entry.name):
                continue
            matches.append((Path(entry.path), info))
        matches.sort(key=lambda item: (item[1].st_ctime_ns, item[0].name))
        return matches

    def candidate_is_stable(self, session: TransferSession, path: Path, info: os.stat_result, now: float) -> bool:
        identity = (info.st_dev, info.st_ino)
        previous = session.candidates.get(identity)
        if previous is None or previous.size != info.st_size or previous.mtime_ns != info.st_mtime_ns:
            session.candidates[identity] = CandidateState(info.st_size, info.st_mtime_ns, now)
            return False
        return now - previous.stable_since >= STABLE_INTERVAL

    def safe_destination_path(self, directory: Path, name: str) -> Path:
        candidate = directory / name
        number = 0
        while candidate.exists() or candidate.is_symlink():
            number += 1
            candidate = directory / duplicate_name(name, number)
        return candidate

    def copy_exclusive(self, source: Path, destination: Path, source_stat: os.stat_result) -> None:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, source_stat.st_mode & 0o777)
            try:
                with os.fdopen(source_fd, "rb", closefd=False) as source_file, os.fdopen(destination_fd, "wb", closefd=False) as destination_file:
                    shutil.copyfileobj(source_file, destination_file, 1024 * 1024)
                    destination_file.flush()
                    os.fsync(destination_fd)
            except Exception:
                try:
                    destination.unlink()
                except OSError:
                    pass
                raise
            finally:
                os.close(destination_fd)
        finally:
            os.close(source_fd)
        source.unlink()

    def move_safely(
        self,
        source: Path,
        destination_directory: Path,
        destination_name: str,
    ) -> Path:
        source_stat = source.lstat()
        if not stat.S_ISREG(source_stat.st_mode):
            raise ProtocolError("received source is no longer a regular file")

        destination_directory, _ = self.resolver._validate_path(destination_directory, {})
        if destination_directory == self.downloads:
            return source

        for number in range(0, 10000):
            name = destination_name if number == 0 else duplicate_name(destination_name, number)
            destination = destination_directory / name
            try:
                os.link(source, destination, follow_symlinks=False)
                source.unlink()
                return destination
            except FileExistsError:
                continue
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                destination = self.safe_destination_path(destination_directory, destination_name)
                self.copy_exclusive(source, destination, source_stat)
                return destination
        raise ProtocolError("could not choose a non-colliding destination name")

    def tick_transfer(self) -> None:
        session = self.active
        if not session:
            return
        now = time.monotonic()
        if now >= session.deadline:
            self.log(f"transfer {session.transfer_id} timed out; unmatched files remain in Downloads")
            self.send({
                "type": "error",
                "version": PROTOCOL_VERSION,
                "transferId": session.transfer_id,
                "error": "timed out waiting for expected SPICE files",
                "received": session.moved,
            })
            self.active = None
            return

        expected = session.files[len(session.moved)]
        candidates = self.scan_candidates(session, expected)
        for source, info in candidates:
            if not self.candidate_is_stable(session, source, info, now):
                continue
            identity = (info.st_dev, info.st_ino)
            try:
                destination = self.move_safely(source, session.destination, expected.name)
            except (OSError, ProtocolError) as error:
                self.log(f"target move failed for {source.name}: {error}; leaving file in Downloads")
                destination = source
                session.target = {
                    "kind": "fallback",
                    "uri": Gio.File.new_for_path(str(self.downloads)).get_uri(),
                    "confidence": "low",
                    "reason": f"target-disappeared: {error}",
                }
            session.matched.add(identity)
            moved = {"expected": expected.name, "received": source.name, "final": str(destination), "size": info.st_size}
            session.moved.append(moved)
            self.log(f"transfer id={session.transfer_id} received={source.name} moved={destination}")
            break

        if len(session.moved) == len(session.files):
            self.send({
                "type": "complete",
                "version": PROTOCOL_VERSION,
                "transferId": session.transfer_id,
                "target": session.target,
                "files": session.moved,
            })
            self.log(f"transfer {session.transfer_id} complete")
            self.active = None

    def run_connected(self) -> None:
        selector = selectors.DefaultSelector()
        selector.register(self.fd, selectors.EVENT_READ)
        try:
            while self.running and self.fd is not None:
                for key, _ in selector.select(POLL_INTERVAL):
                    try:
                        data = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError as error:
                        self.log(f"port read failed: {error}")
                        self.close_port()
                        break
                    if not data:
                        self.log("control port disconnected")
                        self.close_port()
                        break
                    self.process_input(data)
                self.tick_transfer()
        finally:
            selector.close()

    def run(self) -> int:
        self.log(f"waiting for {self.port_path}")
        while self.running:
            try:
                self.fd = os.open(self.port_path, os.O_RDWR | os.O_NONBLOCK)
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.EACCES, errno.EBUSY):
                    self.log(f"cannot open control port: {error}")
                time.sleep(PORT_RETRY_INTERVAL)
                continue
            self.log("control port connected")
            self.run_connected()
        self.close_port()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="UTM target-aware drag-and-drop guest helper")
    parser.add_argument("--port", type=Path, default=Path(DEFAULT_PORT))
    parser.add_argument("--debug", action="store_true", default=os.environ.get("UTM_DND_DEBUG") == "1")
    args = parser.parse_args()

    helper = GuestHelper(args.port, debug=args.debug)

    def stop(_signum: int, _frame: Any) -> None:
        helper.running = False
        helper.close_port()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    return helper.run()


if __name__ == "__main__":
    raise SystemExit(main())
