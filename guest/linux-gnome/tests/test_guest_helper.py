#!/usr/bin/python3

import importlib.util
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

    def test_valid_drop(self):
        result = MODULE.validate_drop_message(self.valid_message())
        self.assertEqual(result[-1][0], MODULE.ExpectedFile("hello world.txt", 12))

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
        helper.send = lambda _message: None

        helper.handle_message({"type": "cancel", "version": 1, "transferId": identifier})

        self.assertIsNone(helper.active)

    def test_spice_duplicate_name_matching(self):
        self.assertTrue(MODULE.name_matches_received("test.txt", "test (1).txt"))
        self.assertTrue(MODULE.name_matches_received("archive.tar.gz", "archive.tar (2).gz"))
        self.assertFalse(MODULE.name_matches_received("test.txt", "other (1).txt"))

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
