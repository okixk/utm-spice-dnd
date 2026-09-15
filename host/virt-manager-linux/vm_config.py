#!/usr/bin/python3
"""Safely enable or disable target-aware SPICE drag-and-drop for a VM."""

from __future__ import annotations

import argparse
import copy
import difflib
import errno
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from xml.etree import ElementTree as ET


CONTROL_PORT_NAME = "com.utmapp.dnd.0"
SPICE_AGENT_NAME = "com.redhat.spice.0"


class ConfigurationError(RuntimeError):
    """A safe domain configuration change could not be completed."""


class XmlChangeResult(NamedTuple):
    xml: str
    changes: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)


class DomainApplyResult(NamedTuple):
    changed: bool
    changes: tuple[str, ...]
    backup_path: Optional[Path]
    xml: str


def parse_domain_xml(xml: str) -> ET.Element:
    """Parse domain XML while retaining comments and processing instructions."""
    try:
        parser = ET.XMLParser(
            target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
        )
        return ET.fromstring(xml, parser=parser)
    except ET.ParseError as error:
        raise ConfigurationError(f"libvirt returned invalid domain XML: {error}") from error


def _serialize(root: ET.Element, original: str) -> str:
    rendered = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    if original.endswith("\n"):
        rendered += "\n"
    return rendered


def _append_preserving_indent(parent: ET.Element, child: ET.Element) -> None:
    """Append to a copied tree without disturbing existing child indentation."""
    if len(parent):
        closing_indent = parent[-1].tail
        sibling_indent = _line_indent(parent.text)
        if closing_indent and "\n" in closing_indent:
            parent[-1].tail = f"\n{sibling_indent}"
        child.tail = closing_indent
    else:
        child.tail = parent.text
    parent.append(child)


def _line_indent(value: Optional[str]) -> str:
    if not value or "\n" not in value:
        return ""
    return value.rsplit("\n", 1)[1]


def _indent_new_container(parent: ET.Element, child: ET.Element) -> None:
    """Indent a newly-created element using the surrounding document style."""
    child_indent = _line_indent(parent.text)
    nested_indent = f"{child_indent}  "
    if len(child):
        child.text = f"\n{nested_indent}"
        for nested in child[:-1]:
            nested.tail = f"\n{nested_indent}"
        child[-1].tail = f"\n{child_indent}"


def _domain_devices(root: ET.Element, require_spice: bool) -> tuple[ET.Element, list[ET.Element]]:
    if root.tag != "domain" or root.get("type") not in {"kvm", "qemu"}:
        raise ConfigurationError("domain is not a QEMU/KVM virtual machine")

    devices = root.find("devices")
    if devices is None:
        raise ConfigurationError("domain has no devices section")

    spice_graphics = devices.findall("graphics[@type='spice']")
    if require_spice and not spice_graphics:
        raise ConfigurationError("domain does not have SPICE graphics")
    return devices, spice_graphics


def _channels_claiming_name(devices: ET.Element, name: str) -> list[ET.Element]:
    claimed = []
    for channel in devices.findall("channel"):
        target = channel.find("target")
        source = channel.find("source")
        if (target is not None and target.get("name") == name) or (
            source is not None and source.get("channel") == name
        ):
            claimed.append(channel)
    return claimed


def _is_control_channel(channel: ET.Element) -> bool:
    source = channel.find("source")
    target = channel.find("target")
    return bool(
        channel.get("type") == "spiceport"
        and source is not None
        and source.get("channel") == CONTROL_PORT_NAME
        and target is not None
        and target.get("type") == "virtio"
        and target.get("name") == CONTROL_PORT_NAME
    )


def _is_normal_agent_channel(channel: ET.Element) -> bool:
    target = channel.find("target")
    return bool(
        channel.get("type") == "spicevmc"
        and target is not None
        and target.get("type") == "virtio"
        and target.get("name") in {None, SPICE_AGENT_NAME}
    )


def _ensure_file_transfer(spice_graphics: list[ET.Element], changes: list[str]) -> None:
    changed = False
    for graphics in spice_graphics:
        entries = graphics.findall("filetransfer")
        if len(entries) > 1:
            raise ConfigurationError("SPICE graphics has duplicate filetransfer elements")
        if not entries:
            _append_preserving_indent(graphics, ET.Element("filetransfer", {"enable": "yes"}))
            changed = True
        elif entries[0].get("enable") != "yes":
            entries[0].set("enable", "yes")
            changed = True
    if changed:
        changes.append("enabled SPICE file transfer")


def _ensure_virtio_serial_controller(devices: ET.Element, changes: list[str]) -> None:
    if devices.find("controller[@type='virtio-serial']") is not None:
        return
    _append_preserving_indent(
        devices, ET.Element("controller", {"type": "virtio-serial", "index": "0"})
    )
    changes.append("added virtio-serial controller")


def _ensure_spice_agent(devices: ET.Element, changes: list[str]) -> None:
    channels = _channels_claiming_name(devices, SPICE_AGENT_NAME)

    # A spicevmc target without an explicit name has this libvirt default.
    unnamed = [
        channel
        for channel in devices.findall("channel[@type='spicevmc']")
        if channel.find("target") is not None
        and channel.find("target").get("type") == "virtio"
        and channel.find("target").get("name") is None
    ]
    if channels and unnamed:
        raise ConfigurationError("domain has duplicate normal SPICE agent channels")
    if not channels and len(unnamed) == 1:
        unnamed[0].find("target").set("name", SPICE_AGENT_NAME)
        changes.append("made normal SPICE agent channel name explicit")
        return
    if not channels and len(unnamed) > 1:
        raise ConfigurationError("domain has duplicate implicit SPICE agent channels")
    if len(channels) > 1:
        raise ConfigurationError("domain has duplicate normal SPICE agent channels")
    if len(channels) == 1:
        channel = channels[0]
        target = channel.find("target")
        if (
            channel.get("type") != "spicevmc"
            or target is None
            or target.get("type") != "virtio"
        ):
            raise ConfigurationError("domain has a conflicting normal SPICE agent channel")
        return

    channel = ET.Element("channel", {"type": "spicevmc"})
    channel.append(
        ET.Element("target", {"type": "virtio", "name": SPICE_AGENT_NAME})
    )
    _indent_new_container(devices, channel)
    _append_preserving_indent(devices, channel)
    changes.append("added normal SPICE agent channel")


def _ensure_control_channel(devices: ET.Element, changes: list[str]) -> None:
    channels = _channels_claiming_name(devices, CONTROL_PORT_NAME)
    if len(channels) > 1:
        raise ConfigurationError(f"domain has duplicate {CONTROL_PORT_NAME} channels")
    if len(channels) == 1:
        if not _is_control_channel(channels[0]):
            raise ConfigurationError(f"domain has a conflicting {CONTROL_PORT_NAME} channel")
        return

    channel = ET.Element("channel", {"type": "spiceport"})
    channel.append(ET.Element("source", {"channel": CONTROL_PORT_NAME}))
    channel.append(
        ET.Element("target", {"type": "virtio", "name": CONTROL_PORT_NAME})
    )
    _indent_new_container(devices, channel)
    _append_preserving_indent(devices, channel)
    changes.append(f"added {CONTROL_PORT_NAME} SPICE port channel")


def enable_xml(original: str) -> XmlChangeResult:
    source_root = parse_domain_xml(original)
    root = copy.deepcopy(source_root)
    devices, spice_graphics = _domain_devices(root, require_spice=True)
    changes: list[str] = []

    _ensure_file_transfer(spice_graphics, changes)
    _ensure_virtio_serial_controller(devices, changes)
    _ensure_spice_agent(devices, changes)
    _ensure_control_channel(devices, changes)

    if not changes:
        return XmlChangeResult(original, ())
    rendered = _serialize(root, original)
    _assert_unrelated_semantics_preserved(
        original, rendered, allow_new_virtio_serial_controller=True
    )
    return XmlChangeResult(rendered, tuple(changes))


def disable_xml(original: str) -> XmlChangeResult:
    source_root = parse_domain_xml(original)
    root = copy.deepcopy(source_root)
    devices, _spice_graphics = _domain_devices(root, require_spice=False)

    claiming = _channels_claiming_name(devices, CONTROL_PORT_NAME)
    exact = [channel for channel in claiming if _is_control_channel(channel)]
    if claiming and not exact:
        raise ConfigurationError(f"domain has a conflicting {CONTROL_PORT_NAME} channel")
    if len(claiming) > 1:
        raise ConfigurationError(f"domain has duplicate {CONTROL_PORT_NAME} channels")
    if not exact:
        return XmlChangeResult(original, ())

    for channel in exact:
        devices.remove(channel)
    change = f"removed {CONTROL_PORT_NAME} SPICE port channel"
    rendered = _serialize(root, original)
    _assert_unrelated_semantics_preserved(original, rendered)
    return XmlChangeResult(rendered, (change,))


def _without_managed_nodes(
    xml: str, *, remove_new_virtio_serial_controller: bool = False
) -> ET.Element:
    root = copy.deepcopy(parse_domain_xml(xml))
    devices = root.find("devices")
    if devices is None:
        return root

    for graphics in devices.findall("graphics[@type='spice']"):
        for filetransfer in graphics.findall("filetransfer"):
            graphics.remove(filetransfer)
    controllers = devices.findall("controller[@type='virtio-serial']")
    if remove_new_virtio_serial_controller and len(controllers) == 1:
        devices.remove(controllers[0])
    for channel in list(devices.findall("channel")):
        if _is_normal_agent_channel(channel) or channel in _channels_claiming_name(
            devices, CONTROL_PORT_NAME
        ):
            devices.remove(channel)
    return root


def _significant_text(value: Optional[str]) -> Optional[str]:
    return None if value is None or not value.strip() else value


def _semantic_fingerprint(element: ET.Element) -> tuple[object, ...]:
    tag = element.tag if isinstance(element.tag, str) else element.tag.__name__
    return (
        tag,
        tuple(sorted(element.attrib.items())),
        _significant_text(element.text),
        _significant_text(element.tail),
        tuple(_semantic_fingerprint(child) for child in element),
    )


def _assert_unrelated_semantics_preserved(
    before: str,
    after: str,
    *,
    allow_new_virtio_serial_controller: bool = False,
) -> None:
    before_root = parse_domain_xml(before)
    after_root = parse_domain_xml(after)
    before_devices = before_root.find("devices")
    after_devices = after_root.find("devices")
    before_controllers = (
        []
        if before_devices is None
        else before_devices.findall("controller[@type='virtio-serial']")
    )
    after_controllers = (
        []
        if after_devices is None
        else after_devices.findall("controller[@type='virtio-serial']")
    )

    # The tool creates exactly one default controller only when the domain had
    # none. Existing controllers are unrelated configuration and must remain in
    # the semantic comparison, including their attributes and child elements.
    remove_added_controller = (
        allow_new_virtio_serial_controller
        and not before_controllers
        and len(after_controllers) == 1
    )
    before_tree = _without_managed_nodes(before)
    after_tree = _without_managed_nodes(
        after,
        remove_new_virtio_serial_controller=remove_added_controller,
    )
    if _semantic_fingerprint(before_tree) != _semantic_fingerprint(after_tree):
        raise ConfigurationError("unrelated XML changed while editing managed DnD settings")


def _copy_safe_attributes(
    source: ET.Element, destination: ET.Element, allowed: tuple[str, ...]
) -> None:
    for name in allowed:
        value = source.get(name)
        if value is not None:
            destination.set(name, value)


def _managed_projection(xml: str) -> str:
    """Return only non-secret XML fields managed by this tool."""
    root = parse_domain_xml(xml)
    devices = root.find("devices")
    projection = ET.Element("managed-dnd-config")
    if devices is None:
        return ET.tostring(projection, encoding="unicode") + "\n"

    for index, graphics in enumerate(devices.findall("graphics[@type='spice']")):
        projected = ET.SubElement(projection, "spice-graphics", {"index": str(index)})
        for filetransfer in graphics.findall("filetransfer"):
            projected_transfer = ET.SubElement(projected, "filetransfer")
            _copy_safe_attributes(filetransfer, projected_transfer, ("enable",))

    for controller in devices.findall("controller[@type='virtio-serial']"):
        projected = ET.SubElement(projection, "virtio-serial-controller")
        _copy_safe_attributes(controller, projected, ("index", "model"))
        for child_name in ("alias", "address"):
            for child in controller.findall(child_name):
                projected_child = ET.SubElement(projected, child_name)
                _copy_safe_attributes(
                    child,
                    projected_child,
                    ("name", "type", "controller", "bus", "port"),
                )

    for channel in devices.findall("channel"):
        if not (
            _is_normal_agent_channel(channel)
            or channel in _channels_claiming_name(devices, CONTROL_PORT_NAME)
        ):
            continue
        projected = ET.SubElement(projection, "channel")
        _copy_safe_attributes(channel, projected, ("type",))
        for child_name in ("source", "target", "alias", "address"):
            for child in channel.findall(child_name):
                projected_child = ET.SubElement(projected, child_name)
                _copy_safe_attributes(
                    child,
                    projected_child,
                    ("channel", "type", "name", "state", "controller", "bus", "port"),
                )

    ET.indent(projection, space="  ")
    return ET.tostring(projection, encoding="unicode", short_empty_elements=True) + "\n"


def _managed_persisted_diff(before: str, persisted: str) -> str:
    before_projection = _managed_projection(before)
    persisted_projection = _managed_projection(persisted)
    return "".join(
        difflib.unified_diff(
            before_projection.splitlines(keepends=True),
            persisted_projection.splitlines(keepends=True),
            fromfile="managed-dnd-before.xml",
            tofile="managed-dnd-persisted.xml",
        )
    )


CommandRunner = Callable[..., object]


def _run_checked(
    runner: CommandRunner, command: list[str], description: str
) -> object:
    result = runner(
        command,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise ConfigurationError(f"failed to {description}: {detail}")
    return result


def _virsh_prefix(virsh: str, connection_uri: Optional[str]) -> list[str]:
    command = [virsh]
    if connection_uri:
        command.extend(("--connect", connection_uri))
    return command


def _safe_backup_name(original: str, domain: str) -> str:
    root = parse_domain_xml(original)
    uuid_element = root.find("uuid")
    identity = (uuid_element.text or "").strip() if uuid_element is not None else ""
    if not identity or any(character not in "0123456789abcdefABCDEF-" for character in identity):
        identity = hashlib.sha256(domain.encode("utf-8")).hexdigest()[:16]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{identity}-{timestamp}.xml"


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _validate_backup_ancestor(metadata: os.stat_result, path: Path) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ConfigurationError(f"backup directory ancestor {path} is not a directory")
    if metadata.st_uid not in {0, os.geteuid()}:
        raise ConfigurationError(
            f"backup directory ancestor {path} must be owned by root or the current user"
        )
    if mode & 0o022 and not mode & stat.S_ISVTX:
        raise ConfigurationError(
            f"backup directory has writable ancestor {path} without the sticky bit"
        )


def _validate_private_backup_directory(metadata: os.stat_result, path: Path) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or mode & 0o077
    ):
        raise ConfigurationError(
            f"backup directory {path} must be owned by the current user and have "
            "no group or other permissions"
        )


def _open_private_directory(
    path: Path, *, create: bool = False, restrict_owned_final: bool = False
) -> int:
    """Walk and pin a private directory without following any symlinks."""
    if not path.is_absolute():
        raise ConfigurationError("backup directory path must be absolute")

    flags = _directory_open_flags()
    descriptor = os.open("/", flags)
    walked = Path("/")
    try:
        for component in path.parts[1:]:
            _validate_backup_ancestor(os.fstat(descriptor), walked)
            walked = walked / component
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise ConfigurationError(
                        f"backup directory {path} changed or disappeared"
                    ) from None
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    child = os.open(component, flags, dir_fd=descriptor)
                except OSError as error:
                    raise ConfigurationError(
                        f"could not securely create backup directory {walked}: {error}"
                    ) from error
            except OSError as error:
                detail = (
                    "contains a symlink"
                    if error.errno in {errno.ELOOP, errno.ENOTDIR}
                    else str(error)
                )
                raise ConfigurationError(
                    f"backup directory {path} is unsafe: {detail}"
                ) from error
            os.close(descriptor)
            descriptor = child

        metadata = os.fstat(descriptor)
        if restrict_owned_final:
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise ConfigurationError(
                    f"backup directory {path} must be a real directory owned by "
                    "the current user"
                )
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                os.fchmod(descriptor, 0o700)
                os.fsync(descriptor)
                metadata = os.fstat(descriptor)
        _validate_private_backup_directory(metadata, path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_private_file(directory_fd: int, name: str, contents: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise


def _require_directory_path_unchanged(path: Path, pinned_descriptor: int) -> None:
    """Ensure a reported backup path still names the pinned directory."""
    try:
        current_descriptor = _open_private_directory(path)
    except ConfigurationError as error:
        raise ConfigurationError(
            f"backup directory {path} changed after the backup was written; "
            "no domain change was applied"
        ) from error
    try:
        pinned = os.fstat(pinned_descriptor)
        current = os.fstat(current_descriptor)
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            raise ConfigurationError(
                f"backup directory {path} changed after the backup was written; "
                "no domain change was applied"
            )
    finally:
        os.close(current_descriptor)


def _fsync_directory(descriptor: int) -> None:
    """Persist directory-entry changes before handing XML to virsh."""
    os.fsync(descriptor)


def _migrate_default_project_directory(backup_dir: Path) -> None:
    """Restrict legacy default project state permissions, never custom paths."""
    default_backup_dir = Path(os.path.abspath(_default_backup_dir().expanduser()))
    if backup_dir != default_backup_dir:
        return
    descriptor = _open_private_directory(
        backup_dir.parent,
        create=True,
        restrict_owned_final=True,
    )
    os.close(descriptor)


def _require_powered_off(state: str, domain: str, phase: str) -> None:
    normalized = state.strip().lower()
    if "saved" in normalized:
        raise ConfigurationError(
            f"domain {domain!r} has a managed save image; remove or restore it before "
            f"changing persistent XML ({phase})"
        )
    if not normalized.startswith("shut off"):
        raise ConfigurationError(
            f"domain {domain!r} must be shut off before changing its persistent XML "
            f"({phase} state: {state.strip() or 'unknown'})"
        )


def apply_to_domain(
    domain: str,
    operation: str,
    backup_dir: Path,
    *,
    connection_uri: Optional[str] = None,
    virsh: str = "virsh",
    runner: CommandRunner = subprocess.run,
    reporter: Optional[Callable[[str], None]] = None,
) -> DomainApplyResult:
    if not domain or "\x00" in domain:
        raise ConfigurationError("domain name must not be empty or contain NUL")
    if operation not in {"enable", "disable"}:
        raise ConfigurationError(f"unsupported operation: {operation}")

    prefix = _virsh_prefix(virsh, connection_uri)
    dump = _run_checked(
        runner,
        [*prefix, "dumpxml", "--inactive", domain],
        f"read inactive XML for domain {domain!r}",
    )
    original = dump.stdout
    change = enable_xml(original) if operation == "enable" else disable_xml(original)
    if not change.changed:
        return DomainApplyResult(False, (), None, original)

    state = _run_checked(
        runner,
        [*prefix, "domstate", domain, "--reason"],
        f"read state for domain {domain!r}",
    ).stdout.strip()
    _require_powered_off(state, domain, "current")

    current = _run_checked(
        runner,
        [*prefix, "dumpxml", "--inactive", domain],
        f"re-read inactive XML for domain {domain!r}",
    ).stdout
    if current != original:
        raise ConfigurationError(
            f"inactive XML for domain {domain!r} changed concurrently; no change was applied"
        )
    current_state = _run_checked(
        runner,
        [*prefix, "domstate", domain, "--reason"],
        f"re-read state for domain {domain!r}",
    ).stdout.strip()
    try:
        _require_powered_off(current_state, domain, "pre-define")
    except ConfigurationError as error:
        raise ConfigurationError(f"{error}; no change was applied") from error

    backup_dir = Path(os.path.abspath(backup_dir.expanduser()))
    _migrate_default_project_directory(backup_dir)
    backup_directory_fd = _open_private_directory(backup_dir, create=True)
    backup_name = _safe_backup_name(original, domain)
    backup_path = backup_dir / backup_name
    try:
        _write_private_file(backup_directory_fd, backup_name, original)
        _fsync_directory(backup_directory_fd)

        # Keep candidate anonymous and refer to its inherited descriptor. This
        # prevents another process from replacing path contents between final
        # checks and virsh opening the XML.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(change.xml)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)

            # Recheck after durable backup/candidate preparation so the
            # unavoidable dumpxml/define race spans only final virsh calls.
            latest = _run_checked(
                runner,
                [*prefix, "dumpxml", "--inactive", domain],
                f"perform final inactive XML check for domain {domain!r}",
            ).stdout
            if latest != original:
                raise ConfigurationError(
                    f"inactive XML for domain {domain!r} changed concurrently; no domain "
                    f"change was applied, and the original backup remains at {backup_path}"
                )
            latest_state = _run_checked(
                runner,
                [*prefix, "domstate", domain, "--reason"],
                f"perform final state check for domain {domain!r}",
            ).stdout.strip()
            try:
                _require_powered_off(latest_state, domain, "final pre-define")
            except ConfigurationError as error:
                raise ConfigurationError(
                    f"{error}; no domain change was applied, and the original backup "
                    f"remains at {backup_path}"
                ) from error

            _require_directory_path_unchanged(backup_dir, backup_directory_fd)
            descriptor = stream.fileno()
            result = runner(
                [*prefix, "define", "--validate", f"/proc/self/fd/{descriptor}"],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "LC_ALL": "C"},
                pass_fds=(descriptor,),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown error").strip()
                raise ConfigurationError(
                    f"failed to validate and define domain {domain!r}: {detail}; "
                    f"backup remains at {backup_path}"
                )
    finally:
        os.close(backup_directory_fd)

    persisted = _run_checked(
        runner,
        [*prefix, "dumpxml", "--inactive", domain],
        f"verify persisted inactive XML for domain {domain!r}",
    ).stdout
    try:
        verification = (
            enable_xml(persisted) if operation == "enable" else disable_xml(persisted)
        )
    except ConfigurationError as error:
        raise ConfigurationError(
            f"post-define verification failed for domain {domain!r}: {error}; "
            f"configuration may have been applied, and the original backup remains at "
            f"{backup_path}"
        ) from error
    if verification.changed:
        raise ConfigurationError(
            f"post-define verification failed for domain {domain!r}; configuration may "
            f"have been applied, and the original backup remains at {backup_path}"
        )
    try:
        _assert_unrelated_semantics_preserved(
            original,
            persisted,
            allow_new_virtio_serial_controller=operation == "enable",
        )
    except ConfigurationError as error:
        raise ConfigurationError(
            f"post-define verification found unrelated XML changes for domain {domain!r}; "
            f"the original backup remains at {backup_path}"
        ) from error

    if reporter is not None:
        reporter(_managed_persisted_diff(original, persisted))

    return DomainApplyResult(True, change.changes, backup_path, persisted)


def _default_backup_dir() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "utm-spice-dnd" / "domain-backups"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure a shut-off libvirt QEMU/KVM VM for target-aware SPICE DnD."
    )
    parser.add_argument("operation", choices=("enable", "disable"))
    parser.add_argument("domain", help="libvirt domain name or UUID")
    parser.add_argument(
        "--connect", dest="connection_uri", help="libvirt connection URI (default: virsh default)"
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=_default_backup_dir(),
        help="directory for private inactive-domain XML backups",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = apply_to_domain(
            args.domain,
            args.operation,
            args.backup_dir,
            connection_uri=args.connection_uri,
            reporter=print,
        )
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not result.changed:
        print(f"No changes needed for {args.domain!r}.")
        return 0

    print(f"Updated inactive libvirt XML for {args.domain!r}.")
    print(f"Backup: {result.backup_path}")
    print("Changes:")
    for change in result.changes:
        print(f"  - {change}")
    print("The changes take effect the next time the VM starts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
