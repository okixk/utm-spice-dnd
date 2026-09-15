import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


GUEST_ROOT = Path(__file__).parents[1]
RULE_NAME = "70-utm-dnd.rules"


class GuestInstallSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.guest_root = self.root / "guest"
        shutil.copytree(GUEST_ROOT, self.guest_root)
        self.home = self.root / "home"
        self.home.mkdir()
        self.rule_target = self.root / "etc" / "udev" / "rules.d" / RULE_NAME
        self.rule_target.parent.mkdir(parents=True)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self._write_command("systemctl", "exit 0")
        self._write_command("gnome-shell", "exit 0")
        self._write_command("gnome-extensions", "exit 0")
        self._write_command("spice-vdagent", "exit 0")
        self._write_command("udevadm", "exit 0")
        self._write_command(
            "sudo",
            'if [ -n "${SWAP_RULE_SOURCE:-}" ]; then\n'
            '    printf "%s\\n" "# swapped after privilege boundary" > "$SWAP_RULE_SOURCE"\n'
            "fi\n"
            'exec "$@"',
        )
        self._write_command(
            "stat",
            'for argument in "$@"; do last=$argument; done\n'
            'case "${last:-}" in\n'
            '    "${TEST_RULE_TARGET:-}"|*/.70-utm-dnd-uninstall.*/70-utm-dnd.rules)\n'
            '        if [ "${FAIL_RULE_STAT:-0}" = 1 ]; then exit 1; fi\n'
            '        if [ -n "${FAKE_RULE_STAT:-}" ]; then\n'
            '            printf "%s\\n" "$FAKE_RULE_STAT"\n'
            "            exit 0\n"
            "        fi\n"
            "        ;;\n"
            "esac\n"
            'exec /usr/bin/stat "$@"',
        )
        self._write_command(
            "mv",
            'source=\n'
            'for argument in "$@"; do\n'
            '    case "$argument" in -*) ;; *)\n'
            '        if [ -z "$source" ]; then source=$argument; fi\n'
            '    esac\n'
            'done\n'
            'if [ "${FAIL_RULE_QUARANTINE:-0}" = 1 ] '
            '&& [ "$source" = "${TEST_RULE_TARGET:-}" ]; then\n'
            "    exit 1\n"
            "fi\n"
            'if [ "${FAKE_EXTENSION_MOUNT:-0}" = 1 ] '
            '&& [ "$source" = "${TEST_EXTENSION_TARGET:-}" ]; then\n'
            "    exit 1\n"
            "fi\n"
            '/usr/bin/mv "$@"\n'
            'status=$?\n'
            'if [ "$status" -eq 0 ] && [ "${RACE_REPLACE_RULE:-0}" = 1 ] '
            '&& [ "$source" = "${TEST_RULE_TARGET:-}" ]; then\n'
            '    printf "%s\\n" "# concurrent administrator replacement" > "$TEST_RULE_TARGET"\n'
            "fi\n"
            'if [ "$status" -eq 0 ] && [ "${RACE_REPLACE_RULE_DIR:-0}" = 1 ] '
            '&& [ "$source" = "${TEST_RULE_TARGET:-}" ]; then\n'
            '    mkdir -p -- "$TEST_RULE_TARGET"\n'
            '    printf "%s\\n" "concurrent directory" > "$TEST_RULE_TARGET/admin-marker"\n'
            "fi\n"
            'if [ "$status" -eq 0 ] && [ "${RACE_REPLACE_EXTENSION:-0}" = 1 ] '
            '&& [ "$source" = "${TEST_EXTENSION_TARGET:-}" ]; then\n'
            '    mkdir -p -- "$TEST_EXTENSION_TARGET"\n'
            '    printf "%s\\n" "concurrent replacement" > "$TEST_EXTENSION_TARGET/extension.js"\n'
            "fi\n"
            'exit "$status"',
        )
        self._write_command(
            "ln",
            'if [ "${RACE_RULE_DIRECTORY:-0}" = 1 ]; then\n'
            '    mkdir -p -- "$TEST_RULE_TARGET"\n'
            'fi\n'
            'exec /usr/bin/ln "$@"',
        )
        self._write_command(
            "mountpoint",
            '[ "${FAKE_EXTENSION_MOUNT:-0}" = 1 ]',
        )

        original_target = "/etc/udev/rules.d/70-utm-dnd.rules"
        for name in ("install.sh", "uninstall.sh"):
            script = self.guest_root / name
            content = script.read_text(encoding="utf-8")
            self.assertIn(original_target, content)
            script.write_text(
                content.replace(original_target, str(self.rule_target)),
                encoding="utf-8",
            )

    def tearDown(self):
        self.temporary.cleanup()

    def _write_command(self, name, body):
        path = self.bin_dir / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def _run(self, name, extra_environment=None):
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "USER": "test-user",
            "XDG_RUNTIME_DIR": str(self.root / "run"),
            "XDG_SESSION_TYPE": "wayland",
            "TEST_RULE_TARGET": str(self.rule_target),
            "TEST_EXTENSION_TARGET": str(
                self.home
                / ".local/share/gnome-shell/extensions/utm-dnd-target@utmapp.dev"
            ),
            "FAKE_RULE_STAT": "0:0:644",
            **(extra_environment or {}),
        }
        return subprocess.run(
            [str(self.guest_root / name)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    @property
    def project_rule(self):
        return (self.guest_root / "install" / RULE_NAME).read_bytes()

    def test_install_refuses_to_overwrite_different_rule(self):
        admin_rule = b"# administrator-managed rule\n"
        self.rule_target.write_bytes(admin_rule)

        result = self._run("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.rule_target.read_bytes(), admin_rule)
        self.assertIn("different content", result.stderr)
        self.assertIn(str(self.rule_target), result.stderr)

    def test_install_accepts_identical_existing_root_owned_rule(self):
        self.rule_target.write_bytes(self.project_rule)

        result = self._run("install.sh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rule_target.read_bytes(), self.project_rule)

    def test_install_rejects_identical_rule_with_untrusted_owner(self):
        self.rule_target.write_bytes(self.project_rule)

        result = self._run("install.sh", {"FAKE_RULE_STAT": "1000:1000:644"})

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.rule_target.read_bytes(), self.project_rule)
        self.assertIn("ownership or mode", result.stderr)

    def test_install_rejects_identical_group_writable_rule(self):
        self.rule_target.write_bytes(self.project_rule)

        result = self._run("install.sh", {"FAKE_RULE_STAT": "0:0:664"})

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.rule_target.read_bytes(), self.project_rule)
        self.assertIn("ownership or mode", result.stderr)

    def test_install_creates_missing_rule(self):
        result = self._run("install.sh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rule_target.read_bytes(), self.project_rule)
        self.assertEqual(self.rule_target.stat().st_mode & 0o777, 0o644)

    def test_install_never_links_into_concurrent_destination_directory(self):
        result = self._run("install.sh", {"RACE_RULE_DIRECTORY": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.rule_target.is_dir())
        self.assertEqual(list(self.rule_target.iterdir()), [])

    def test_install_uses_rule_snapshot_taken_before_sudo(self):
        expected = self.project_rule

        result = self._run(
            "install.sh",
            {"SWAP_RULE_SOURCE": str(self.guest_root / "install" / RULE_NAME)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rule_target.read_bytes(), expected)

    def test_install_fails_when_rule_source_is_missing(self):
        (self.guest_root / "install" / RULE_NAME).unlink()

        result = self._run("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.rule_target.exists())
        self.assertIn("could not read udev rule source", result.stderr)

    def test_install_fails_when_rule_source_is_unreadable(self):
        source = self.guest_root / "install" / RULE_NAME
        source.chmod(0)

        result = self._run("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.rule_target.exists())
        self.assertIn("could not read udev rule source", result.stderr)

    def test_install_refuses_symlink_even_when_content_matches(self):
        external = self.root / "admin-rule"
        external.write_bytes(self.project_rule)
        self.rule_target.symlink_to(external)

        result = self._run("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.rule_target.is_symlink())
        self.assertEqual(external.read_bytes(), self.project_rule)
        self.assertIn("symbolic link", result.stderr)

    def test_uninstall_removes_exact_project_rule(self):
        self.rule_target.write_bytes(self.project_rule)

        result = self._run("uninstall.sh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.rule_target.exists())

    def test_uninstall_preserves_exact_rule_with_untrusted_owner(self):
        self.rule_target.write_bytes(self.project_rule)

        result = self._run("uninstall.sh", {"FAKE_RULE_STAT": "1000:1000:644"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.rule_target.exists())
        self.assertIn("untrusted ownership or mode", result.stderr)

    def test_uninstall_uses_rule_snapshot_taken_before_sudo(self):
        expected = self.project_rule
        self.rule_target.write_bytes(expected)

        result = self._run(
            "uninstall.sh",
            {"SWAP_RULE_SOURCE": str(self.guest_root / "install" / RULE_NAME)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.rule_target.exists())

    def test_uninstall_never_deletes_concurrent_replacement(self):
        self.rule_target.write_bytes(self.project_rule)

        result = self._run("uninstall.sh", {"RACE_REPLACE_RULE": "1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.rule_target.read_text(encoding="utf-8"),
            "# concurrent administrator replacement\n",
        )

    def test_rule_rollback_does_not_nest_into_concurrent_directory(self):
        original = self.project_rule + b"# local policy\n"
        self.rule_target.write_bytes(original)

        result = self._run("uninstall.sh", {"RACE_REPLACE_RULE_DIR": "1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.rule_target.is_dir())
        self.assertEqual(
            (self.rule_target / "admin-marker").read_text(encoding="utf-8"),
            "concurrent directory\n",
        )
        self.assertFalse((self.rule_target / RULE_NAME).exists())
        backups = list(
            self.rule_target.parent.glob(
                f".70-utm-dnd-uninstall.*/{RULE_NAME}"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertIn("preserved prior file", result.stderr)

    def test_uninstall_reports_quarantine_failure_and_preserves_rule(self):
        expected = self.project_rule
        self.rule_target.write_bytes(expected)

        result = self._run("uninstall.sh", {"FAIL_RULE_QUARANTINE": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.rule_target.read_bytes(), expected)
        self.assertIn("could not quarantine", result.stderr)
        self.assertIn("preserving it", result.stderr)

    def test_uninstall_restores_rule_after_post_quarantine_failure(self):
        expected = self.project_rule
        self.rule_target.write_bytes(expected)

        result = self._run("uninstall.sh", {"FAIL_RULE_STAT": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.rule_target.read_bytes(), expected)
        self.assertEqual(
            list(self.rule_target.parent.glob(".70-utm-dnd-uninstall.*")),
            [],
        )

    def test_uninstall_preserves_rule_when_rule_source_is_missing(self):
        expected = self.project_rule
        self.rule_target.write_bytes(expected)
        (self.guest_root / "install" / RULE_NAME).unlink()

        result = self._run("uninstall.sh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rule_target.read_bytes(), expected)
        self.assertIn("could not read udev rule source", result.stderr)

    def test_uninstall_preserves_rule_when_rule_source_is_unreadable(self):
        expected = self.project_rule
        self.rule_target.write_bytes(expected)
        (self.guest_root / "install" / RULE_NAME).chmod(0)

        result = self._run("uninstall.sh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rule_target.read_bytes(), expected)
        self.assertIn("could not read udev rule source", result.stderr)

    def test_uninstall_preserves_changed_rule(self):
        admin_rule = self.project_rule + b"# local policy\n"
        self.rule_target.write_bytes(admin_rule)

        result = self._run("uninstall.sh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rule_target.read_bytes(), admin_rule)
        self.assertIn("not removing", result.stderr)
        self.assertIn("different content", result.stderr)

    def test_uninstall_preserves_symlink(self):
        external = self.root / "admin-rule"
        external.write_bytes(self.project_rule)
        self.rule_target.symlink_to(external)

        result = self._run("uninstall.sh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.rule_target.is_symlink())
        self.assertIn("not removing symbolic link", result.stderr)

    def test_uninstall_preserves_unexpected_extension_files(self):
        extension = (
            self.home
            / ".local/share/gnome-shell/extensions/utm-dnd-target@utmapp.dev"
        )
        extension.mkdir(parents=True)
        (extension / "extension.js").write_text("owned", encoding="utf-8")
        (extension / "metadata.json").write_text("owned", encoding="utf-8")
        unexpected = extension / "admin-note.txt"
        unexpected.write_text("preserve", encoding="utf-8")

        result = self._run("uninstall.sh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((extension / "extension.js").exists())
        self.assertFalse((extension / "metadata.json").exists())
        self.assertEqual(unexpected.read_text(encoding="utf-8"), "preserve")
        self.assertIn("unexpected files", result.stderr)

    def test_uninstall_does_not_traverse_extension_mountpoint(self):
        extension = (
            self.home
            / ".local/share/gnome-shell/extensions/utm-dnd-target@utmapp.dev"
        )
        extension.mkdir(parents=True)
        owned = extension / "extension.js"
        owned.write_text("mounted data", encoding="utf-8")

        result = self._run("uninstall.sh", {"FAKE_EXTENSION_MOUNT": "1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(owned.read_text(encoding="utf-8"), "mounted data")
        self.assertIn("mount boundary", result.stderr)

    def test_uninstall_never_deletes_concurrent_extension_replacement(self):
        extension = (
            self.home
            / ".local/share/gnome-shell/extensions/utm-dnd-target@utmapp.dev"
        )
        extension.mkdir(parents=True)
        (extension / "extension.js").write_text("owned", encoding="utf-8")
        (extension / "metadata.json").write_text("owned", encoding="utf-8")

        result = self._run("uninstall.sh", {"RACE_REPLACE_EXTENSION": "1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (extension / "extension.js").read_text(encoding="utf-8"),
            "concurrent replacement\n",
        )

    def test_extension_rollback_does_not_nest_into_concurrent_directory(self):
        extension = (
            self.home
            / ".local/share/gnome-shell/extensions/utm-dnd-target@utmapp.dev"
        )
        extension.mkdir(parents=True)
        (extension / "extension.js").write_text("owned", encoding="utf-8")
        unexpected = extension / "admin-note.txt"
        unexpected.write_text("preserve", encoding="utf-8")

        result = self._run("uninstall.sh", {"RACE_REPLACE_EXTENSION": "1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (extension / "extension.js").read_text(encoding="utf-8"),
            "concurrent replacement\n",
        )
        self.assertFalse((extension / "utm-dnd-target@utmapp.dev").exists())
        backups = list(
            extension.parent.glob(
                ".utm-dnd-uninstall.*/utm-dnd-target@utmapp.dev/admin-note.txt"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "preserve")
        self.assertIn("preserved prior extension", result.stderr)


if __name__ == "__main__":
    unittest.main()
