import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HOST_DIR = Path(__file__).parents[1]
RUNTIME_SONAMES = (
    "libusbredirhost.so.1",
    "libusbredirparser.so.1",
    "libusb-1.0.so.0",
)


class RuntimeLauncherTests(unittest.TestCase):
    def _make_tree(self, launcher_name: str) -> tuple[Path, Path]:
        root = Path(self._temporary.name)
        script_dir = root / "host"
        script_dir.mkdir()
        shutil.copy2(HOST_DIR / launcher_name, script_dir / launcher_name)

        prefix = script_dir / ".build" / "prefix"
        runtime = prefix / "lib" / "runtime"
        runtime.mkdir(parents=True)
        (prefix / "lib" / "libspice-client-gtk-3.0.so.5").touch()
        for soname in RUNTIME_SONAMES:
            (runtime / soname).touch()
        return script_dir, prefix

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._temporary.cleanup()

    def _assert_launcher_environment(self, launcher_name: str, executable: Path):
        script_dir, prefix = self._make_tree(launcher_name)
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$LD_LIBRARY_PATH\"\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

        environment = dict(os.environ)
        environment["LD_LIBRARY_PATH"] = "/existing/private/lib"
        result = subprocess.run(
            ["sh", str(script_dir / launcher_name)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            f"{prefix}/lib:{prefix}/lib/runtime:/existing/private/lib",
        )

    def test_virt_manager_uses_private_runtime_libraries(self):
        root = Path(self._temporary.name)
        executable = root / "host" / ".build" / "src" / "virt-manager-5.1.0" / "virt-manager"
        self._assert_launcher_environment("run-virt-manager.sh", executable)

    def test_virt_viewer_uses_private_runtime_libraries(self):
        root = Path(self._temporary.name)
        executable = root / "host" / ".build" / "virt-viewer-root" / "usr" / "bin" / "virt-viewer"
        self._assert_launcher_environment("run-virt-viewer.sh", executable)

    def test_launcher_rejects_incomplete_private_runtime(self):
        script_dir, prefix = self._make_tree("run-virt-viewer.sh")
        (prefix / "lib" / "runtime" / RUNTIME_SONAMES[0]).unlink()
        viewer = script_dir / ".build" / "virt-viewer-root" / "usr" / "bin" / "virt-viewer"
        viewer.parent.mkdir(parents=True)
        viewer.touch(mode=0o755)

        result = subprocess.run(
            ["sh", str(script_dir / "run-virt-viewer.sh")],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime library is missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
