import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALL = Path(__file__).parents[1] / "install.sh"


class InstallSafetyTests(unittest.TestCase):
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
