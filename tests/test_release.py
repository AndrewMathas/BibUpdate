"""Exercise release gates in isolated Git checkouts, without an upload."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

import bibupdate


spec = importlib.util.spec_from_file_location(
    "bibupdate_release_build", Path(__file__).resolve().parents[1] / "setup.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@unittest.skipUnless(shutil.which("git"), "Release checks use Git")
class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "release"
        self.directory.mkdir()
        self.metadata = dict(bibupdate.default_metadata, version="1.2.3", copyright="2014-2020")
        self.metadata["release date"] = "2 November 2020 AEST"
        self.script = ('"""Release fixture.\nCopyright (C) 2014-2020\n"""\n'
                       + "default_metadata = " + repr(self.metadata) + "\n")
        self.write_ini()
        (self.directory / "bibupdate.py").write_text(self.script)
        (self.directory / "README.md").write_text("Generated README\n")
        (self.directory / "bibupdate.tex").write_text("Generated manual\n")
        (self.directory / "bibupdate.pdf").write_bytes(b"PDF fixture")
        (self.directory / "bibupdate.log").write_text("Output written on bibupdate.pdf (1 page).\n")
        (self.directory / "LICENSE.txt").write_text("Licence fixture\n")
        (self.directory / "CHANGES.md").write_text("## Version 1.2.3 - 2020-11-02\n\nRelease notes.\n")
        (self.directory / ".gitignore").write_text("__pycache__/\nbibupdate.log\nbibupdate.tar.gz\nbibupdate.ctan\n")
        tests = self.directory / "tests"
        tests.mkdir()
        (tests / "test_smoke.py").write_text(
            "import unittest\nclass Smoke(unittest.TestCase):\n"
            "    def test_example(self): self.assertEqual(2 + 2, 4)\n"
        )
        self.run_git("init", "--quiet")
        excludes = self.directory.parent / "empty-gitignore"
        excludes.write_text("")
        self.run_git("config", "core.excludesFile", str(excludes))
        self.commit()
        newer = time.time_ns() + 1_000_000_000
        for name in ["bibupdate.pdf", "bibupdate.log"]:
            os.utime(self.directory / name, ns=(newer, newer))

        real_run = subprocess.run
        real_which = shutil.which
        def run(command, **kwargs):
            if command[0] == "fixture-pdfinfo":
                return subprocess.CompletedProcess(command, 0, "Title: bibupdate - 1.2.3\nPages: 1\n", "")
            return real_run(command, **kwargs)
        patchers = [
            patch.object(build.subprocess, "run", side_effect=run),
            patch.object(build.shutil, "which", side_effect=lambda name:
                         "fixture-pdfinfo" if name == "pdfinfo" else real_which(name)),
            patch.object(build, "build_documentation", side_effect=self.generated_documentation),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_git(self, *args):
        return subprocess.run(["git", *args], cwd=self.directory, text=True,
                              capture_output=True, check=True)

    def commit(self):
        self.run_git("add", ".")
        hooks = self.directory.parent / "empty-hooks"
        hooks.mkdir(exist_ok=True)
        self.run_git("-c", "user.name=Release Tests", "-c", "user.email=release@example.invalid",
                     "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(hooks),
                     "commit", "--quiet", "--message", "Release fixture")

    def write_ini(self):
        (self.directory / "bibupdate.ini").write_text(
            "".join("%s = %s\n" % pair for pair in self.metadata.items())
        )

    def generated_documentation(self, directory, make_pdf=True):
        self.assertFalse(make_pdf)
        directory = Path(directory)
        (directory / "README.md").write_text("Generated README\n")
        (directory / "bibupdate.tex").write_text("Generated manual\n")

    def checks(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return build.check_release(self.directory, **kwargs)

    def assert_failed(self, label):
        failures = self.checks(skip_tests=True)
        self.assertTrue(any(message.startswith(label + ":") for message in failures), failures)

    def test_ready_release_runs_tests_and_packages_the_checked_files(self):
        (self.directory / "bibupdate.tar.gz").write_bytes(b"old archive")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(build.release(self.directory))
        with tarfile.open(self.directory / "bibupdate.tar.gz") as package:
            self.assertEqual(package.extractfile("bibupdate/bibupdate.py").read(),
                             (self.directory / "bibupdate.py").read_bytes())
        self.assertIn("version=1.2.3\n", (self.directory / "bibupdate.ctan").read_text())

    def test_check_only_does_not_write_release_files(self):
        before = {name: (self.directory / name).read_bytes() for name in build.CTAN_FILES}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(build.release(self.directory, check_only=True))
        self.assertEqual(before, {name: (self.directory / name).read_bytes() for name in build.CTAN_FILES})
        self.assertFalse((self.directory / "bibupdate.tar.gz").exists())
        self.assertFalse((self.directory / "bibupdate.ctan").exists())

    def test_old_archive_is_removed_before_checks_even_when_they_fail(self):
        archive = self.directory / "bibupdate.tar.gz"
        def failed_checks(directory, skip_tests=False):
            self.assertEqual(directory, self.directory)
            self.assertFalse(archive.exists())
            return ["Readiness failure"]
        for check_only in [False, True]:
            with self.subTest(check_only=check_only):
                archive.write_bytes(b"old archive")
                with patch.object(build, "check_release", side_effect=failed_checks), \
                     patch.object(build, "build_ctan_package") as package, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertFalse(build.release(self.directory, check_only=check_only))
                package.assert_not_called()
                self.assertFalse(archive.exists())

    def test_prerelease_and_metadata_mismatch_block_packaging(self):
        self.metadata["version"] = "1.2.3-rc1"
        self.write_ini()
        with contextlib.redirect_stdout(io.StringIO()):
            failures = build.check_release(self.directory, skip_tests=True)
            self.assertFalse(build.release(self.directory, skip_tests=True))
        self.assertTrue(any(f.startswith("Release version:") for f in failures))
        self.assertTrue(any(f.startswith("Standalone metadata:") for f in failures))
        self.assertFalse((self.directory / "bibupdate.tar.gz").exists())

    def test_copyright_and_changelog_must_match_release_date(self):
        self.metadata["release date"] = "2 November 2019 AEST"
        self.write_ini()
        self.assert_failed("Copyright years")
        self.assert_failed("Changelog")

    def test_invalid_date_is_reported_without_stopping_other_checks(self):
        self.metadata["release date"] = "not a date"
        self.write_ini()
        failures = self.checks(skip_tests=True)
        self.assertTrue(any(f.startswith("Release date:") for f in failures))
        self.assertTrue(any(f.startswith("Copyright years:") for f in failures))
        self.assertTrue(any(f.startswith("Changelog:") for f in failures))

    def test_bold_changelog_heading_is_accepted_without_an_explicit_date(self):
        (self.directory / "CHANGES.md").write_text("**v1.2.3** -- CTAN release\n")
        self.commit()
        self.assertEqual(self.checks(skip_tests=True), [])

    def test_changelog_can_omit_a_zero_patch_component(self):
        self.metadata["version"] = "1.2.0"
        self.write_ini()
        (self.directory / "CHANGES.md").write_text("**v1.2** -- CTAN release\n")
        self.assertFalse(any(f.startswith("Changelog:") for f in self.checks(skip_tests=True)))

    def test_changelog_title_and_badges_can_precede_the_version_heading(self):
        for heading in ["## Version 1.2.3 - 2020-11-02", "**v1.2.3** -- CTAN release"]:
            with self.subTest(heading=heading):
                (self.directory / "CHANGES.md").write_text(
                    "[![Version](https://img.shields.io/badge/version-9.9.9-success)](https://example.invalid/)\n"
                    "[![Python](https://img.shields.io/badge/python-3.9-important)](https://example.invalid/)\n"
                    "\n# **BibUpdate** - Change log\n\n" + heading + "\n\n- Release notes\n"
                )
                self.assertFalse(any(f.startswith("Changelog:") for f in self.checks(skip_tests=True)))

    def test_first_version_heading_must_match_even_if_a_later_heading_matches(self):
        (self.directory / "CHANGES.md").write_text(
            "# **BibUpdate** - Change log\n\n## Version 1.2.4\n\n"
            "- New changes\n\n## Version 1.2.3\n\n- Old changes\n"
        )
        self.assert_failed("Changelog")

    def test_titled_changelog_still_requires_a_version_heading_and_matching_date(self):
        for contents in [
            "# **BibUpdate** - Change log\n\n- Requires Python 3.9\n",
            "# **BibUpdate** - Change log\n\n## Version 1.2.3 - 2020-11-03\n",
        ]:
            with self.subTest(contents=contents):
                (self.directory / "CHANGES.md").write_text(contents)
                self.assert_failed("Changelog")

    def test_untracked_files_are_reported(self):
        (self.directory / "forgotten.txt").write_text("Untracked release input")
        self.assert_failed("Git working tree")

    def test_staged_changes_are_reported(self):
        (self.directory / "LICENSE.txt").write_text("Changed licence")
        self.run_git("add", "LICENSE.txt")
        self.assert_failed("Git working tree")

    def test_generated_outputs_do_not_make_a_clean_checkout_dirty(self):
        (self.directory / "bibupdate.tar.gz").write_bytes(b"old archive")
        (self.directory / "bibupdate.ctan").write_text("old upload metadata")
        self.assertEqual(self.checks(skip_tests=True), [])

    def test_stale_documentation_is_reported(self):
        (self.directory / "README.md").write_text("Old README")
        self.assert_failed("Generated documentation")

    def test_old_pdf_and_unresolved_references_are_reported(self):
        os.utime(self.directory / "bibupdate.pdf", (1, 1))
        (self.directory / "bibupdate.log").write_text("LaTeX Warning: There were undefined references.\n")
        self.assert_failed("Manual")
        self.assert_failed("LaTeX log")

    def test_wrong_pdf_version_is_reported(self):
        original = subprocess.run
        def wrong_version(command, **kwargs):
            if command[0] == "fixture-pdfinfo":
                return subprocess.CompletedProcess(command, 0, "Title: bibupdate - 0.0.0\nPages: 1\n", "")
            return original(command, **kwargs)
        with patch.object(build.subprocess, "run", side_effect=wrong_version):
            self.assert_failed("Manual")

    def test_failed_tests_block_packaging_but_can_be_explicitly_skipped(self):
        (self.directory / "tests/test_smoke.py").write_text(
            "import unittest\nclass Smoke(unittest.TestCase):\n"
            "    def test_failure(self): self.fail('intentional failure')\n"
        )
        self.commit()
        self.assertTrue(any(f.startswith("Test suite:") for f in self.checks()))
        self.assertEqual(self.checks(skip_tests=True), [])

    def test_empty_test_discovery_is_a_failure(self):
        (self.directory / "tests/test_smoke.py").write_text("# No test cases\n")
        self.commit()
        self.assertTrue(any(f.startswith("Test suite:") and "no tests" in f
                            for f in self.checks()))

    def test_force_overrides_readiness_failures_but_not_missing_package_files(self):
        (self.directory / "untracked.txt").write_text("Work in progress")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(build.release(self.directory, force=True, skip_tests=True))
            self.assertFalse(build.release(self.directory, force=True, check_only=True, skip_tests=True))
            (self.directory / "bibupdate.pdf").unlink()
            with self.assertRaises(FileNotFoundError):
                build.release(self.directory, force=True, skip_tests=True)

    def test_wrapper_forwards_arguments_when_run_outside_the_source_directory(self):
        shutil.copy2(Path(build.__file__).with_name("make_release"), self.directory / "make_release")
        (self.directory / "setup.py").write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\n")
        for flags in [["--check-only", "--skip-tests"], ["-m"], ["-t"], ["-mt"]]:
            with self.subTest(flags=flags):
                result = subprocess.run(["sh", str(self.directory / "make_release"), *flags],
                                        cwd=self.directory.parent, text=True, capture_output=True, check=True)
                self.assertEqual(json.loads(result.stdout), ["release", *flags])

    def test_cli_returns_failure_status_for_failed_check_only(self):
        with patch.object(build.sys, "argv", ["setup.py", "release", "--check-only"]), \
             patch.object(build, "release", return_value=False) as release:
            with self.assertRaises(SystemExit) as result:
                build.main()
        self.assertEqual(result.exception.code, 1)
        release.assert_called_once_with(check_only=True, force=False, skip_tests=False)


class MaintenanceTests(unittest.TestCase):
    def run_actions(self, *flags):
        with patch.object(build.sys, "argv", ["setup.py", "release", *flags]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            build.main()
        return output.getvalue()

    def test_actions_run_only_requested_work_in_manual_then_tests_order(self):
        for flags, expected in [
            (("-m",), ["manual"]), (("--manual",), ["manual"]),
            (("-t",), ["tests"]), (("--tests",), ["tests"]),
            (("-mt",), ["manual", "tests"]), (("-tm",), ["manual", "tests"]),
        ]:
            calls = []
            def run_tests():
                calls.append("tests")
                return "Ran 1 test in 0.001s; OK"
            with self.subTest(flags=flags), \
                 patch.object(build, "build_documentation", side_effect=lambda: calls.append("manual")), \
                 patch.object(build, "run_tests", side_effect=run_tests), \
                 patch.object(build, "release", side_effect=AssertionError("Release checks attempted")), \
                 patch.object(build, "build_ctan_package", side_effect=AssertionError("Packaging attempted")):
                output = self.run_actions(*flags)
            self.assertEqual(calls, expected)
            self.assertNotIn("\033[", output)
            if "tests" in expected:
                self.assertIn("✓ Test suite: Ran 1 test", output)

    def test_standalone_tests_use_real_discovery_and_return_failure_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            tests = directory / "tests"
            tests.mkdir()
            test = tests / "test_smoke.py"
            test.write_text("import unittest\nclass Smoke(unittest.TestCase):\n"
                            "    def test_failure(self): self.fail('intentional failure')\n")
            original = build.run_tests
            with patch.object(build, "run_tests", side_effect=lambda: original(directory)), \
                 contextlib.redirect_stderr(io.StringIO()) as errors, \
                 self.assertRaises(SystemExit) as result:
                self.run_actions("-t")
            self.assertEqual(result.exception.code, 1)
            self.assertIn("intentional failure", errors.getvalue())
            self.assertIn("✗", errors.getvalue())
            self.assertFalse((directory / "bibupdate.tar.gz").exists())

    def test_failed_manual_build_stops_combined_action_with_failure_status(self):
        with patch.object(build, "build_documentation", side_effect=subprocess.CalledProcessError(1, "latexmk")), \
             patch.object(build, "run_tests") as tests, \
             contextlib.redirect_stderr(io.StringIO()) as errors, \
             self.assertRaises(SystemExit) as result:
            self.run_actions("-mt")
        self.assertEqual(result.exception.code, 1)
        tests.assert_not_called()
        self.assertIn("latexmk", errors.getvalue())

    def test_maintenance_actions_reject_release_only_flags(self):
        for flags in [("-m", "-c"), ("-t", "-s"), ("-mt", "-f")]:
            with self.subTest(flags=flags), \
                 contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as result:
                self.run_actions(*flags)
            self.assertEqual(result.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
