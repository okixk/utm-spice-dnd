#!/usr/bin/python3

import importlib.util
import errno
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "guest" / "utm_dnd_guest.py"
SPEC = importlib.util.spec_from_file_location("utm_dnd_guest", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ProtocolTests(unittest.TestCase):
    def valid_message(self):
        return {
            "type": "drop",
            "version": 1,
            "transferId": str(uuid.uuid4()),
            "display": 0,
            "x": 100,
            "y": 200,
            "framebufferWidth": 1280,
            "framebufferHeight": 800,
            "files": [{"name": "hello world.txt", "size": 12}],
        }

    def test_run_backs_off_after_immediate_port_disconnect(self):
        helper = object.__new__(MODULE.GuestHelper)
        helper.port_path = Path("/dev/fake-control-port")
        helper.running = True
        helper.fd = None
        helper.log = lambda _message: None

        def disconnect_immediately():
            helper.fd = None

        def stop_after_backoff(_duration):
            helper.running = False

        with patch.object(MODULE.os, "open", return_value=19), \
             patch.object(helper, "run_connected", side_effect=disconnect_immediately), \
             patch.object(helper, "close_port"), \
             patch.object(MODULE.time, "sleep", side_effect=stop_after_backoff) as sleep:
            self.assertEqual(helper.run(), 0)

        sleep.assert_called_once_with(MODULE.PORT_RETRY_INTERVAL)

    def test_valid_drop(self):
        result = MODULE.validate_drop_message(self.valid_message())
        self.assertEqual(result[-1][0], MODULE.ExpectedFile("hello world.txt", 12))

    def test_rejects_non_string_uuid_surrogates_and_huge_coordinates(self):
        for key, value in (("transferId", 3), ("transferId", []), ("x", 10 ** 400)):
            with self.subTest(key=key, value=str(value)[:20]):
                message = {**self.valid_message(), key: value}
                with self.assertRaises(MODULE.ProtocolError):
                    MODULE.validate_drop_message(message)
        message = {**self.valid_message(), "files": [{"name": "\ud800", "size": 0}]}
        with self.assertRaises(MODULE.ProtocolError):
            MODULE.validate_drop_message(message)

    def test_staging_and_restoration_never_overwrite(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "file.txt"
            source.write_bytes(b"payload")
            identifier = uuid.uuid4()
            occupied = source.with_name(f".utm-spice-dnd-{identifier}.stage")
            occupied.write_bytes(b"existing")
            with patch.object(MODULE.uuid, "uuid4", return_value=identifier):
                with self.assertRaises(FileExistsError):
                    helper.stage_source(source, source.stat())
            self.assertEqual(occupied.read_bytes(), b"existing")
            self.assertEqual(source.read_bytes(), b"payload")
            staging = helper.stage_source(source, source.stat())
            source.write_bytes(b"replacement")
            restored = helper.restore_staged_source(staging, source)
            self.assertEqual(restored.name, "file (1).txt")
            self.assertEqual(restored.read_bytes(), b"payload")
            self.assertEqual(source.read_bytes(), b"replacement")

    def test_changed_source_is_restored_without_overwrite(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "file.txt"
            source.write_bytes(b"original")
            info = source.stat()
            source.write_bytes(b"changed-size")
            with self.assertRaises(MODULE.ProtocolError):
                helper.stage_source(source, info)
            self.assertEqual(source.read_bytes(), b"changed-size")

    def test_scan_candidate_replacement_is_not_moved(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "file.txt"
            source.write_bytes(b"original")
            info = source.stat()
            source.rename(source.with_suffix(".saved"))
            source.write_bytes(b"replaced")
            with self.assertRaises(MODULE.ProtocolError):
                helper.move_safely(source, Path(directory), "file.txt", info)
            self.assertEqual(source.read_bytes(), b"replaced")

    def test_failed_move_restores_payload_beside_concurrent_source(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads, destination = root / "Downloads", root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test.txt"
            source.write_bytes(b"payload")
            helper.downloads = downloads
            helper.resolver = SimpleNamespace(_validate_path=lambda path, target: (path, target))

            def fail_move(*args, **kwargs):
                source.write_bytes(b"replacement")
                raise PermissionError("destination unavailable")

            with patch.object(MODULE, "link_fd_no_replace", side_effect=fail_move):
                with self.assertRaises(PermissionError):
                    helper.move_safely(source, destination, source.name)
            self.assertEqual(source.read_bytes(), b"replacement")
            self.assertEqual((downloads / "test (1).txt").read_bytes(), b"payload")

    def test_rejects_host_path(self):
        message = self.valid_message()
        message["files"][0]["name"] = "../../etc/passwd"
        with self.assertRaises(MODULE.ProtocolError):
            MODULE.validate_drop_message(message)

    def test_rejects_outside_framebuffer(self):
        message = self.valid_message()
        message["x"] = 1280
        with self.assertRaises(MODULE.ProtocolError):
            MODULE.validate_drop_message(message)

    def test_rejects_unknown_type_and_version(self):
        message = self.valid_message()
        message["type"] = "execute"
        with self.assertRaisesRegex(MODULE.ProtocolError, "unsupported message type"):
            MODULE.validate_drop_message(message)

        message = self.valid_message()
        message["version"] = 2
        with self.assertRaisesRegex(MODULE.ProtocolError, "unsupported protocol version"):
            MODULE.validate_drop_message(message)

    def test_rejects_missing_and_extra_fields(self):
        message = self.valid_message()
        del message["display"]
        with self.assertRaises(MODULE.ProtocolError):
            MODULE.validate_drop_message(message)

        message = self.valid_message()
        message["destination"] = "/etc"
        with self.assertRaisesRegex(MODULE.ProtocolError, "invalid drop message fields"):
            MODULE.validate_drop_message(message)

    def test_rejects_invalid_file_metadata(self):
        message = self.valid_message()
        message["files"][0]["size"] = -1
        with self.assertRaises(MODULE.ProtocolError):
            MODULE.validate_drop_message(message)

        message = self.valid_message()
        message["files"] = [{"name": "test", "size": 1, "path": "/tmp/test"}]
        with self.assertRaisesRegex(MODULE.ProtocolError, "invalid file entry fields"):
            MODULE.validate_drop_message(message)

        message = self.valid_message()
        message["files"] = [{"name": f"file-{number}", "size": 1} for number in range(MODULE.MAX_FILES + 1)]
        with self.assertRaisesRegex(MODULE.ProtocolError, "invalid files list"):
            MODULE.validate_drop_message(message)

    def test_cancel_validation(self):
        identifier = str(uuid.uuid4())
        self.assertEqual(MODULE.validate_cancel_message({
            "type": "cancel", "version": 1, "transferId": identifier,
        }), identifier)
        with self.assertRaises(MODULE.ProtocolError):
            MODULE.validate_cancel_message({
                "type": "cancel", "version": 2, "transferId": identifier,
            })

    def test_invalid_message_has_no_resolver_side_effect(self):
        helper = object.__new__(MODULE.GuestHelper)
        helper.active = None
        helper.fd = None
        helper.logs = []
        helper.responses = []
        helper.log = helper.logs.append
        helper.send = helper.responses.append
        helper.resolver = SimpleNamespace(resolve=lambda *args: self.fail("resolver must not be called"))
        helper.snapshot_downloads = lambda: self.fail("Downloads must not be inspected")

        helper.handle_message({"type": "execute", "version": 1, "command": "touch /tmp/bad"})

        self.assertIsNone(helper.active)
        self.assertEqual(helper.responses[0]["type"], "error")

    def test_matching_cancel_clears_active_session(self):
        identifier = str(uuid.uuid4())
        helper = object.__new__(MODULE.GuestHelper)
        helper.active = SimpleNamespace(transfer_id=identifier)
        helper.fd = None
        helper.log = lambda _message: None
        helper.responses = []
        helper.send = helper.responses.append

        helper.handle_message({"type": "cancel", "version": 1, "transferId": identifier})

        self.assertIsNone(helper.active)
        self.assertEqual(helper.responses, [{
            "type": "cancelled",
            "version": 1,
            "transferId": identifier,
        }])

    def test_cancel_without_active_session_is_acknowledged_as_barrier(self):
        identifier = str(uuid.uuid4())
        helper = object.__new__(MODULE.GuestHelper)
        helper.active = None
        helper.fd = None
        helper.log = lambda _message: None
        helper.responses = []
        helper.send = helper.responses.append

        helper.handle_message({"type": "cancel", "version": 1, "transferId": identifier})

        self.assertEqual(helper.responses[0]["type"], "cancelled")
        self.assertEqual(helper.responses[0]["transferId"], identifier)

    def test_cancel_for_another_id_does_not_acknowledge_while_active(self):
        active_identifier = str(uuid.uuid4())
        cancelled_identifier = str(uuid.uuid4())
        helper = object.__new__(MODULE.GuestHelper)
        active = SimpleNamespace(transfer_id=active_identifier)
        helper.active = active
        helper.fd = None
        helper.log = lambda _message: None
        helper.responses = []
        helper.send = helper.responses.append

        helper.handle_message({
            "type": "cancel",
            "version": 1,
            "transferId": cancelled_identifier,
        })

        self.assertIs(helper.active, active)
        self.assertEqual(helper.responses, [{
            "type": "error",
            "version": 1,
            "transferId": cancelled_identifier,
            "error": "another semantic transfer is active",
        }])

    def test_spice_duplicate_name_matching(self):
        self.assertTrue(MODULE.name_matches_received("test.txt", "test (1).txt"))
        self.assertTrue(MODULE.name_matches_received("archive.tar.gz", "archive.tar (2).gz"))
        self.assertFalse(MODULE.name_matches_received("test.txt", "other (1).txt"))

    def test_duplicate_name_stays_within_name_max(self):
        candidate = MODULE.duplicate_name(f"{'a' * 251}.txt", 1)
        self.assertLessEqual(len(candidate.encode("utf-8")), 255)
        self.assertTrue(candidate.endswith(" (1).txt"))

    def test_zero_length_port_write_times_out(self):
        helper = object.__new__(MODULE.GuestHelper)
        helper.fd = 19
        helper.logs = []
        helper.log = helper.logs.append
        helper.close_port = lambda: setattr(helper, "fd", None)

        with patch.object(MODULE.os, "write", return_value=0), \
             patch.object(MODULE.time, "monotonic", side_effect=(1.0, 2.0, 5.0)), \
             patch.object(MODULE.time, "sleep"):
            helper.send({"type": "test"})

        self.assertIsNone(helper.fd)
        self.assertIn("control port write timed out", helper.logs)

    def test_completion_response_is_compact(self):
        identifier = str(uuid.uuid4())
        helper = object.__new__(MODULE.GuestHelper)
        helper.active = MODULE.TransferSession(
            transfer_id=identifier,
            files=[MODULE.ExpectedFile("file.txt", 1)],
            destination=Path("/tmp"),
            target={"kind": "desktop", "uri": "file:///tmp", "confidence": "high"},
            baseline=set(),
            started_wall_ns=0,
            deadline=10.0,
        )
        fake_info = SimpleNamespace(st_dev=1, st_ino=2, st_size=1)
        helper.scan_candidates = lambda _session, _expected: [(Path("/tmp/file.txt"), fake_info)]
        helper.candidate_is_stable = lambda *_args: True
        helper.move_safely = lambda *_args: Path("/tmp/final.txt")
        helper.log = lambda _message: None
        helper.responses = []
        helper.send = helper.responses.append

        with patch.object(MODULE.time, "monotonic", return_value=1.0):
            helper.tick_transfer()

        self.assertEqual(helper.responses, [{
            "type": "complete",
            "version": 1,
            "transferId": identifier,
        }])
        self.assertIsNone(helper.active)

    def test_unicode_normalization(self):
        self.assertTrue(MODULE.name_matches_received("café.txt", "cafe\u0301.txt"))

    def test_safe_destination_collision(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.txt").write_bytes(b"old")
            self.assertEqual(helper.safe_destination_path(root, "test.txt").name, "test (1).txt")

    def test_spice_renamed_source_uses_expected_destination_name(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "Downloads"
            destination = root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test (1).txt"
            source.write_bytes(b"transferred")
            helper.downloads = downloads
            helper.resolver = object.__new__(MODULE.TargetResolver)
            helper.resolver._validate_path = lambda path, target: (path, target)

            moved = helper.move_safely(source, destination, "test.txt")

            self.assertEqual(moved, destination / "test.txt")
            self.assertEqual(moved.read_bytes(), b"transferred")
            self.assertFalse(source.exists())

    def test_same_filesystem_move_does_not_unlink_replaced_staging_path(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "Downloads"
            destination = root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test.txt"
            source.write_bytes(b"original")
            helper.downloads = downloads
            helper.resolver = SimpleNamespace(_validate_path=lambda path, target: (path, target))
            helper.logs = []
            helper.log = helper.logs.append
            real_link = MODULE.link_fd_no_replace
            displaced_staging = root / "displaced-staging"

            def link_then_replace(staging_fd, final):
                real_link(staging_fd, final)
                staging = next(downloads.glob(".utm-spice-dnd-*.stage"))
                staging.rename(displaced_staging)
                staging.write_bytes(b"replacement")

            with patch.object(MODULE, "link_fd_no_replace", side_effect=link_then_replace):
                moved = helper.move_safely(source, destination, "test.txt")

            self.assertEqual(moved.read_bytes(), b"original")
            self.assertEqual(source.read_bytes(), b"replacement")
            self.assertEqual(displaced_staging.read_bytes(), b"original")
            self.assertTrue(any("preserved staging pathname replacement" in message for message in helper.logs))

    def test_same_filesystem_move_uses_verified_inode_if_staging_swaps_before_link(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "Downloads"
            destination = root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test.txt"
            source.write_bytes(b"original")
            helper.downloads = downloads
            helper.resolver = SimpleNamespace(_validate_path=lambda path, target: (path, target))
            helper.logs = []
            helper.log = helper.logs.append
            real_link = MODULE.link_fd_no_replace
            displaced_staging = root / "displaced-staging"

            def swap_before_link(staging_fd, final):
                staging = next(downloads.glob(".utm-spice-dnd-*.stage"))
                staging.rename(displaced_staging)
                staging.write_bytes(b"replacement")
                return real_link(staging_fd, final)

            with patch.object(MODULE, "link_fd_no_replace", side_effect=swap_before_link):
                moved = helper.move_safely(source, destination, "test.txt")

            self.assertEqual(moved.read_bytes(), b"original")
            self.assertEqual(source.read_bytes(), b"replacement")
            self.assertEqual(displaced_staging.read_bytes(), b"original")

    def test_cross_filesystem_copy_does_not_unlink_replaced_staging_path(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "Downloads"
            destination = root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test.txt"
            source.write_bytes(b"original")
            helper.downloads = downloads
            helper.resolver = object.__new__(MODULE.TargetResolver)
            helper.resolver._validate_path = lambda path, target: (path, target)
            helper.logs = []
            helper.log = helper.logs.append

            real_copy = MODULE.shutil.copyfileobj
            displaced_staging = root / "displaced-staging"

            def replace_during_copy(source_file, destination_file, length):
                staging = next(downloads.glob(".utm-spice-dnd-*.stage"))
                staging.rename(displaced_staging)
                staging.write_bytes(b"replacement")
                return real_copy(source_file, destination_file, length)

            with patch.object(MODULE, "link_fd_no_replace", side_effect=OSError(errno.EXDEV, "cross-device")), \
                 patch.object(MODULE.shutil, "copyfileobj", side_effect=replace_during_copy):
                moved = helper.move_safely(source, destination, "test.txt")

            self.assertEqual(moved.read_bytes(), b"original")
            self.assertEqual(source.read_bytes(), b"replacement")
            self.assertEqual(displaced_staging.read_bytes(), b"original")
            self.assertTrue(any("preserved staging pathname replacement" in message for message in helper.logs))

    def test_cross_filesystem_copy_removes_unchanged_staging_inode(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "Downloads"
            destination = root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test.txt"
            source.write_bytes(b"original")
            helper.downloads = downloads
            helper.resolver = SimpleNamespace(_validate_path=lambda path, target: (path, target))
            helper.log = lambda _message: None

            with patch.object(MODULE, "link_fd_no_replace", side_effect=OSError(errno.EXDEV, "cross-device")):
                moved = helper.move_safely(source, destination, "test.txt")

            self.assertEqual(moved.read_bytes(), b"original")
            self.assertFalse(source.exists())
            self.assertEqual(list(downloads.iterdir()), [])

    def test_cross_filesystem_copy_does_not_publish_over_destination_replacement(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "Downloads"
            destination = root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test.txt"
            source.write_bytes(b"original")
            helper.downloads = downloads
            helper.resolver = SimpleNamespace(_validate_path=lambda path, target: (path, target))
            helper.log = lambda _message: None
            real_copy = MODULE.shutil.copyfileobj

            def replace_final_during_copy(source_file, destination_file, length):
                final = destination / "test.txt"
                if final.exists():
                    final.unlink()
                final.write_bytes(b"replacement")
                return real_copy(source_file, destination_file, length)

            with patch.object(MODULE, "link_fd_no_replace", side_effect=OSError(errno.EXDEV, "cross-device")), \
                 patch.object(MODULE.shutil, "copyfileobj", side_effect=replace_final_during_copy):
                moved = helper.move_safely(source, destination, "test.txt")

            self.assertEqual((destination / "test.txt").read_bytes(), b"replacement")
            self.assertEqual(moved.name, "test (1).txt")
            self.assertEqual(moved.read_bytes(), b"original")
            self.assertFalse(source.exists())

    def test_cross_filesystem_copy_retracts_swap_immediately_before_publish(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "Downloads"
            destination = root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test.txt"
            source.write_bytes(b"original")
            helper.downloads = downloads
            helper.resolver = SimpleNamespace(_validate_path=lambda path, target: (path, target))
            helper.logs = []
            helper.log = helper.logs.append
            real_rename_no_replace = MODULE.rename_no_replace

            def swap_before_publish(rename_source, rename_destination):
                rename_source = Path(rename_source)
                if rename_source.name == "payload":
                    rename_source.rename(rename_source.with_name("displaced-payload"))
                    rename_source.write_bytes(b"replacement")
                return real_rename_no_replace(rename_source, rename_destination)

            with patch.object(MODULE, "link_fd_no_replace", side_effect=OSError(errno.EXDEV, "cross-device")), \
                 patch.object(MODULE, "rename_no_replace", side_effect=swap_before_publish):
                with self.assertRaisesRegex(MODULE.ProtocolError, "published file identity changed"):
                    helper.move_safely(source, destination, "test.txt")

            self.assertEqual(source.read_bytes(), b"original")
            self.assertFalse((destination / "test.txt").exists())
            retained = list(destination.glob(".utm-spice-dnd-*.unverified"))
            self.assertEqual(len(retained), 1)
            self.assertEqual(retained[0].read_bytes(), b"replacement")

    def test_unverified_publication_retraction_failures_fail_closed(self):
        helper = object.__new__(MODULE.GuestHelper)
        helper.logs = []
        helper.log = helper.logs.append
        destination = Path("/untrusted/destination")

        with patch.object(
            MODULE,
            "rename_no_replace",
            side_effect=[FileExistsError(), FileNotFoundError()],
        ):
            helper.retract_unverified_destination(destination)
        self.assertTrue(any("disappeared before retraction" in message for message in helper.logs))

        with patch.object(MODULE, "rename_no_replace", side_effect=PermissionError("denied")):
            helper.retract_unverified_destination(destination)
        self.assertTrue(any("could not retract" in message for message in helper.logs))

        with patch.object(MODULE, "rename_no_replace", side_effect=FileExistsError()):
            helper.retract_unverified_destination(destination)
        self.assertTrue(any("name collisions" in message for message in helper.logs))

    def test_missing_published_destination_is_never_verified(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference"
            reference.write_bytes(b"payload")
            with self.assertRaisesRegex(MODULE.ProtocolError, "could not be verified"):
                helper.verify_published_destination(Path(directory) / "missing", reference.stat())

    def test_failed_cross_filesystem_copy_does_not_unlink_temp_replacement(self):
        helper = object.__new__(MODULE.GuestHelper)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "Downloads"
            destination = root / "Documents"
            downloads.mkdir()
            destination.mkdir()
            source = downloads / "test.txt"
            source.write_bytes(b"original")
            helper.downloads = downloads
            helper.resolver = SimpleNamespace(_validate_path=lambda path, target: (path, target))
            helper.logs = []
            helper.log = helper.logs.append

            def replace_temp_and_fail(_source_file, _destination_file, _length):
                workspace = next(destination.glob(".utm-spice-dnd-*.copy"))
                temporary = workspace / "payload"
                temporary.rename(workspace / "displaced-payload")
                temporary.write_bytes(b"replacement")
                raise OSError("copy failed")

            with patch.object(MODULE, "link_fd_no_replace", side_effect=OSError(errno.EXDEV, "cross-device")), \
                 patch.object(MODULE.shutil, "copyfileobj", side_effect=replace_temp_and_fail):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    helper.move_safely(source, destination, "test.txt")

            workspace = next(destination.glob(".utm-spice-dnd-*.copy"))
            self.assertEqual((workspace / "payload").read_bytes(), b"replacement")
            self.assertEqual(source.read_bytes(), b"original")
            self.assertTrue(any("copy pathname replacement retained" in message for message in helper.logs))

    def test_fallback_validates_downloads_with_target_metadata(self):
        resolver = object.__new__(MODULE.TargetResolver)
        captured = {}

        def validate(path, target):
            captured["path"] = path
            captured["target"] = target
            return path, target

        resolver._validate_path = validate
        with patch.object(MODULE.Gio.File, "new_for_path") as new_for_path:
            new_for_path.return_value.get_uri.return_value = "file:///home/test/Downloads"
            path, target = resolver._fallback("unsupported-window")

        self.assertEqual(path, captured["path"])
        self.assertIs(target, captured["target"])
        self.assertEqual(target["kind"], "fallback")
        self.assertEqual(target["reason"], "unsupported-window")


class TargetResolverTests(unittest.TestCase):
    def make_resolver(self):
        resolver = object.__new__(MODULE.TargetResolver)
        resolver.debug = False
        return resolver

    def test_shell_and_nautilus_dbus_responses_are_unpacked(self):
        resolver = self.make_resolver()
        resolver.bus = SimpleNamespace(call_sync=lambda *args: SimpleNamespace(
            unpack=lambda: ('{"kind":"desktop"}',)
        ))
        self.assertEqual(
            resolver._inspect_shell(0, 1.0, 2.0, 100.0, 80.0),
            {"kind": "desktop"},
        )

        resolver.bus = SimpleNamespace(call_sync=lambda *args: SimpleNamespace(
            unpack=lambda: ({"/window/1": ["file:///tmp"]},)
        ))
        self.assertEqual(
            resolver._nautilus_locations(),
            {"/window/1": ["file:///tmp"]},
        )

    def test_shell_non_object_response_is_rejected(self):
        resolver = self.make_resolver()
        resolver.bus = SimpleNamespace(call_sync=lambda *args: SimpleNamespace(
            unpack=lambda: ('[]',)
        ))
        with self.assertRaisesRegex(MODULE.ProtocolError, "invalid Shell"):
            resolver._inspect_shell(0, 1.0, 2.0, 100.0, 80.0)

    def test_validate_path_accepts_local_directory_without_mutating_target(self):
        resolver = self.make_resolver()
        with tempfile.TemporaryDirectory() as directory:
            target = {"kind": "desktop"}
            fake_file = SimpleNamespace(
                query_filesystem_info=lambda *args: SimpleNamespace(
                    has_attribute=lambda _name: True,
                    get_attribute_boolean=lambda _name: False,
                ),
                get_uri=lambda: "file:///resolved",
            )
            with patch.object(MODULE.Gio.File, "new_for_path", return_value=fake_file):
                resolved, enriched = resolver._validate_path(Path(directory), target)
            self.assertEqual(resolved, Path(directory).resolve())
            self.assertEqual(enriched["uri"], "file:///resolved")
            self.assertEqual(target, {"kind": "desktop"})

    def test_validate_path_rejects_forbidden_missing_and_remote_destinations(self):
        resolver = self.make_resolver()
        with self.assertRaisesRegex(MODULE.ProtocolError, "forbidden"):
            resolver._validate_path(Path("/proc"), {})
        with self.assertRaisesRegex(MODULE.ProtocolError, "cannot be resolved"):
            resolver._validate_path(Path("/certainly/not/a/real/dnd-directory"), {})

        with tempfile.TemporaryDirectory() as directory:
            fake_file = SimpleNamespace(
                query_filesystem_info=lambda *args: SimpleNamespace(
                    has_attribute=lambda _name: True,
                    get_attribute_boolean=lambda _name: True,
                )
            )
            with patch.object(MODULE.Gio.File, "new_for_path", return_value=fake_file):
                with self.assertRaisesRegex(MODULE.ProtocolError, "remote filesystems"):
                    resolver._validate_path(Path(directory), {})

    def test_resolve_desktop_and_desktop_validation_fallback(self):
        resolver = self.make_resolver()
        resolver._inspect_shell = lambda *args: {"kind": "desktop", "confidence": "medium"}
        resolver._validate_path = lambda path, target: (path, target)
        destination, target = resolver.resolve(0, 1, 2, 100, 80)
        self.assertEqual(target["kind"], "desktop")
        self.assertEqual(target["confidence"], "medium")

        resolver._validate_path = lambda *_args: (_ for _ in ()).throw(MODULE.ProtocolError("gone"))
        resolver._fallback = lambda reason, diagnostic=None: (Path("/fallback"), {"reason": reason})
        destination, target = resolver.resolve(0, 1, 2, 100, 80)
        self.assertEqual(destination, Path("/fallback"))
        self.assertIn("desktop-unavailable", target["reason"])

    def test_resolve_shell_failure_and_unsupported_window_fall_back(self):
        resolver = self.make_resolver()
        resolver._fallback = lambda reason, diagnostic=None: (Path("/fallback"), {
            "reason": reason, "diagnostic": diagnostic,
        })
        resolver._inspect_shell = lambda *args: (_ for _ in ()).throw(MODULE.ProtocolError("bad"))
        self.assertIn("shell-inspection-failed", resolver.resolve(0, 1, 2, 3, 4)[1]["reason"])

        diagnostic = {"kind": "window", "appId": "calculator", "reason": "unsupported-app"}
        resolver._inspect_shell = lambda *args: diagnostic
        result = resolver.resolve(0, 1, 2, 3, 4)
        self.assertEqual(result[1]["reason"], "unsupported-app")
        self.assertIs(result[1]["diagnostic"], diagnostic)

    def test_resolve_nautilus_local_directory(self):
        resolver = self.make_resolver()
        diagnostic = {
            "kind": "window", "appId": "org.gnome.Nautilus", "wmClass": "",
            "gtkWindowPath": "/window/1",
        }
        resolver._inspect_shell = lambda *args: diagnostic
        resolver._nautilus_locations = lambda: {"/window/1": ["file:///tmp/Documents"]}
        resolver._validate_path = lambda path, target: (path, target)
        fake_file = SimpleNamespace(get_path=lambda: "/tmp/Documents")
        with patch.object(MODULE.Gio.File, "new_for_uri", return_value=fake_file):
            destination, target = resolver.resolve(0, 1, 2, 3, 4)
        self.assertEqual(destination, Path("/tmp/Documents"))
        self.assertEqual(target["kind"], "nautilus")

    def test_resolve_nautilus_rejects_unusable_location_metadata(self):
        diagnostic = {
            "kind": "window", "appId": "org.gnome.Nautilus", "wmClass": "",
            "gtkWindowPath": "/window/1",
        }
        cases = (
            ({**diagnostic, "gtkWindowPath": ""}, {}, "no-gtk-object-path"),
            (diagnostic, {}, "ambiguous"),
            (diagnostic, {"/window/1": ["smb://server/share"]}, "not-local"),
        )
        for inspected, locations, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                resolver = self.make_resolver()
                resolver._inspect_shell = lambda *args, value=inspected: value
                resolver._nautilus_locations = lambda value=locations: value
                resolver._fallback = lambda reason, diagnostic=None: (Path("/fallback"), {"reason": reason})
                self.assertIn(expected_reason, resolver.resolve(0, 1, 2, 3, 4)[1]["reason"])


class GuestRuntimeTests(unittest.TestCase):
    def make_helper(self):
        helper = object.__new__(MODULE.GuestHelper)
        helper.fd = None
        helper.active = None
        helper.read_buffer = bytearray()
        helper.logs = []
        helper.log = helper.logs.append
        return helper

    def test_send_handles_partial_blocking_and_write_error(self):
        helper = self.make_helper()
        helper.fd = 8
        helper.close_port = lambda: setattr(helper, "fd", None)
        writes = iter((BlockingIOError(), 3, 1000))

        def write(_fd, payload):
            result = next(writes)
            if isinstance(result, BaseException):
                raise result
            return min(result, len(payload))

        with patch.object(MODULE.os, "write", side_effect=write), \
             patch.object(MODULE.time, "sleep"), \
             patch.object(MODULE.time, "monotonic", side_effect=(0.0, 0.1, 0.2, 0.3)):
            helper.send({"type": "ok"})
        self.assertEqual(helper.fd, 8)

        with patch.object(MODULE.os, "write", side_effect=OSError("disconnected")), \
             patch.object(MODULE.time, "monotonic", side_effect=(0.0, 0.1)):
            helper.send({"type": "fail"})
        self.assertIsNone(helper.fd)
        self.assertTrue(any("port write failed" in message for message in helper.logs))

    def test_handle_drop_creates_session_and_sends_ready(self):
        helper = self.make_helper()
        helper.debug = True
        helper.responses = []
        helper.send = helper.responses.append
        helper.resolver = SimpleNamespace(resolve=lambda *_args: (
            Path("/documents"),
            {
                "kind": "nautilus", "uri": "file:///documents", "confidence": "high",
                "diagnostic": {"appId": "org.gnome.Nautilus"},
            },
        ))
        helper.snapshot_downloads = lambda: {(1, 2)}
        message = {
            "type": "drop", "version": 1, "transferId": str(uuid.uuid4()),
            "display": 0, "x": 10, "y": 20,
            "framebufferWidth": 1280, "framebufferHeight": 800,
            "files": [{"name": "file.txt", "size": 4}],
        }

        with patch.object(MODULE.time, "time_ns", return_value=123), \
             patch.object(MODULE.time, "monotonic", return_value=5.0):
            helper.handle_message(message)

        self.assertEqual(helper.active.transfer_id, message["transferId"])
        self.assertEqual(helper.active.baseline, {(1, 2)})
        self.assertEqual(helper.active.started_wall_ns, 123)
        self.assertEqual(helper.active.deadline, 5.0 + MODULE.TRANSFER_TIMEOUT)
        self.assertEqual(helper.responses, [{
            "type": "ready", "version": 1, "transferId": message["transferId"],
            "target": {
                "kind": "nautilus", "uri": "file:///documents", "confidence": "high",
                "diagnostic": {"appId": "org.gnome.Nautilus"},
            },
        }])
        self.assertTrue(any("target diagnostic=" in message for message in helper.logs))

    def test_send_ignores_disconnected_and_rejects_oversized_response(self):
        helper = self.make_helper()
        with patch.object(MODULE.os, "write") as write:
            helper.send({"type": "ignored"})
        write.assert_not_called()

        helper.fd = 8
        helper.send({"payload": "x" * MODULE.MAX_MESSAGE_BYTES})
        self.assertIn("refusing oversized response", helper.logs)

    def test_snapshot_and_scan_candidates_filter_unrelated_entries(self):
        helper = self.make_helper()
        with tempfile.TemporaryDirectory() as directory:
            downloads = Path(directory)
            helper.downloads = downloads
            existing = downloads / "existing.txt"
            existing.write_bytes(b"old")
            baseline = helper.snapshot_downloads()
            received = downloads / "wanted (1).txt"
            received.write_bytes(b"data")
            wrong = downloads / "wrong.txt"
            wrong.write_bytes(b"data")
            session = MODULE.TransferSession(
                transfer_id=str(uuid.uuid4()), files=[MODULE.ExpectedFile("wanted.txt", 4)],
                destination=downloads, target={}, baseline=baseline,
                started_wall_ns=0, deadline=100,
            )
            matches = helper.scan_candidates(session, session.files[0])
            self.assertEqual([path for path, _info in matches], [received])

    def test_candidate_stability_tracks_identity_size_and_time(self):
        helper = self.make_helper()
        session = SimpleNamespace(candidates={})
        info = SimpleNamespace(st_dev=1, st_ino=2, st_size=3, st_mtime_ns=4)
        self.assertFalse(helper.candidate_is_stable(session, Path("file"), info, 1.0))
        self.assertFalse(helper.candidate_is_stable(
            session, Path("file"), SimpleNamespace(**{**vars(info), "st_size": 4}), 1.2,
        ))
        self.assertTrue(helper.candidate_is_stable(
            session, Path("file"), SimpleNamespace(**{**vars(info), "st_size": 4}),
            1.2 + MODULE.STABLE_INTERVAL + 0.01,
        ))

    def test_tick_transfer_timeout_and_failed_target_move(self):
        helper = self.make_helper()
        helper.responses = []
        helper.send = helper.responses.append
        identifier = str(uuid.uuid4())
        helper.active = MODULE.TransferSession(
            transfer_id=identifier, files=[MODULE.ExpectedFile("a", 1)], destination=Path("/target"),
            target={"kind": "desktop"}, baseline=set(), started_wall_ns=0, deadline=1.0,
        )
        with patch.object(MODULE.time, "monotonic", return_value=2.0):
            helper.tick_transfer()
        self.assertEqual(helper.responses[0]["error"], "timed out waiting for expected SPICE files")
        self.assertIsNone(helper.active)

        info = SimpleNamespace(st_dev=1, st_ino=2, st_size=1)
        helper.active = MODULE.TransferSession(
            transfer_id=identifier, files=[MODULE.ExpectedFile("a", 1)], destination=Path("/target"),
            target={"kind": "desktop"}, baseline=set(), started_wall_ns=0, deadline=10.0,
        )
        helper.downloads = Path("/downloads")
        helper.scan_candidates = lambda *_args: [(Path("/downloads/a"), info)]
        helper.candidate_is_stable = lambda *_args: True
        helper.move_safely = lambda *_args: (_ for _ in ()).throw(MODULE.ProtocolError("gone"))
        with patch.object(MODULE.time, "monotonic", return_value=2.0), \
             patch.object(MODULE.Gio.File, "new_for_path", return_value=SimpleNamespace(get_uri=lambda: "file:///downloads")):
            helper.tick_transfer()
        self.assertEqual(helper.responses[-1]["type"], "complete")
        self.assertIsNone(helper.active)
        self.assertTrue(any("target move failed" in message for message in helper.logs))


class FramingTests(unittest.TestCase):
    def make_helper(self):
        helper = object.__new__(MODULE.GuestHelper)
        helper.fd = 1
        helper.read_buffer = bytearray()
        helper.active = None
        helper.handled = []
        helper.sent = []
        helper.logs = []
        helper.closed = False
        helper.handle_message = helper.handled.append
        helper.send = helper.sent.append
        helper.log = helper.logs.append

        def close_port():
            helper.closed = True
            helper.fd = None
            helper.read_buffer.clear()
            helper.active = None

        helper.close_port = close_port
        return helper

    def valid_message(self):
        return {
            "type": "drop",
            "version": 1,
            "transferId": str(uuid.uuid4()),
            "display": 0,
            "x": 1,
            "y": 2,
            "framebufferWidth": 100,
            "framebufferHeight": 100,
            "files": [{"name": "test.txt", "size": 4}],
        }

    def test_partial_message(self):
        helper = self.make_helper()
        encoded = (json.dumps(self.valid_message()) + "\n").encode()
        midpoint = len(encoded) // 2
        helper.process_input(encoded[:midpoint])
        self.assertEqual(helper.handled, [])
        helper.process_input(encoded[midpoint:])
        self.assertEqual(len(helper.handled), 1)

    def test_multiple_messages_per_read(self):
        helper = self.make_helper()
        first = self.valid_message()
        second = self.valid_message()
        helper.process_input((json.dumps(first) + "\n" + json.dumps(second) + "\n").encode())
        self.assertEqual(helper.handled, [first, second])

    def test_malformed_json_is_rejected(self):
        helper = self.make_helper()
        helper.process_input(b"{not-json}\n")
        self.assertEqual(helper.handled, [])
        self.assertEqual(helper.sent[0]["type"], "error")
        self.assertEqual(helper.sent[0]["error"], "invalid JSON")

    def test_size_bounded_huge_integer_is_rejected_without_crashing(self):
        helper = self.make_helper()

        helper.process_input(b'{"x":' + b"1" * 5000 + b"}\n")

        self.assertEqual(helper.handled, [])
        self.assertEqual(helper.sent[0]["error"], "invalid JSON")

    def test_nested_json_and_invalid_utf8_are_rejected(self):
        for payload in (b"[" * 2000 + b"]" * 2000 + b"\n", b"\xff\n",
                        b'{"x": 1, "x": 2}\n', b'{"x": NaN}\n'):
            helper = self.make_helper()
            helper.process_input(payload)
            self.assertEqual(helper.handled, [])
            self.assertEqual(helper.sent[0]["error"], "invalid JSON")

    def test_complete_frame_followed_by_oversized_partial_disconnects(self):
        helper = self.make_helper()
        helper.process_input(b"{}\n" + b"x" * MODULE.MAX_MESSAGE_BYTES)
        self.assertTrue(helper.closed)
        self.assertEqual(helper.read_buffer, bytearray())

    def test_oversized_complete_line_is_rejected(self):
        helper = self.make_helper()
        helper.process_input(b"x" * MODULE.MAX_MESSAGE_BYTES + b"\n")
        self.assertFalse(helper.closed)
        self.assertEqual(helper.handled, [])
        self.assertIn("rejected oversized control message", helper.logs)

    def test_oversized_truncated_line_disconnects(self):
        helper = self.make_helper()
        helper.active = SimpleNamespace(transfer_id=str(uuid.uuid4()))
        helper.process_input(b"x" * (MODULE.MAX_MESSAGE_BYTES + 1))
        self.assertTrue(helper.closed)
        self.assertIsNone(helper.active)
        self.assertEqual(helper.read_buffer, bytearray())

    def test_disconnect_clears_partial_message_and_active_session(self):
        helper = object.__new__(MODULE.GuestHelper)
        read_fd, write_fd = os.pipe()
        try:
            helper.fd = read_fd
            helper.read_buffer = bytearray(b'{"type":"drop"')
            helper.active = SimpleNamespace(transfer_id=str(uuid.uuid4()))
            helper.close_port()
            self.assertIsNone(helper.fd)
            self.assertIsNone(helper.active)
            self.assertEqual(helper.read_buffer, bytearray())
        finally:
            os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
