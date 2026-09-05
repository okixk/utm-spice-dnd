#!/usr/bin/python3

"""Controlled development responder for testing malformed SPICE-port replies."""

import argparse
import errno
import json
import os
import time
from pathlib import Path


DEFAULT_PORT = Path("/dev/virtio-ports/com.utmapp.dnd.0")
MAX_MESSAGE_BYTES = 64 * 1024


def open_port(path: Path) -> int:
    deadline = time.monotonic() + 10.0
    while True:
        try:
            return os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.EBUSY) or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def read_line(fd: int) -> bytes:
    buffer = bytearray()
    deadline = time.monotonic() + 10.0
    while b"\n" not in buffer:
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for host metadata")
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            time.sleep(0.01)
            continue
        if not data:
            raise EOFError("control port disconnected")
        buffer.extend(data)
        if len(buffer) > MAX_MESSAGE_BYTES:
            raise ValueError("host message exceeded 64 KiB")
    line, _, _remainder = buffer.partition(b"\n")
    return bytes(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("malformed-json", "wrong-version", "oversized", "no-reply"))
    parser.add_argument("--port", type=Path, default=DEFAULT_PORT)
    parser.add_argument("--hold-seconds", type=float, default=4.0)
    args = parser.parse_args()

    fd = open_port(args.port)
    try:
        line = read_line(fd)
        message = json.loads(line.decode("utf-8"))
        transfer_id = message.get("transferId")
        print(f"received={json.dumps(message, ensure_ascii=False, separators=(',', ':'))}", flush=True)

        if args.mode == "malformed-json":
            reply = b"{not-json}\n"
        elif args.mode == "wrong-version":
            reply = (json.dumps({
                "type": "ready",
                "version": 2,
                "transferId": transfer_id,
                "target": {"kind": "fallback", "confidence": "low"},
            }, separators=(",", ":")) + "\n").encode()
        elif args.mode == "oversized":
            reply = b"x" * MAX_MESSAGE_BYTES + b"\n"
        else:
            reply = b""

        if reply:
            os.set_blocking(fd, True)
            remaining = memoryview(reply)
            while remaining:
                written = os.write(fd, remaining)
                remaining = remaining[written:]
            print(f"sent={args.mode} bytes={len(reply)}", flush=True)
        time.sleep(args.hold_seconds)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
