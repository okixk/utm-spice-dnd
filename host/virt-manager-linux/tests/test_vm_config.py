#!/usr/bin/python3

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "vm_config.py"
SPEC = importlib.util.spec_from_file_location("vm_config", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


BASE_XML = """\
<domain type='kvm'>
  <name>test-vm</name>
  <uuid>11111111-2222-3333-4444-555555555555</uuid>
  <metadata>
    <example:keep xmlns:example='urn:example'>unchanged</example:keep>
  </metadata>
  <devices>
    <!-- keep this comment -->
    <controller type='virtio-serial' index='0'/>
    <channel type='spicevmc'>
      <target type='virtio' name='com.redhat.spice.0'/>
    </channel>
    <graphics type='spice' autoport='yes'>
      <listen type='address'/>
      <image compression='off'/>
    </graphics>
    <disk type='file' device='disk'>
      <source file='/var/lib/libvirt/images/test.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
  </devices>
</domain>
"""


CONTROL_XML = """\
<channel type='spiceport'>
  <source channel='com.utmapp.dnd.0'/>
  <target type='virtio' name='com.utmapp.dnd.0'/>
</channel>
"""


def parse(xml):
    return MODULE.parse_domain_xml(xml)


class EnableXmlTests(unittest.TestCase):
    def test_adds_explicit_filetransfer_and_control_channel(self):
        result = MODULE.enable_xml(BASE_XML)

        root = parse(result.xml)
        graphics = root.find("./devices/graphics[@type='spice']")
        control = root.find("./devices/channel/target[@name='com.utmapp.dnd.0']/..")
        self.assertEqual(graphics.find("filetransfer").get("enable"), "yes")
        self.assertEqual(control.get("type"), "spiceport")
        self.assertEqual(control.find("source").get("channel"), "com.utmapp.dnd.0")
        self.assertEqual(control.find("target").get("type"), "virtio")
        self.assertIn("enabled SPICE file transfer", result.changes)
        self.assertIn("added com.utmapp.dnd.0 SPICE port channel", result.changes)
        self.assertIn("      <filetransfer enable=\"yes\" />", result.xml)
        self.assertIn(
            "    <channel type=\"spiceport\">\n"
            "      <source channel=\"com.utmapp.dnd.0\" />\n"
            "      <target type=\"virtio\" name=\"com.utmapp.dnd.0\" />\n"
            "    </channel>",
            result.xml,
        )

    def test_enables_disabled_filetransfer(self):
        source = BASE_XML.replace(
            "<listen type='address'/>",
            "<listen type='address'/>\n      <filetransfer enable='no'/>",
        )

        result = MODULE.enable_xml(source)

        root = parse(result.xml)
        self.assertEqual(
            root.find("./devices/graphics[@type='spice']/filetransfer").get("enable"),
            "yes",
        )

    def test_adds_normal_agent_and_controller_when_absent(self):
        source = BASE_XML.replace(
            "    <controller type='virtio-serial' index='0'/>\n", ""
        ).replace(
            "    <channel type='spicevmc'>\n"
            "      <target type='virtio' name='com.redhat.spice.0'/>\n"
            "    </channel>\n",
            "",
        )

        result = MODULE.enable_xml(source)

        root = parse(result.xml)
        normal = root.find("./devices/channel/target[@name='com.redhat.spice.0']/..")
        self.assertEqual(normal.get("type"), "spicevmc")
        self.assertIsNotNone(root.find("./devices/controller[@type='virtio-serial']"))
        self.assertIn("added normal SPICE agent channel", result.changes)
        self.assertIn("added virtio-serial controller", result.changes)

    def test_is_idempotent_and_does_not_mutate_input_tree(self):
        first = MODULE.enable_xml(BASE_XML)
        first_tree = parse(first.xml)

        second = MODULE.enable_xml(first.xml)

        self.assertFalse(second.changed)
        self.assertEqual(second.xml, first.xml)
        self.assertEqual(len(first_tree.findall("./devices/graphics/filetransfer")), 1)
        self.assertEqual(
            len(first_tree.findall("./devices/channel/target[@name='com.utmapp.dnd.0']")),
            1,
        )

    def test_preserves_unrelated_xml_and_comments(self):
        source = BASE_XML.replace(
            "  <metadata>", "  <?retain processing-instruction?>\n  <metadata>",
        )
        result = MODULE.enable_xml(source)
        root = parse(result.xml)

        self.assertEqual(
            root.find("./devices/disk/source").get("file"),
            "/var/lib/libvirt/images/test.qcow2",
        )
        self.assertEqual(root.find("./metadata/{urn:example}keep").text, "unchanged")
        self.assertIn("<!-- keep this comment -->", result.xml)
        self.assertIn("<?retain processing-instruction?>", result.xml)

    def test_semantic_verification_detects_changes_to_existing_controller(self):
        changed = BASE_XML.replace(
            "<controller type='virtio-serial' index='0'/>",
            "<controller type='virtio-serial' index='7'/>",
        )

        with self.assertRaisesRegex(MODULE.ConfigurationError, "unrelated XML"):
            MODULE._assert_unrelated_semantics_preserved(BASE_XML, changed)

    def test_semantic_verification_allows_new_managed_controller(self):
        without_controller = BASE_XML.replace(
            "    <controller type='virtio-serial' index='0'/>\n", ""
        )
        with_controller = MODULE.enable_xml(without_controller).xml

        MODULE._assert_unrelated_semantics_preserved(
            without_controller,
            with_controller,
            allow_new_virtio_serial_controller=True,
        )

    def test_rejects_non_qemu_domain(self):
        with self.assertRaisesRegex(MODULE.ConfigurationError, "QEMU/KVM"):
            MODULE.enable_xml(BASE_XML.replace("type='kvm'", "type='lxc'"))

    def test_rejects_domain_without_spice(self):
        with self.assertRaisesRegex(MODULE.ConfigurationError, "SPICE graphics"):
            MODULE.enable_xml(BASE_XML.replace("type='spice'", "type='vnc'"))

    def test_rejects_conflicting_or_duplicate_channels(self):
        conflicting = BASE_XML.replace(
            "  </devices>",
            "    <channel type='unix'>\n"
            "      <target type='virtio' name='com.utmapp.dnd.0'/>\n"
            "    </channel>\n"
            "  </devices>",
        )
        with self.assertRaisesRegex(MODULE.ConfigurationError, "conflicting"):
            MODULE.enable_xml(conflicting)

        duplicate_agent = BASE_XML.replace(
            "  </devices>",
            "    <channel type='spicevmc'>\n"
            "      <target type='virtio' name='com.redhat.spice.0'/>\n"
            "    </channel>\n"
            "  </devices>",
        )
        with self.assertRaisesRegex(MODULE.ConfigurationError, "duplicate"):
            MODULE.enable_xml(duplicate_agent)

        implicit_duplicate_agent = BASE_XML.replace(
            "  </devices>",
            "    <channel type='spicevmc'>\n"
            "      <target type='virtio'/>\n"
            "    </channel>\n"
            "  </devices>",
        )
        with self.assertRaisesRegex(MODULE.ConfigurationError, "duplicate"):
            MODULE.enable_xml(implicit_duplicate_agent)


class DisableXmlTests(unittest.TestCase):
    def test_removes_only_dnd_channel(self):
        enabled = MODULE.enable_xml(BASE_XML).xml

        result = MODULE.disable_xml(enabled)

        root = parse(result.xml)
        self.assertIsNone(
            root.find("./devices/channel/target[@name='com.utmapp.dnd.0']/..")
        )
        self.assertIsNotNone(
            root.find("./devices/channel/target[@name='com.redhat.spice.0']/..")
        )
        self.assertEqual(
            root.find("./devices/graphics[@type='spice']/filetransfer").get("enable"),
            "yes",
        )
        self.assertIsNotNone(root.find("./devices/controller[@type='virtio-serial']"))
        self.assertEqual(result.changes, ("removed com.utmapp.dnd.0 SPICE port channel",))

    def test_is_idempotent_when_control_channel_absent(self):
        result = MODULE.disable_xml(BASE_XML)
        self.assertFalse(result.changed)
        self.assertEqual(result.xml, BASE_XML)

    def test_refuses_to_remove_a_conflicting_channel(self):
        conflicting = BASE_XML.replace(
            "  </devices>",
            "    <channel type='unix'>\n"
            "      <target type='virtio' name='com.utmapp.dnd.0'/>\n"
            "    </channel>\n"
            "  </devices>",
        )

        with self.assertRaisesRegex(MODULE.ConfigurationError, "conflicting"):
            MODULE.disable_xml(conflicting)


class FakeCommandRunner:
    def __init__(
        self,
        xml,
        state="shut off",
        define_returncode=0,
        xml_after=None,
        xml_predefine=None,
        persisted_xml=None,
    ):
        self.xml = xml
        self.xml_after = xml_after
        self.xml_predefine = xml_predefine
        self.persisted_xml = persisted_xml
        self.state = state
        self.define_returncode = define_returncode
        self.calls = []
        self.dump_count = 0
        self.defined_xml = None

    def __call__(self, command, **kwargs):
        self.calls.append(tuple(command))
        if "dumpxml" in command:
            self.dump_count += 1
            if self.dump_count == 2 and self.xml_after:
                xml = self.xml_after
            elif self.dump_count == 3 and self.xml_predefine:
                xml = self.xml_predefine
            elif self.defined_xml is not None and self.dump_count >= 3:
                xml = self.persisted_xml or self.defined_xml or self.xml
            else:
                xml = self.xml
            return SimpleNamespace(returncode=0, stdout=xml, stderr="")
        if "domstate" in command:
            return SimpleNamespace(returncode=0, stdout=f"{self.state}\n", stderr="")
        if "define" in command:
            self.defined_xml = Path(command[-1]).read_text(encoding="utf-8")
            return SimpleNamespace(
                returncode=self.define_returncode,
                stdout="Domain 'test-vm' defined\n" if self.define_returncode == 0 else "",
                stderr="define failed\n" if self.define_returncode else "",
            )
        raise AssertionError(f"unexpected command: {command}")


class ApplyTests(unittest.TestCase):
    def test_accepts_virsh_shut_off_reason_format(self):
        MODULE._require_powered_off("shut off (unknown)", "vm", "test")

    def test_refuses_running_domain_before_backup_or_define(self):
        runner = FakeCommandRunner(BASE_XML, state="running")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.ConfigurationError, "shut off"):
                MODULE.apply_to_domain(
                    "test-vm", "enable", Path(directory), runner=runner
                )
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertFalse(any("define" in call for call in runner.calls))

    def test_refuses_domain_with_managed_save_before_backup_or_define(self):
        runner = FakeCommandRunner(BASE_XML, state="shut off (saved)")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.ConfigurationError, "managed save"):
                MODULE.apply_to_domain(
                    "test-vm", "enable", Path(directory), runner=runner
                )
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertFalse(any("define" in call for call in runner.calls))

    def test_unchanged_running_domain_is_safe_and_does_not_create_backup(self):
        enabled = MODULE.enable_xml(BASE_XML).xml
        runner = FakeCommandRunner(enabled, state="running")
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.apply_to_domain(
                "test-vm", "enable", Path(directory), runner=runner
            )
            self.assertFalse(result.changed)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertFalse(any("domstate" in call for call in runner.calls))

    def test_backs_up_original_with_private_permissions_then_defines(self):
        runner = FakeCommandRunner(BASE_XML)
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.apply_to_domain(
                "test-vm", "enable", Path(directory), runner=runner
            )
            backups = list(Path(directory).glob("*.xml"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), BASE_XML)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
            self.assertEqual(result.backup_path, backups[0])
        define_call = next(call for call in runner.calls if "define" in call)
        self.assertIn("--validate", define_call)
        self.assertGreaterEqual(runner.dump_count, 3)
        self.assertEqual(result.xml, runner.defined_xml)

    def test_fsyncs_backup_candidate_and_directory_before_define(self):
        events = []

        class OrderedRunner(FakeCommandRunner):
            def __call__(self, command, **kwargs):
                if "define" in command:
                    events.append("define")
                return super().__call__(command, **kwargs)

        def record_fsync(descriptor):
            kind = "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
            events.append(f"fsync-{kind}")

        runner = OrderedRunner(BASE_XML)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.os, "fsync", side_effect=record_fsync
        ):
            MODULE.apply_to_domain(
                "test-vm", "enable", Path(directory), runner=runner
            )

        define_index = events.index("define")
        durable_events = events[:define_index]
        self.assertGreaterEqual(durable_events.count("fsync-file"), 2)
        self.assertGreaterEqual(durable_events.count("fsync-directory"), 2)
        self.assertEqual(durable_events[-1], "fsync-directory")

    def test_define_failure_reports_backup_for_manual_recovery(self):
        runner = FakeCommandRunner(BASE_XML, define_returncode=1)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.ConfigurationError, "backup remains at"):
                MODULE.apply_to_domain(
                    "test-vm", "enable", Path(directory), runner=runner
                )
            self.assertEqual(len(list(Path(directory).glob("*.xml"))), 1)

    def test_passes_connection_uri_and_domain_as_separate_arguments(self):
        enabled = MODULE.enable_xml(BASE_XML).xml
        runner = FakeCommandRunner(enabled)
        with tempfile.TemporaryDirectory() as directory:
            MODULE.apply_to_domain(
                "name;not-a-shell-command",
                "enable",
                Path(directory),
                connection_uri="qemu:///system",
                runner=runner,
            )
        self.assertEqual(
            runner.calls[0],
            (
                "virsh",
                "--connect",
                "qemu:///system",
                "dumpxml",
                "--inactive",
                "name;not-a-shell-command",
            ),
        )

    def test_refuses_concurrent_domain_xml_drift(self):
        drifted = BASE_XML.replace("<name>test-vm</name>", "<name>changed-elsewhere</name>")
        runner = FakeCommandRunner(BASE_XML, xml_after=drifted)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.ConfigurationError, "changed concurrently"):
                MODULE.apply_to_domain(
                    "test-vm", "enable", Path(directory), runner=runner
                )
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertFalse(any("define" in call for call in runner.calls))

    def test_refuses_last_moment_xml_drift_after_durable_backup(self):
        drifted = BASE_XML.replace("<name>test-vm</name>", "<name>late-change</name>")
        runner = FakeCommandRunner(BASE_XML, xml_predefine=drifted)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.ConfigurationError, "changed concurrently"):
                MODULE.apply_to_domain(
                    "test-vm", "enable", Path(directory), runner=runner
                )
            self.assertEqual(len(list(Path(directory).glob("*.xml"))), 1)
        self.assertFalse(any("define" in call for call in runner.calls))

    def test_reports_scoped_diff_of_actual_persisted_xml_after_define(self):
        events = []

        class OrderedRunner(FakeCommandRunner):
            def __call__(self, command, **kwargs):
                if "define" in command:
                    events.append("define")
                return super().__call__(command, **kwargs)

        candidate = MODULE.enable_xml(BASE_XML).xml
        persisted = candidate.replace(
            "<source channel=\"com.utmapp.dnd.0\" />",
            "<source channel=\"com.utmapp.dnd.0\" />\n      "
            "<alias name=\"persisted-control\" />",
        )
        runner = OrderedRunner(BASE_XML, persisted_xml=persisted)
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.apply_to_domain(
                "test-vm",
                "enable",
                Path(directory),
                runner=runner,
                reporter=lambda message: events.append(message),
            )

        self.assertEqual(events[0], "define")
        self.assertIn("--- managed-dnd-before.xml", events[-1])
        self.assertIn("+++ managed-dnd-persisted.xml", events[-1])
        self.assertIn("persisted-control", events[-1])
        self.assertEqual(result.xml, persisted)

    def test_report_does_not_disclose_unrelated_xml_or_spice_password(self):
        source = BASE_XML.replace(
            "autoport='yes'", "autoport='yes' passwd='do-not-print-me'"
        ).replace("test.qcow2", "sensitive-disk-name.qcow2")
        reports = []
        runner = FakeCommandRunner(source)
        with tempfile.TemporaryDirectory() as directory:
            MODULE.apply_to_domain(
                "test-vm", "enable", Path(directory), runner=runner,
                reporter=reports.append,
            )

        self.assertEqual(len(reports), 1)
        self.assertNotIn("do-not-print-me", reports[0])
        self.assertNotIn("sensitive-disk-name.qcow2", reports[0])
        self.assertNotIn("<disk", reports[0])

    def test_rejects_post_define_verification_failure_and_keeps_backup(self):
        runner = FakeCommandRunner(BASE_XML, persisted_xml=BASE_XML)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.ConfigurationError, "verification failed"):
                MODULE.apply_to_domain(
                    "test-vm", "enable", Path(directory), runner=runner
                )
            self.assertEqual(len(list(Path(directory).glob("*.xml"))), 1)

    def test_rejects_unrelated_post_define_semantic_drift(self):
        persisted = MODULE.enable_xml(BASE_XML).xml.replace(
            "test.qcow2", "changed-by-someone-else.qcow2"
        )
        runner = FakeCommandRunner(BASE_XML, persisted_xml=persisted)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.ConfigurationError, "unrelated XML"):
                MODULE.apply_to_domain(
                    "test-vm", "enable", Path(directory), runner=runner
                )


if __name__ == "__main__":
    unittest.main()
