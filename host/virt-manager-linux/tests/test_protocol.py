import json
import sys
import unittest
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path


LIB_DIR = Path(__file__).parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from protocol import (  # noqa: E402
    MAX_MESSAGE_BYTES,
    DropMetadata,
    ExpectedFile,
    FrameDecoder,
    Handshake,
    HandshakeState,
    ProtocolError,
    encode_frame,
    validate_drop_message,
)


class DropValidationTests(unittest.TestCase):
    def valid_wire(self):
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

    def test_valid_message_becomes_immutable_metadata(self):
        metadata = validate_drop_message(self.valid_wire())

        self.assertIsInstance(metadata, DropMetadata)
        self.assertEqual(metadata.files, (ExpectedFile("hello world.txt", 12),))
        with self.assertRaises(FrozenInstanceError):
            metadata.display = 3

    def test_round_trip_wire_shape_is_protocol_v1(self):
        wire = self.valid_wire()
        metadata = validate_drop_message(wire)

        self.assertEqual(metadata.to_wire(), wire)

    def test_rejects_unknown_fields_paths_boolean_numbers_and_outside_points(self):
        cases = []
        extra = self.valid_wire()
        extra["destination"] = "/etc"
        cases.append(extra)
        path = self.valid_wire()
        path["files"] = [{"name": "../secret", "size": 1}]
        cases.append(path)
        bool_version = self.valid_wire()
        bool_version["version"] = True
        cases.append(bool_version)
        bool_size = self.valid_wire()
        bool_size["files"] = [{"name": "a", "size": False}]
        cases.append(bool_size)
        outside = self.valid_wire()
        outside["x"] = outside["framebufferWidth"]
        cases.append(outside)

        for message in cases:
            with self.subTest(message=message), self.assertRaises(ProtocolError):
                validate_drop_message(message)


class FramingTests(unittest.TestCase):
    def valid_wire(self):
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

    def test_partial_and_multiple_frames_return_new_decoder_state(self):
        first = encode_frame(self.valid_wire())
        second_message = self.valid_wire()
        second = encode_frame(second_message)
        initial = FrameDecoder()

        partial = initial.feed(first[:10])
        complete = partial.decoder.feed(first[10:] + second)

        self.assertEqual(partial.messages, ())
        self.assertNotEqual(partial.decoder, initial)
        self.assertEqual(len(complete.messages), 2)
        self.assertEqual(complete.messages[1], second_message)

    def test_invalid_json_is_reported_without_poisoning_next_frame(self):
        decoder = FrameDecoder()
        valid = self.valid_wire()

        result = decoder.feed(b"{bad-json}\n" + encode_frame(valid))

        self.assertEqual(result.messages, (valid,))
        self.assertEqual(len(result.errors), 1)
        self.assertFalse(result.reconnect_required)

    def test_duplicate_json_members_are_rejected(self):
        result = FrameDecoder().feed(b'{"type":"execute","type":"ready"}\n')

        self.assertEqual(result.messages, ())
        self.assertEqual(result.errors, ("duplicate JSON member: type",))

    def test_oversized_unterminated_frame_requires_reconnect(self):
        result = FrameDecoder().feed(b"x" * MAX_MESSAGE_BYTES)

        self.assertTrue(result.reconnect_required)
        self.assertEqual(result.decoder, FrameDecoder())
        self.assertEqual(result.messages, ())

    def test_encoder_includes_newline_in_size_limit(self):
        with self.assertRaises(ProtocolError):
            encode_frame({"payload": "x" * MAX_MESSAGE_BYTES})


class HandshakeTests(unittest.TestCase):
    def ready(self, transfer_id):
        return {
            "type": "ready",
            "version": 1,
            "transferId": transfer_id,
            "target": {
                "kind": "nautilus",
                "uri": "file:///home/user/Documents",
                "confidence": "high",
            },
        }

    def test_matching_ready_transitions_to_ready(self):
        transfer_id = str(uuid.uuid4())
        handshake = Handshake.begin(transfer_id, now=10, timeout=3)

        ready = handshake.receive(self.ready(transfer_id), now=11)

        self.assertEqual(ready.state, HandshakeState.READY)
        self.assertTrue(ready.target_aware)
        self.assertFalse(ready.fallback_to_downloads)

    def test_mismatched_transfer_id_is_rejected_without_state_mutation(self):
        transfer_id = str(uuid.uuid4())
        handshake = Handshake.begin(transfer_id, now=10, timeout=3)

        with self.assertRaises(ProtocolError):
            handshake.receive(self.ready(str(uuid.uuid4())), now=11)

        self.assertEqual(handshake.state, HandshakeState.WAITING)

    def test_timeout_is_terminal_and_late_ready_does_not_rearm(self):
        transfer_id = str(uuid.uuid4())
        handshake = Handshake.begin(transfer_id, now=10, timeout=3).poll(now=13)

        late = handshake.receive(self.ready(transfer_id), now=14)

        self.assertEqual(handshake.state, HandshakeState.WAITING_CANCEL_WRITE)
        self.assertEqual(late, handshake)
        self.assertFalse(handshake.fallback_to_downloads)
        self.assertTrue(handshake.cancel_metadata_before_payload)

    def test_fallback_requires_cancel_write_then_matching_ack(self):
        transfer_id = str(uuid.uuid4())
        handshake = Handshake.begin(transfer_id, now=0, timeout=3).fail("bad reply")
        cancelled = {"type": "cancelled", "version": 1, "transferId": transfer_id}

        early = handshake.receive(cancelled, now=1)
        waiting = early.cancel_write_succeeded(now=1, timeout=2)
        fallback = waiting.receive(cancelled, now=1.1)

        self.assertEqual(early.state, HandshakeState.WAITING_CANCEL_WRITE)
        self.assertEqual(waiting.state, HandshakeState.WAITING_CANCEL_ACK)
        self.assertEqual(fallback.state, HandshakeState.FALLBACK)
        self.assertTrue(fallback.fallback_to_downloads)

    def test_target_transfer_waits_for_complete_barrier(self):
        transfer_id = str(uuid.uuid4())
        ready = Handshake.begin(transfer_id, 0, 3).receive(self.ready(transfer_id), 1)

        waiting = ready.payload_copied(now=2, timeout=3)
        complete = waiting.receive(
            {"type": "complete", "version": 1, "transferId": transfer_id},
            now=2.1,
        )

        self.assertEqual(waiting.state, HandshakeState.WAITING_COMPLETE)
        self.assertEqual(complete.state, HandshakeState.COMPLETE)

    def test_valid_guest_error_selects_fallback(self):
        transfer_id = str(uuid.uuid4())
        handshake = Handshake.begin(transfer_id, now=0, timeout=3)
        response = {
            "type": "error",
            "version": 1,
            "transferId": transfer_id,
            "error": "another semantic transfer is active",
        }

        failed = handshake.receive(response, now=1)

        self.assertEqual(failed.state, HandshakeState.WAITING_CANCEL_WRITE)
        self.assertFalse(failed.fallback_to_downloads)
        self.assertTrue(failed.cancel_metadata_before_payload)

    def test_ready_schema_is_strict(self):
        transfer_id = str(uuid.uuid4())
        response = self.ready(transfer_id)
        response["unexpected"] = True
        handshake = Handshake.begin(transfer_id, now=0, timeout=3)

        with self.assertRaises(ProtocolError):
            handshake.receive(response, now=1)


if __name__ == "__main__":
    unittest.main()
