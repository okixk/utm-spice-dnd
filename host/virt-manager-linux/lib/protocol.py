"""Frontend-neutral protocol-v1 primitives for target-aware SPICE drops."""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PurePath
from typing import Any, Mapping


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
MAX_FILES = 100
MAX_FILE_NAME_BYTES = 255


class ProtocolError(ValueError):
    pass


def _uuid_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ProtocolError("invalid transferId")
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise ProtocolError("invalid transferId") from error


def _finite_number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f"invalid {name}")
    return float(value)


def _basename(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ProtocolError("invalid file name")
    if value in (".", "..") or PurePath(value).name != value or "/" in value:
        raise ProtocolError("file name must be a basename")
    if len(value.encode("utf-8")) > MAX_FILE_NAME_BYTES:
        raise ProtocolError("file name is too long")
    return value


@dataclass(frozen=True)
class ExpectedFile:
    name: str
    size: int

    def __post_init__(self) -> None:
        _basename(self.name)
        if isinstance(self.size, bool) or not isinstance(self.size, int) or not 0 <= self.size <= (1 << 63) - 1:
            raise ProtocolError("invalid file size")

    def to_wire(self) -> dict[str, Any]:
        return {"name": self.name, "size": self.size}


@dataclass(frozen=True)
class DropMetadata:
    transfer_id: str
    display: int
    x: float
    y: float
    framebuffer_width: float
    framebuffer_height: float
    files: tuple[ExpectedFile, ...]

    def __post_init__(self) -> None:
        canonical = _uuid_text(self.transfer_id)
        object.__setattr__(self, "transfer_id", canonical)
        if isinstance(self.display, bool) or not isinstance(self.display, int) or not 0 <= self.display <= 31:
            raise ProtocolError("invalid display")
        x = _finite_number("x", self.x)
        y = _finite_number("y", self.y)
        width = _finite_number("framebufferWidth", self.framebuffer_width)
        height = _finite_number("framebufferHeight", self.framebuffer_height)
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x >= width or y >= height:
            raise ProtocolError("drop coordinates are outside the framebuffer")
        if not isinstance(self.files, tuple) or not 1 <= len(self.files) <= MAX_FILES:
            raise ProtocolError("invalid files list")
        if not all(isinstance(item, ExpectedFile) for item in self.files):
            raise ProtocolError("invalid file entry")
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)
        object.__setattr__(self, "framebuffer_width", width)
        object.__setattr__(self, "framebuffer_height", height)

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "drop",
            "version": PROTOCOL_VERSION,
            "transferId": self.transfer_id,
            "display": self.display,
            "x": self.x,
            "y": self.y,
            "framebufferWidth": self.framebuffer_width,
            "framebufferHeight": self.framebuffer_height,
            "files": [item.to_wire() for item in self.files],
        }


def validate_drop_message(message: Any) -> DropMetadata:
    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    if message.get("type") != "drop":
        raise ProtocolError("unsupported message type")
    version = message.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    if set(message) != {
        "type",
        "version",
        "transferId",
        "display",
        "x",
        "y",
        "framebufferWidth",
        "framebufferHeight",
        "files",
    }:
        raise ProtocolError("invalid drop message fields")

    raw_files = message.get("files")
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= MAX_FILES:
        raise ProtocolError("invalid files list")
    files: list[ExpectedFile] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise ProtocolError("invalid file entry")
        if set(raw_file) != {"name", "size"}:
            raise ProtocolError("invalid file entry fields")
        files.append(ExpectedFile(raw_file.get("name"), raw_file.get("size")))

    return DropMetadata(
        transfer_id=message.get("transferId"),
        display=message.get("display"),
        x=message.get("x"),
        y=message.get("y"),
        framebuffer_width=message.get("framebufferWidth"),
        framebuffer_height=message.get("framebufferHeight"),
        files=tuple(files),
    )


def encode_frame(message: Mapping[str, Any]) -> bytes:
    if not isinstance(message, Mapping):
        raise ProtocolError("message must be an object")
    try:
        encoded = (
            json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ProtocolError(f"message cannot be encoded: {error}") from error
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolError("control message exceeds 64 KiB")
    return encoded


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProtocolError(f"invalid JSON number: {value}")


def _validate_json_depth(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ProtocolError("JSON nesting is too deep")
    members = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for member in members:
        _validate_json_depth(member, depth + 1)


def decode_json_frame(raw: bytes) -> Any:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ProtocolError("invalid JSON") from error
    _validate_json_depth(value)
    return value


@dataclass(frozen=True)
class DecodeBatch:
    decoder: "FrameDecoder"
    messages: tuple[Any, ...]
    errors: tuple[str, ...]
    reconnect_required: bool = False


@dataclass(frozen=True)
class FrameDecoder:
    buffer: bytes = b""

    def feed(self, data: bytes) -> DecodeBatch:
        if not isinstance(data, bytes):
            raise TypeError("frame data must be bytes")
        buffer = self.buffer + data
        messages: list[Any] = []
        errors: list[str] = []

        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            if not raw:
                continue
            if len(raw) + 1 > MAX_MESSAGE_BYTES:
                errors.append("control message exceeds 64 KiB")
                continue
            try:
                messages.append(decode_json_frame(raw))
            except ProtocolError as error:
                errors.append(str(error))

        if len(buffer) >= MAX_MESSAGE_BYTES:
            errors.append("unterminated control message exceeds 64 KiB")
            return DecodeBatch(
                decoder=FrameDecoder(),
                messages=tuple(messages),
                errors=tuple(errors),
                reconnect_required=True,
            )
        return DecodeBatch(
            decoder=FrameDecoder(buffer),
            messages=tuple(messages),
            errors=tuple(errors),
        )


@dataclass(frozen=True)
class TargetResponse:
    kind: str
    uri: str
    confidence: str
    reason: str | None = None


@dataclass(frozen=True)
class ReadyResponse:
    transfer_id: str
    target: TargetResponse


@dataclass(frozen=True)
class ErrorResponse:
    transfer_id: str
    error: str


@dataclass(frozen=True)
class BarrierResponse:
    kind: str
    transfer_id: str


def validate_handshake_response(message: Any) -> ReadyResponse | ErrorResponse | BarrierResponse:
    if not isinstance(message, dict):
        raise ProtocolError("response must be an object")
    version = message.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    response_type = message.get("type")

    if response_type == "ready":
        if set(message) != {"type", "version", "transferId", "target"}:
            raise ProtocolError("invalid ready response fields")
        target = message.get("target")
        if not isinstance(target, dict):
            raise ProtocolError("invalid target")
        required = {"kind", "uri", "confidence"}
        allowed = required | {"reason", "diagnostic"}
        if not required <= set(target) or not set(target) <= allowed:
            raise ProtocolError("invalid target fields")
        kind = target.get("kind")
        uri = target.get("uri")
        confidence = target.get("confidence")
        reason = target.get("reason")
        if kind not in {"desktop", "nautilus", "fallback"}:
            raise ProtocolError("invalid target kind")
        if not isinstance(uri, str) or not uri.startswith("file://") or len(uri.encode("utf-8")) > 4096:
            raise ProtocolError("invalid target URI")
        if confidence not in {"high", "low"}:
            raise ProtocolError("invalid target confidence")
        if reason is not None and (not isinstance(reason, str) or len(reason.encode("utf-8")) > 4096):
            raise ProtocolError("invalid target reason")
        if "diagnostic" in target and not isinstance(target["diagnostic"], dict):
            raise ProtocolError("invalid target diagnostic")
        return ReadyResponse(
            transfer_id=_uuid_text(message.get("transferId")),
            target=TargetResponse(kind, uri, confidence, reason),
        )

    if response_type == "error":
        if set(message) != {"type", "version", "transferId", "error"}:
            raise ProtocolError("invalid error response fields")
        error = message.get("error")
        if not isinstance(error, str) or not error or len(error.encode("utf-8")) > 4096:
            raise ProtocolError("invalid error description")
        return ErrorResponse(_uuid_text(message.get("transferId")), error)

    if response_type in {"cancelled", "complete"}:
        if set(message) != {"type", "version", "transferId"}:
            raise ProtocolError(f"invalid {response_type} response fields")
        return BarrierResponse(response_type, _uuid_text(message.get("transferId")))

    raise ProtocolError("unsupported response type")


class HandshakeState(Enum):
    WAITING = "waiting"
    READY = "ready"
    WAITING_CANCEL_WRITE = "waiting-cancel-write"
    WAITING_CANCEL_ACK = "waiting-cancel-ack"
    FALLBACK = "fallback"
    WAITING_COMPLETE = "waiting-complete"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class Handshake:
    transfer_id: str
    deadline: float
    state: HandshakeState = HandshakeState.WAITING
    target: TargetResponse | None = None
    error: str | None = None

    @classmethod
    def begin(cls, transfer_id: str, now: float, timeout: float) -> "Handshake":
        canonical = _uuid_text(transfer_id)
        start = _finite_number("now", now)
        duration = _finite_number("timeout", timeout)
        if duration <= 0:
            raise ProtocolError("timeout must be positive")
        return cls(canonical, start + duration)

    @property
    def target_aware(self) -> bool:
        return self.state in {HandshakeState.READY, HandshakeState.WAITING_COMPLETE}

    @property
    def fallback_to_downloads(self) -> bool:
        return self.state is HandshakeState.FALLBACK

    @property
    def cancel_metadata_before_payload(self) -> bool:
        return self.state is HandshakeState.WAITING_CANCEL_WRITE

    def poll(self, now: float) -> "Handshake":
        current = _finite_number("now", now)
        if self.state is HandshakeState.WAITING and current >= self.deadline:
            return replace(
                self,
                state=HandshakeState.WAITING_CANCEL_WRITE,
                error="guest helper response timed out",
            )
        if self.state in {HandshakeState.WAITING_CANCEL_ACK, HandshakeState.WAITING_COMPLETE} and current >= self.deadline:
            return replace(self, state=HandshakeState.FAILED, error="guest barrier timed out")
        return self

    def receive(self, message: Any, now: float) -> "Handshake":
        current = self.poll(now)
        if current.state in {HandshakeState.FAILED, HandshakeState.COMPLETE}:
            return current
        response = validate_handshake_response(message)
        if response.transfer_id != self.transfer_id:
            raise ProtocolError("response transferId does not match")
        if current.state is HandshakeState.WAITING:
            if isinstance(response, ReadyResponse):
                return replace(current, state=HandshakeState.READY, target=response.target)
            if isinstance(response, ErrorResponse):
                return replace(current, state=HandshakeState.WAITING_CANCEL_WRITE, error=response.error)
            return current
        if current.state is HandshakeState.WAITING_CANCEL_ACK:
            if isinstance(response, BarrierResponse) and response.kind == "cancelled":
                return replace(current, state=HandshakeState.FALLBACK)
            return current
        if current.state is HandshakeState.WAITING_COMPLETE:
            if isinstance(response, BarrierResponse) and response.kind == "complete":
                return replace(current, state=HandshakeState.COMPLETE)
            return current
        return current

    def cancel_write_succeeded(self, now: float, timeout: float) -> "Handshake":
        if self.state is not HandshakeState.WAITING_CANCEL_WRITE:
            return self
        current = _finite_number("now", now)
        duration = _finite_number("timeout", timeout)
        if duration <= 0:
            raise ProtocolError("timeout must be positive")
        return replace(self, state=HandshakeState.WAITING_CANCEL_ACK, deadline=current + duration)

    def payload_copied(self, now: float, timeout: float) -> "Handshake":
        if self.state is HandshakeState.FALLBACK:
            return replace(self, state=HandshakeState.COMPLETE)
        if self.state is not HandshakeState.READY:
            return self
        current = _finite_number("now", now)
        duration = _finite_number("timeout", timeout)
        if duration <= 0:
            raise ProtocolError("timeout must be positive")
        return replace(self, state=HandshakeState.WAITING_COMPLETE, deadline=current + duration)

    def fail(self, error: str) -> "Handshake":
        if self.state is not HandshakeState.WAITING:
            return self
        if not isinstance(error, str) or not error:
            raise ProtocolError("failure description is required")
        return replace(self, state=HandshakeState.WAITING_CANCEL_WRITE, error=error)


def cancel_message(transfer_id: str) -> dict[str, Any]:
    return {
        "type": "cancel",
        "version": PROTOCOL_VERSION,
        "transferId": _uuid_text(transfer_id),
    }
