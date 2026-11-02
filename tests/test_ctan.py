"""Check CTAN release packaging without an upload or documentation build."""

import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import bibupdate


spec = importlib.util.spec_from_file_location(
    "bibupdate_ctan_build", Path(__file__).resolve().parents[1] / "setup.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class CtanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "release"
        self.directory.mkdir()
        for name in build.CTAN_FILES:
            (self.directory / name).write_bytes(("Release fixture: " + name).encode())
        shutil.copy2(bibupdate.__file__, self.directory / "bibupdate.py")
        (self.directory / "bibupdate.py").chmod(0o755)
        (self.directory / "bibupdate.ini").write_text("version = 9.8.7\n")

    def generate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            build.build_ctan_package(self.directory)

    def test_archive_contains_only_release_files_with_their_contents_and_modes(self):
        self.generate()
        with tarfile.open(self.directory / "bibupdate.tar.gz", "r:gz") as package:
            self.assertCountEqual(package.getnames(), [
                "bibupdate/bibupdate.py", "bibupdate/README.md", "bibupdate/bibupdate.tex",
                "bibupdate/bibupdate.pdf", "bibupdate/bibupdate.ini", "bibupdate/LICENSE.txt",
                "bibupdate/CHANGES.md",
            ])
            for member in package.getmembers():
                self.assertTrue(member.isfile())
                source = self.directory / Path(member.name).name
                self.assertEqual(package.extractfile(member).read(), source.read_bytes())
            self.assertEqual(package.getmember("bibupdate/bibupdate.py").mode & 0o777, 0o755)
        configuration = (self.directory / "bibupdate.ctan").read_text()
        self.assertIn("file=bibupdate.tar.gz\n", configuration)
        self.assertIn("version=9.8.7\n", configuration)
        self.assertFalse(any(path.name.startswith(".ctan-") for path in self.directory.iterdir()))

    def test_missing_release_files_do_not_replace_existing_outputs(self):
        archive = self.directory / "bibupdate.tar.gz"
        configuration = self.directory / "bibupdate.ctan"
        archive.write_bytes(b"previous archive")
        configuration.write_text("previous metadata")
        for name in build.CTAN_FILES:
            with self.subTest(missing=name):
                path = self.directory / name
                contents = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(FileNotFoundError, name):
                    self.generate()
                self.assertEqual(archive.read_bytes(), b"previous archive")
                self.assertEqual(configuration.read_text(), "previous metadata")
                path.write_bytes(contents)

    def test_command_uses_its_source_directory_when_run_elsewhere(self):
        shutil.copy2(build.__file__, self.directory / "setup.py")
        result = subprocess.run(
            [sys.executable, str(self.directory / "setup.py"), "ctan"],
            cwd=self.directory.parent, text=True, capture_output=True, check=True,
        )
        self.assertTrue((self.directory / "bibupdate.tar.gz").is_file())
        self.assertIn("version=9.8.7\n", (self.directory / "bibupdate.ctan").read_text())
        self.assertIn("ctanupload -F bibupdate.ctan", result.stdout)
        self.assertFalse((self.directory.parent / "bibupdate.tar.gz").exists())
        self.assertFalse((self.directory.parent / "bibupdate.ctan").exists())

    def test_changing_input_does_not_replace_existing_archive_or_metadata(self):
        archive = self.directory / "bibupdate.tar.gz"
        configuration = self.directory / "bibupdate.ctan"
        archive.write_bytes(b"previous archive")
        configuration.write_text("previous metadata")
        original_add = tarfile.TarFile.add
        def change_during_add(package, name, *args, **kwargs):
            original_add(package, name, *args, **kwargs)
            if Path(name).name == "bibupdate.pdf":
                Path(name).write_bytes(b"PDF rewritten by another process")
        with patch.object(tarfile.TarFile, "add", new=change_during_add), \
             self.assertRaisesRegex(OSError, "changed while packaging"):
            self.generate()
        self.assertEqual(archive.read_bytes(), b"previous archive")
        self.assertEqual(configuration.read_text(), "previous metadata")


if __name__ == "__main__":
    unittest.main()
