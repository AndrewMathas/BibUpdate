"""Check installed launchers and optional metadata using isolated script copies."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import bibupdate


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.scripts = root / "scripts" / "bibupdate"
        self.bin = root / "bin"
        self.working = root / "unrelated"
        for directory in [self.scripts, self.bin, self.working]:
            directory.mkdir(parents=True)
        self.script = self.scripts / "bibupdate.py"
        shutil.copy2(bibupdate.__file__, self.script)
        (self.scripts / "bibupdate.ini").write_text("version = 9.8.7\n")
        # Metadata next to a launcher or in the current directory must not win.
        (self.bin / "bibupdate.ini").write_text("version = 0.0.0\n")
        (self.working / "bibupdate.ini").write_text("version = 7.7.7\n")

    def symlink(self, link, target):
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError) as error:
            self.skipTest("Symlinks unavailable: %s" % error)
        return link

    def run_script(self, script, option="--version"):
        return subprocess.run(
            [sys.executable, str(script), option], cwd=self.working,
            text=True, capture_output=True, check=True,
        ).stdout

    def test_absolute_symlink_uses_metadata_beside_the_target(self):
        launcher = self.symlink(self.bin / "bibupdate", self.script)
        self.assertIn("version 9.8.7:", self.run_script(launcher))

    def test_chained_relative_symlinks_use_metadata_beside_the_target(self):
        intermediate = self.bin.parent / "links"
        intermediate.mkdir()
        self.symlink(intermediate / "launcher", "../scripts/bibupdate/bibupdate.py")
        launcher = self.symlink(self.bin / "bibupdate", "../links/launcher")
        self.assertIn("version 9.8.7:", self.run_script(launcher))

    def test_missing_target_ini_uses_embedded_metadata_even_through_a_symlink(self):
        (self.scripts / "bibupdate.ini").unlink()
        launcher = self.symlink(self.bin / "bibupdate", self.script)
        for script in [self.script, launcher]:
            with self.subTest(script=script):
                self.assertIn("version %s:" % bibupdate.default_metadata["version"],
                              self.run_script(script))
                self.assertIn("Update and validate BibTeX files", self.run_script(script, "--help"))


if __name__ == "__main__":
    unittest.main()
