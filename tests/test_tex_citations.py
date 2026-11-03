"""Offline citation remapping, ambiguity handling and TeX file backups."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bibupdate as b
from test_duplicates import article


class CitationTests(unittest.TestCase):
    def remap(self, text):
        return b.remap_tex_citations(text, {"Old": "New", "Alias": "Smith2020"},
                                    {"Same": ["Same", "Jones2021"]})

    def test_standard_commands_stars_notes_case_and_key_boundaries(self):
        source = (r"\cite{Old, Other, OldExtra} \Citep*[see][p.~2]{Alias}" + "\n"
                  + r"\nocite{*,Old} \citeauthor{Old} \citeyearpar{Old}"
                  + r"\parencite[see {the [example]}]{Old} \Textcite{Old}")
        updated, count, unresolved = self.remap(source)
        expected = source.replace("{Old,", "{New,").replace(
            "{Alias}", "{Smith2020}").replace("*,Old", "*,New").replace("{Old}", "{New}")
        self.assertEqual(updated, expected)
        self.assertEqual(count, 7)
        self.assertFalse(unresolved)

    def test_multicites_and_nested_citation_in_optional_note(self):
        source = r"\parencites(see)(also)[note \cite{Alias}][2]{Old}[3]{Alias}{Same}"
        updated, count, unresolved = self.remap(source)
        self.assertEqual(updated, source.replace("{Old}", "{New}").replace("{Alias}", "{Smith2020}"))
        self.assertEqual(count, 3)
        self.assertEqual(unresolved, {"Same"})

    def test_whitespace_and_comments_in_and_between_arguments(self):
        source = "\\cite % Alias\n [see] % Old\n { Old, % Old\n Alias,\n Same }\n"
        updated, count, unresolved = self.remap(source)
        self.assertEqual(updated, source.replace("{ Old,", "{ New,").replace("\n Alias,", "\n Smith2020,"))
        self.assertEqual(count, 2)
        self.assertEqual(unresolved, {"Same"})

    def test_leaves_prose_comments_other_commands_and_macro_keys_alone(self):
        source = (r"Old \label{Old} \ref{Old} \bibliography{Old} \mycite{Old}" + "\n"
                  + r"% \cite{Old}" + "\n" + r"\\cite{Old} \cite{\macro}"
                  + r"\cite{{Old}} \citestyle{Old} \url{http://site/\cite{Old}}")
        self.assertEqual(self.remap(source), (source, 0, set()))

    def test_skips_inline_and_block_verbatim(self):
        examples = [r"\verb|\cite{Old}|", r"\verb*+\cite{Old}+",
                    r"\Verb|\cite{Old}|", r"\lstinline[language=TeX]|\cite{Old}|",
                    r"\lstinline{\cite{Old}}", r"\mintinline{tex}|\cite{Old}|",
                    r"\mintinline[escapeinside=||]{tex}{\cite{Old}}"]
        for env in ["verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "comment", "filecontents*"]:
            examples.append("\\begin{%s}\n\\cite{Old}\n\\end{%s}" % (env, env))
        for example in examples:
            with self.subTest(example=example):
                self.assertEqual(self.remap(example + r"\cite{Old}"),
                                 (example + r"\cite{New}", 1, set()))

    def test_malformed_or_unterminated_arguments_are_not_rewritten(self):
        for source in [r"\cite{Old", r"\cite[note{]{Old}", r"\verb|\cite{Old}",
                       "\\cite{Old % trailing comment}"]:
            with self.subTest(source=source):
                self.assertEqual(self.remap(source), (source, 0, set()))


class TexFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.bib = self.directory / "papers.bib"
        self.output = self.directory / "cleaned.bib"
        self.tex = self.directory / "chapter one.tex"
        self.other = self.directory / "appendix.tex"
        self.source = article("First") + article("Alias")
        self.bib.write_text(self.source)
        self.tex.write_text(r"\cite{Alias}")
        self.other.write_text(r"\nocite{Alias}")

    def run_cli(self, *arguments):
        with patch.object(b.sys, "argv", ["bibupdate", *map(str, arguments)]), \
             contextlib.redirect_stdout(io.StringIO()) as log, \
             patch.object(b, "lookup_entries", side_effect=AssertionError("AMS lookup attempted")), \
             patch.object(b, "arxiv_search", side_effect=AssertionError("arXiv lookup attempted")):
            b.main()
        return log.getvalue()

    def backup(self, path):
        return Path(str(path) + ".bak")

    def test_bib_position_and_multiple_or_repeated_tex_lists(self):
        argument_lists = [
            ["-t", self.tex, self.other, self.bib, self.output],
            [self.bib, "-t", self.tex, self.other, self.output],
            ["-t", self.tex, self.bib, self.other, self.output],
            ["-t", self.tex, "-t", self.other, self.bib, self.output],
            [f"-t={self.tex}", self.bib, f"-t={self.other}", self.output],
        ]
        for arguments in argument_lists:
            with self.subTest(arguments=arguments):
                self.tex.write_text(r"\cite{Alias}")
                self.other.write_text(r"\nocite{Alias}")
                self.run_cli("--deduplicate", *arguments)
                self.assertEqual(self.tex.read_text(), r"\cite{First}")
                self.assertEqual(self.other.read_text(), r"\nocite{First}")
                self.assertEqual(self.backup(self.tex).read_text(), r"\cite{Alias}")
                self.assertEqual(self.backup(self.other).read_text(), r"\nocite{Alias}")
                self.assertNotIn("@article{Alias,", self.output.read_text())

    def test_t_is_allowed_with_each_cleanup_mode(self):
        for flag in ["--deduplicate", "--remove-duplicate-papers", "--rename-duplicate-keys"]:
            with self.subTest(flag=flag):
                self.run_cli(flag, self.bib, "-t", self.tex)
        self.assertTrue((self.directory / "updated_papers.bib").is_file())

    def test_t_rejects_other_modes_or_bad_extensions_without_writes(self):
        arguments = [
            [self.bib, "-t", self.tex],
            ["--check", self.bib, "-t", self.tex],
            ["--list-duplicates", self.bib, "-t", self.tex],
            ["--deduplicate", self.bib, "-t"],
            ["--deduplicate", self.bib, "-t", self.directory / "file.txt"],
            ["--deduplicate", self.bib, "-t=file.bib"],
        ]
        for args in arguments:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                self.run_cli(*args)
        self.assertEqual(self.bib.read_text(), self.source)
        self.assertEqual(self.tex.read_text(), r"\cite{Alias}")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.backup(self.tex).exists())

    def test_only_changed_tex_files_are_backed_up_and_written(self):
        self.other.write_text(r"\cite{First}")
        before = self.other.stat().st_mtime_ns
        self.run_cli("--remove-duplicate-papers", "-t", self.tex, self.other, self.bib, self.output)
        self.assertEqual(self.backup(self.tex).read_text(), r"\cite{Alias}")
        self.assertFalse(self.backup(self.other).exists())
        self.assertEqual(self.other.stat().st_mtime_ns, before)

    def test_repeated_paths_and_symlinks_preserve_the_original_backup(self):
        link = self.directory / "link.tex"
        link.symlink_to(self.tex)
        self.run_cli("--deduplicate", "-t", link, self.tex, link, self.bib, self.output)
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.tex.read_text(), r"\cite{First}")
        self.assertEqual(self.backup(link).read_text(), r"\cite{Alias}")
        self.assertFalse(self.backup(self.tex).exists())

    def test_backup_replacement_and_permissions_preserve_bytes_and_newlines(self):
        original = b'% caf\xc3\xa9 \xff\r\n\\cite{ Alias }\r\n'
        self.tex.write_bytes(original)
        self.tex.chmod(0o640)
        self.backup(self.tex).write_bytes(b"Previous backup")
        self.run_cli("--deduplicate", "-t", self.tex, self.bib, self.output)
        self.assertEqual(self.tex.read_bytes(), original.replace(b"{ Alias }", b"{ First }"))
        self.assertEqual(self.backup(self.tex).read_bytes(), original)
        self.assertEqual(self.tex.stat().st_mode & 0o777, 0o640)

    def test_ambiguous_old_keys_stay_unchanged_and_are_reported(self):
        self.bib.write_text(article("Same", title="One", author="Other, A.")
                            + article("Same", title="Two", author="Smith, Alice", year="2020"))
        self.tex.write_text(r"\cite{Same}")
        log = self.run_cli("--rename-duplicate-keys", "-t", self.tex, self.bib, self.output)
        self.assertEqual(self.tex.read_text(), r"\cite{Same}")
        self.assertFalse(self.backup(self.tex).exists())
        self.assertIn("Ambiguous citation 'Same'", log)
        self.assertIn("Smith2020", log)
        self.assertIn("left unchanged", log)
        self.assertIn("@article{Smith2020,", self.output.read_text())

    def test_only_unambiguous_alias_follows_final_renamed_survivor(self):
        self.bib.write_text(article("Same", title="One", author="Other, A.")
                            + article("Same", title="Two", author="Smith, Alice", year="2020")
                            + article("Alias", title="Two", author="A. Smith"))
        original = r"\cite{Same,Alias}"
        self.tex.write_text(original)
        log = self.run_cli("--deduplicate", "-q", "-t", self.tex, self.bib, self.output)
        self.assertEqual(self.tex.read_text(), r"\cite{Same,Smith2020}")
        self.assertEqual(self.backup(self.tex).read_text(), original)
        self.assertIn("Ambiguous citation", log)
        self.assertIn("Updated citations", log)

    def test_repeated_removed_key_can_have_two_different_retained_targets(self):
        self.bib.write_text(article("First", title="One") + article("Second", title="Two")
                            + article("Alias", title="One") + article("Alias", title="Two")
                            + article("Reference", title="Three", crossref="Alias"))
        log = self.run_cli("--deduplicate", "-t", self.tex, self.bib, self.output)
        self.assertEqual(self.tex.read_text(), r"\cite{Alias}")
        self.assertIn("Ambiguous citation 'Alias'", log)
        self.assertIn("First, Second", log)
        self.assertIn("crossref = {Alias}", self.output.read_text())
        self.assertFalse(self.backup(self.tex).exists())

    def test_identical_repeated_keys_merge_without_false_ambiguity(self):
        self.bib.write_text(self.source + article("Alias"))
        log = self.run_cli("--deduplicate", "-t", self.tex, self.bib, self.output)
        self.assertEqual(self.tex.read_text(), r"\cite{First}")
        self.assertNotIn("Ambiguous", log)

    def test_missing_tex_prevents_bib_overwrite_or_any_tex_changes(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--deduplicate", "-o", "-t", self.tex,
                         self.directory / "missing.tex", self.bib)
        self.assertEqual(self.bib.read_text(), self.source)
        self.assertEqual(self.tex.read_text(), r"\cite{Alias}")
        self.assertFalse(self.backup(self.bib).exists())
        self.assertFalse(self.backup(self.tex).exists())

    def test_staging_or_backup_failure_prevents_data_writes_and_cleans_temporaries(self):
        for failure in ["stage_file", "replace"]:
            with self.subTest(failure=failure):
                target = b if failure == "stage_file" else b.os
                with patch.object(target, failure, side_effect=PermissionError("Backup denied")), \
                     contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.run_cli("--deduplicate", "-o", "-t", self.tex, self.bib)
                self.assertEqual(self.tex.read_text(), r"\cite{Alias}")
                self.assertEqual(self.bib.read_text(), self.source)
                self.assertFalse(list(self.directory.glob(".bibupdate-*")))

    def test_concurrent_tex_edit_is_detected_before_any_writes(self):
        plans, _ = b.prepare_tex_updates([str(self.tex)], {"Alias": "First"}, {},
                                        str(self.bib), str(self.output))
        self.tex.write_text("Edited by user")
        with self.assertRaisesRegex(ValueError, "changed during cleanup"):
            b.write_cleanup("updated bibliography", plans)
        self.assertEqual(self.tex.read_text(), "Edited by user")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.backup(self.tex).exists())

    def test_rejects_tex_symlink_to_bibliography_before_writes(self):
        self.tex.unlink()
        self.tex.symlink_to(self.bib)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--deduplicate", "-o", "-t", self.tex, self.bib)
        self.assertEqual(self.bib.read_text(), self.source)
        self.assertFalse(self.backup(self.tex).exists())

    def test_changed_file_and_bibliography_overwrite_have_separate_backups(self):
        self.run_cli("--deduplicate", "-o", "-t", self.tex, self.bib)
        self.assertEqual(self.backup(self.bib).read_text(), self.source)
        self.assertEqual(self.backup(self.tex).read_text(), r"\cite{Alias}")
        self.assertNotIn("@article{Alias,", self.bib.read_text())


if __name__ == "__main__":
    unittest.main()
