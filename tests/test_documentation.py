"""Protect handwritten manual text while regenerating documentation."""

import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import bibupdate


spec = importlib.util.spec_from_file_location(
    "bibupdate_build", Path(__file__).resolve().parents[1] / "setup.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class RegionTests(unittest.TestCase):
    def test_missing_duplicate_or_reversed_markers_are_rejected(self):
        for source in ["unmarked handwritten text", "END ... BEGIN", "BEGIN BEGIN END"]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                build.replace_region(source, "BEGIN", "END", "generated")

    def test_surrounding_manual_text_is_preserved_verbatim(self):
        source = "Title and custom style\nBEGIN\nold help\nEND\nHandwritten chapter\n"
        self.assertEqual(
            build.replace_region(source, "BEGIN", "END", "new help"),
            "Title and custom style\nBEGIN\nnew help\nEND\nHandwritten chapter\n",
        )


@unittest.skipUnless(shutil.which("pandoc"), "Pandoc is a build-only dependency")
class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.manual = self.directory / "bibupdate.tex"
        self.template = (
            "Handwritten preamble\n" + build.PREAMBLE_BEGIN + "\nold packages\n"
            + build.PREAMBLE_END + "\nHandwritten introduction\n"
            + build.BODY_BEGIN + "\nold help\n" + build.BODY_END
            + "\nHandwritten bug report instructions\n"
            + build.LICENCE_BEGIN + "\nold licence\n" + build.LICENCE_END + "\n"
        )
        self.manual.write_text(self.template)

    def generate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            build.build_documentation(self.directory, make_pdf=False)

    def test_sources_follow_builtin_help_and_preserve_handwritten_regions(self):
        self.generate()
        readme = (self.directory / "README.md").read_text()
        self.assertEqual(readme, build.readme_markdown())
        self.assertTrue(readme.startswith("# bibupdate\n\n[![Version]"))
        self.assertEqual(readme.count("https://img.shields.io/"), 3)
        self.assertIn("python3.9%2B", readme)
        self.assertIn("https://www.gnu.org/licenses/gpl-3.0.en.html", readme)
        self.assertNotIn("www.maths.usyd.edu.au/u/mathas/", readme)
        generated = self.manual.read_text()
        self.assertNotIn(r"\includegraphics", generated)
        self.assertNotIn("www.maths.usyd.edu.au/u/mathas/", generated)
        self.assertIn(r"\subsection*{Options and defaults}", generated)
        self.assertNotIn(r"\section{", generated)
        self.assertNotIn(r"\subsection{", generated)
        self.assertNotIn(r"\tableofcontents", generated)
        self.assertNotIn("{Support}", generated)
        self.assertNotIn("{To do}", generated)
        self.assertNotIn("{Author}", generated)
        self.assertGreater(generated.index(r"\subsection*{Licence}"),
                           generated.index("Handwritten bug report instructions"))
        self.assertIn("Duplicate warnings", generated)
        # Removing the generated regions from both versions leaves identical text.
        for begin, end in build.GENERATED_REGIONS:
            generated = build.replace_region(generated, begin, end, "")
            self.template = build.replace_region(self.template, begin, end, "")
        self.assertEqual(generated, self.template)
        self.assertFalse((self.directory / "README.pdf").exists())
        self.assertFalse((self.directory / "README.tex").exists())
        self.assertFalse((self.directory / "bibupdate.pdf").exists())

    def test_rerunning_generation_is_idempotent_and_tracks_new_help(self):
        self.generate()
        first = self.manual.read_bytes()
        first_readme = (self.directory / "README.md").read_bytes()
        self.generate()
        self.assertEqual(self.manual.read_bytes(), first)
        self.assertEqual((self.directory / "README.md").read_bytes(), first_readme)
        with patch.object(bibupdate, "__doc__", "# Program\n\nNew help text.\n\n## Licence\n\nGPL.\n"):
            self.generate()
        self.assertIn("New help text", self.manual.read_text())
        self.assertNotIn("old help", self.manual.read_text())
        self.assertIn("Handwritten bug report instructions", self.manual.read_text())

    def test_option_descriptions_are_indented_without_indenting_other_sections(self):
        self.generate()
        generated = self.manual.read_text()
        before, _, options = generated.partition(r"\subsection*{Options and defaults}")
        options, _, after = options.partition(r"\subsection*{Known issues}")
        begin = r"\begin{BibUpdateOptionDescription}"
        end = r"\end{BibUpdateOptionDescription}"
        self.assertNotIn(begin, before)
        self.assertNotIn(begin, after)
        groups = options.split(r"\subsubsection*")[1:]
        self.assertEqual(len(groups), 6)
        for group in groups:
            heading, _, descriptions = group.partition(r"\BibUpdateOptionHeading*")
            self.assertNotIn(begin, heading)
            descriptions = descriptions.split(r"\BibUpdateOptionHeading*")
            self.assertTrue(descriptions)
            for description in descriptions:
                self.assertEqual(description.count(begin), 1)
                self.assertEqual(description.count(end), 1)
                self.assertTrue(description.rstrip().endswith(end))

    def test_invalid_markers_leave_both_files_untouched(self):
        self.manual.write_text("Unmarked handwritten manual")
        readme = self.directory / "README.md"
        readme.write_text("Original README")
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.manual.read_text(), "Unmarked handwritten manual")
        self.assertEqual(readme.read_text(), "Original README")

    def test_pdf_build_targets_manual_and_propagates_compiler_failure(self):
        real_run = subprocess.run
        def fail_compiler(command, **kwargs):
            if command[-1] == "bibupdate.tex":
                raise subprocess.CalledProcessError(1, command)
            return real_run(command, **kwargs)
        with patch.object(build.subprocess, "run", side_effect=fail_compiler) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                build.build_documentation(self.directory)
        self.assertEqual(run.call_args[0][0][-1], "bibupdate.tex")
        self.assertEqual(run.call_args.kwargs["cwd"], self.directory)

    def test_missing_pandoc_leaves_sources_untouched(self):
        with patch.object(build.shutil, "which", return_value=None), \
             self.assertRaisesRegex(FileNotFoundError, "Pandoc"):
            self.generate()
        self.assertEqual(self.manual.read_text(), self.template)
        self.assertFalse((self.directory / "README.md").exists())

    def test_missing_licence_leaves_sources_untouched(self):
        with patch.object(bibupdate, "__doc__", "# Program\n\nNo licence.\n"), \
             self.assertRaisesRegex(ValueError, "Licence"):
            self.generate()
        self.assertEqual(self.manual.read_text(), self.template)
        self.assertFalse((self.directory / "README.md").exists())


if __name__ == "__main__":
    unittest.main()
