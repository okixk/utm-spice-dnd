#!/usr/bin/python3

import importlib.util
import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import call, patch


MODULE_PATH = Path(__file__).with_name("port_responder.py")
SPEC = importlib.util.spec_from_file_location("port_responder", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TimeoutAcknowledgementTests(unittest.TestCase):
    def test_timeout_ack_waits_then_acknowledges_matching_cancel(self):
        transfer_id = str(uuid.uuid4())
        drop = json.dumps({"type": "drop", "transferId": transfer_id}).encode()
        cancel = json.dumps({
            "type": "cancel", "version": 1, "transferId": transfer_id,
        }).encode()
        writes = []

        with patch.object(sys, "argv", [
            "port_responder.py", "timeout-ack", "--delay-seconds", "5.5",
            "--hold-seconds", "0",
        ]), patch.object(MODULE, "open_port", return_value=17), \
             patch.object(MODULE, "read_line", side_effect=(drop, cancel)), \
             patch.object(MODULE, "write_all", side_effect=lambda fd, payload: writes.append((fd, payload))), \
             patch.object(MODULE.time, "sleep") as sleep, \
             patch.object(MODULE.os, "close") as close:
            self.assertEqual(MODULE.main(), 0)

        self.assertEqual(sleep.call_args_list, [call(5.5), call(0.0)])
        self.assertEqual(close.call_args_list, [call(17)])
        self.assertEqual(len(writes), 1)
        self.assertEqual(json.loads(writes[0][1]), {
            "type": "cancelled", "version": 1, "transferId": transfer_id,
        })

    def test_cancel_with_wrong_transfer_id_is_rejected(self):
        transfer_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        cancel = json.dumps({
            "type": "cancel", "version": 1, "transferId": other_id,
        }).encode()
        with patch.object(MODULE, "read_line", return_value=cancel):
            with self.assertRaisesRegex(ValueError, "unexpected cancellation barrier"):
                MODULE.acknowledge_cancel(17, transfer_id)


if __name__ == "__main__":
    unittest.main()
