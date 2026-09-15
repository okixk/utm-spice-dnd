import shutil
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALL = Path(__file__).parents[1] / "install.sh"
UNINSTALL = Path(__file__).parents[1] / "uninstall.sh"


class InstallSafetyTests(unittest.TestCase):
    @staticmethod
    def _guard_environment(root: Path, rejected_path: Path) -> dict[str, str]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        python = fake_bin / "python3"
        python.write_text(
            "#!/bin/sh\n"
            "[ \"${2-}\" != \"$REJECT_MOUNT_PATH\" ]\n",
            encoding="utf-8",
        )
        python.chmod(0o755)

        apt_get = fake_bin / "apt-get"
        apt_get.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        apt_get.chmod(0o755)

        return {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "REJECT_MOUNT_PATH": str(rejected_path.resolve()),
        }

    def test_rejects_symlinked_build_root_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script_dir = root / "host"
            external = root / "external"
            script_dir.mkdir()
            (external / "src").mkdir(parents=True)
            sentinel = external / "src" / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            copied = script_dir / "install.sh"
            shutil.copy2(INSTALL, copied)
            (script_dir / ".build").symlink_to(external, target_is_directory=True)

            result = subprocess.run(
                ["sh", str(copied)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_rejects_mounted_build_root_without_touching_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script_dir = root / "host"
            build_root = script_dir / ".build"
            build_root.mkdir(parents=True)
            sentinel = build_root / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            copied = script_dir / "install.sh"
            shutil.copy2(INSTALL, copied)

            result = subprocess.run(
                ["sh", str(copied)],
                check=False,
                capture_output=True,
                text=True,
                env=self._guard_environment(root, build_root),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mount", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_rejects_mounted_reset_target_without_deleting_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script_dir = root / "host"
            sysroot = script_dir / ".build" / "sysroot"
            sysroot.mkdir(parents=True)
            sentinel = sysroot / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            copied = script_dir / "install.sh"
            shutil.copy2(INSTALL, copied)

            result = subprocess.run(
                ["sh", str(copied)],
                check=False,
                capture_output=True,
                text=True,
                env=self._guard_environment(root, sysroot),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mount", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")


class UninstallSafetyTests(unittest.TestCase):
    def test_rejects_mounted_build_root_without_touching_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script_dir = root / "host"
            build_root = script_dir / ".build"
            build_root.mkdir(parents=True)
            sentinel = build_root / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            copied = script_dir / "uninstall.sh"
            shutil.copy2(UNINSTALL, copied)

            result = subprocess.run(
                ["sh", str(copied)],
                check=False,
                capture_output=True,
                text=True,
                env=InstallSafetyTests._guard_environment(root, build_root),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mount", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_removes_ordinary_isolated_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script_dir = root / "host"
            build_root = script_dir / ".build"
            build_root.mkdir(parents=True)
            (build_root / "artifact").write_text("remove", encoding="utf-8")
            copied = script_dir / "uninstall.sh"
            shutil.copy2(UNINSTALL, copied)

            result = subprocess.run(
                ["sh", str(copied)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(build_root.exists())
