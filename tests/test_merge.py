"""Offline bibliography merging and citation remapping across input files."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bibupdate as b
from test_duplicates import article


class MergeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.first = self.directory / "first.bib"
        self.second = self.directory / "second.bib"
        self.third = self.directory / "third.bib"
        self.output = self.directory / "result.bib"
        self.tex = self.directory / "chapter one.tex"
        self.other = self.directory / "appendix.tex"
        self.first.write_text(article("First", title="One"))
        self.second.write_text(article("Second", title="Two"))
        self.third.write_text(article("Third", title="Three"))

    def run_cli(self, *arguments):
        with patch.object(b.sys, "argv", ["bibupdate", *map(str, arguments)]), \
             contextlib.redirect_stdout(io.StringIO()) as log, \
             patch.object(b, "lookup_entries", side_effect=AssertionError("AMS lookup attempted")), \
             patch.object(b, "arxiv_search", side_effect=AssertionError("arXiv lookup attempted")):
            b.main()
        return log.getvalue()

    def backup(self, path):
        return Path(str(path) + ".bak")

    def test_merge_preserves_unique_entries_headers_macros_comments_and_crlf(self):
        first = ('% First database\r\n@string{journal = "First Journal"}\r\n'
                 + article("First", journal="journal", custom="Keep this", coden="Keep me").replace("\n", "\r\n")
                 + '% No final newline')
        second = ('% Second database\n@preamble{"Extra preamble"}\n'
                  + article("Second", title="Two"))
        self.first.write_bytes(first.encode())
        self.second.write_bytes(second.encode())
        log = self.run_cli("--merge", self.first, self.second, "--output", self.output)
        self.assertEqual(self.output.read_bytes(), (first + "\n" + second).encode())
        self.assertEqual(self.first.read_bytes(), first.encode())
        self.assertEqual(self.second.read_bytes(), second.encode())
        self.assertIn("Merged 2 bibliography files", log)
        self.assertFalse(self.backup(self.first).exists())

    def test_three_inputs_use_transitive_matches_and_reserve_future_keys(self):
        first = article("Same", title="One", doi="10.1234/example")
        self.first.write_text(first)
        self.second.write_text(article("Alias", title="Changed title", doi="10.1234/example", mrnumber="12345")
                               + article("Same", title="Another paper", author="Smith, Alice", year="2020"))
        self.third.write_text(article("OtherAlias", title="Final title", mrnumber="MR12345")
                              + article("Smith2020", title="Reserved key")
                              + article("Ref", title="Reference paper", crossref="OtherAlias"))
        log = self.run_cli("--merge", self.first, self.second, self.third, "--output", self.output)
        result = self.output.read_text()
        self.assertTrue(result.startswith(first[:-2]))
        self.assertEqual(next(b.source_entries(result))["mrnumber"], "12345")
        self.assertEqual([entry.cite_key for entry in b.source_entries(result)],
                         ["Same", "Smith2020a", "Smith2020", "Ref"])
        self.assertIn("crossref = {Same}", result)
        normalized = " ".join(log.split())
        self.assertIn(str(self.second), normalized)
        self.assertIn(str(self.third), normalized)
        self.assertIn("line 7", normalized)

    def test_input_order_decides_which_paper_fields_are_retained(self):
        self.second.write_text(article("Alias", title="ONE", note="Second file's fields"))
        for order in [(self.first, self.second), (self.second, self.first)]:
            with self.subTest(order=order):
                self.run_cli("--merge", *order, "--output", self.output)
                entries = list(b.source_entries(self.output.read_text()))
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].cite_key, "First" if order[0] == self.first else "Alias")
                self.assertEqual(entries[0]["note"], "Second file's fields")

    def test_default_output_is_beside_first_input_and_does_not_overwrite_it(self):
        before = self.first.read_bytes()
        self.run_cli("--merge", self.first, self.second)
        result = self.first.with_name("merged_first.bib")
        self.assertTrue(result.exists())
        self.assertEqual(self.first.read_bytes(), before)
        self.assertEqual([e.cite_key for e in b.source_entries(result.read_text())], ["First", "Second"])

    def test_explicit_output_and_overwrite_both_back_up_existing_destinations(self):
        self.output.write_text("Previous output")
        self.run_cli("--merge", self.first, self.second, "--output", self.output)
        self.assertEqual(self.backup(self.output).read_text(), "Previous output")
        before = self.first.read_bytes()
        second_before = self.second.read_bytes()
        self.run_cli("--merge", "-o", self.first, self.second)
        self.assertEqual(self.backup(self.first).read_bytes(), before)
        self.assertIn("@article{Second,", self.first.read_text())
        self.assertEqual(self.second.read_bytes(), second_before)

    def test_output_can_explicitly_replace_an_input_after_all_inputs_are_read(self):
        before = self.second.read_bytes()
        self.run_cli("--merge", self.first, self.second, "--output", self.second)
        self.assertEqual(self.backup(self.second).read_bytes(), before)
        self.assertEqual([e.cite_key for e in b.source_entries(self.second.read_text())], ["First", "Second"])

    def test_merge_tex_lists_accept_both_flags_and_flexible_argument_order(self):
        self.second.write_text(article("Alias", title="One"))
        for flag in ["-t", "--texfile"]:
            argument_lists = [
                ["--merge", self.first, self.second, flag, self.tex, self.other],
                ["--merge", flag, self.tex, self.first, self.other, self.second],
                [self.first, flag, self.tex, "--merge", self.second, flag, self.other],
                ["--merge", f"{flag}={self.tex}", self.other, self.first, self.second],
            ]
            for arguments in argument_lists:
                with self.subTest(arguments=arguments):
                    self.tex.write_text(r"\cite{Alias}")
                    self.other.write_text(r"\nocite{Alias}")
                    self.run_cli(*arguments, "--output", self.output)
                    self.assertEqual(self.tex.read_text(), r"\cite{First}")
                    self.assertEqual(self.other.read_text(), r"\nocite{First}")
                    self.assertEqual(self.backup(self.tex).read_text(), r"\cite{Alias}")
                    self.assertEqual(self.backup(self.other).read_text(), r"\nocite{Alias}")

    def test_merge_leaves_ambiguous_citations_and_remaps_unambiguous_aliases(self):
        self.first.write_text(article("Same", title="One", author="Other, A."))
        self.second.write_text(article("Same", title="Two", author="Smith, Alice", year="2020")
                               + article("Alias", title="Two", author="A. Smith"))
        original = r"\citep[see]{Same,Alias}"
        self.tex.write_text(original)
        self.other.write_text(r"\cite{Same}")
        log = self.run_cli("--merge", "-q", self.first, self.second,
                           "--texfile", self.tex, self.other, "--output", self.output)
        self.assertEqual(self.tex.read_text(), r"\citep[see]{Same,Smith2020}")
        self.assertEqual(self.backup(self.tex).read_text(), original)
        self.assertEqual(self.other.read_text(), r"\cite{Same}")
        self.assertFalse(self.backup(self.other).exists())
        self.assertEqual(log.count("Ambiguous citation 'Same'"), 2)
        self.assertIn("Smith2020", log)

    def test_same_paper_with_same_key_is_removed_before_key_renaming(self):
        self.second.write_bytes(self.first.read_bytes())
        self.tex.write_text(r"\cite{First}")
        log = self.run_cli("--merge", self.first, self.second, "-t", self.tex,
                           "--output", self.output)
        self.assertEqual(len(list(b.source_entries(self.output.read_text()))), 1)
        self.assertNotIn("Renamed", log)
        self.assertNotIn("Ambiguous", log)
        self.assertFalse(self.backup(self.tex).exists())

    def test_lookup_and_formatting_flags_do_not_change_offline_merge(self):
        before = self.first.read_text() + self.second.read_text()
        self.run_cli("--merge", self.first, self.second, "--arxiv", "--all", "--threads", "2",
                     "--mathscinet", "-i", "title", "--wrap", "15", "--output", self.output)
        self.assertEqual(self.output.read_text(), before)

    def test_merge_adds_fields_from_all_copies_and_updates_tex_aliases(self):
        self.first.write_text(article("First", title="Base title", doi="10.1234/same", note=""))
        self.second.write_text(article("Second", title="Later title", doi="10.1234/same",
                                       journal="First journal", year="2020", note="Later note"))
        self.third.write_text(article("Third", title="Third title", doi="10.1234/same",
                                      journal="Later journal", year="2021", volume="12"))
        original = r"\cite{Second,Third}"
        self.tex.write_text(original)
        self.run_cli("--merge", self.first, self.second, self.third,
                     "--texfile", self.tex, "--output", self.output)
        entries = list(b.source_entries(self.output.read_text()))
        self.assertEqual(len(entries), 1)
        for name, value in [("title", "Base title"), ("note", ""), ("journal", "First journal"),
                            ("year", "2020"), ("volume", "12")]:
            self.assertEqual(entries[0][name], value, name)
        self.assertEqual(self.tex.read_text(), r"\cite{First,First}")
        self.assertEqual(self.backup(self.tex).read_text(), original)

    def test_imported_author_year_and_crossref_follow_final_renamed_keys(self):
        self.first.write_text(article("Same", title="Different paper"))
        self.second.write_text('@article{Same, title={Child}, doi={10.1234/child}}\n'
                               + article("Parent", title="Parent paper"))
        self.third.write_text(article("ChildAlias", title="Child", doi="10.1234/child",
                                      author="Smith, Alice", year="2020", crossref="ParentAlias")
                              + article("ParentAlias", title="Parent paper"))
        original = r"\cite{ChildAlias,ParentAlias,Same}"
        self.tex.write_text(original)
        log = self.run_cli("--merge", self.first, self.second, self.third,
                           "-t", self.tex, "--output", self.output)
        entries = list(b.source_entries(self.output.read_text()))
        self.assertEqual([entry.cite_key for entry in entries], ["Same", "Smith2020", "Parent"])
        self.assertEqual(entries[1]["author"], "Smith, Alice")
        self.assertEqual(entries[1]["year"], "2020")
        self.assertEqual(entries[1]["crossref"], "Parent")
        self.assertEqual(self.tex.read_text(), r"\cite{Smith2020,Parent,Same}")
        self.assertIn("Ambiguous citation 'Same'", log)
        self.assertEqual(self.backup(self.tex).read_text(), original)

    def test_missing_or_unclosed_input_prevents_all_output_and_tex_writes(self):
        for contents in [None, '@article{Bad, title={Unclosed}\n']:
            with self.subTest(contents=contents):
                if contents is None:
                    self.second.unlink(missing_ok=True)
                else:
                    self.second.write_text(contents)
                self.tex.write_text(r"\cite{Alias}")
                self.output.write_text("Existing output")
                with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
                    self.run_cli("--merge", self.first, self.second, self.third,
                                 "-t", self.tex, "--output", self.output)
                self.assertIn(str(self.second), errors.getvalue())
                self.assertEqual(self.output.read_text(), "Existing output")
                self.assertEqual(self.tex.read_text(), r"\cite{Alias}")
                self.assertFalse(self.backup(self.tex).exists())
                self.assertFalse(self.backup(self.output).exists())

    def test_invalid_merge_arguments_are_rejected_without_writes(self):
        link = self.directory / "link.bib"
        link.symlink_to(self.first)
        cases = [[], [self.first], [self.first, self.first], [self.first, link],
                 [self.first, self.directory / "other.txt"],
                 [self.first, self.second, "--check"],
                 [self.first, self.second, "--list-duplicates"],
                 [self.first, self.second, "--output", self.output, "-o"]]
        before = self.first.read_bytes()
        for arguments in cases:
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                self.run_cli("--merge", *arguments)
        self.assertEqual(self.first.read_bytes(), before)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.backup(self.first).exists())

    def test_default_output_that_is_another_input_requires_explicit_selection(self):
        existing = self.first.with_name("merged_first.bib")
        existing.write_bytes(self.second.read_bytes())
        before = existing.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
            self.run_cli("--merge", self.first, existing)
        self.assertIn("default merge output is an input", errors.getvalue())
        self.assertEqual(existing.read_bytes(), before)
        self.assertFalse(self.backup(existing).exists())

    def test_output_backup_cannot_overwrite_an_input_through_a_symlink(self):
        backup = self.backup(self.output)
        backup.symlink_to(self.second)
        before = self.second.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--merge", self.first, self.second, "--output", self.output)
        self.assertEqual(self.second.read_bytes(), before)
        self.assertFalse(self.output.exists())
        self.assertTrue(backup.is_symlink())

    def test_output_backup_cannot_overwrite_a_hard_linked_input(self):
        os.link(self.second, self.backup(self.output))
        before = self.second.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--merge", self.first, self.second, "--output", self.output)
        self.assertEqual(self.second.read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_missing_tex_file_prevents_merge_overwrite_and_backups(self):
        self.second.write_text(article("Alias", title="One"))
        self.tex.write_text(r"\cite{Alias}")
        before = self.first.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--merge", "-o", self.first, self.second,
                         "--texfile", self.tex, self.directory / "missing.tex")
        self.assertEqual(self.first.read_bytes(), before)
        self.assertEqual(self.tex.read_text(), r"\cite{Alias}")
        self.assertFalse(self.backup(self.first).exists())
        self.assertFalse(self.backup(self.tex).exists())

    def test_tex_inputs_and_backups_cannot_overlap_any_bib_input(self):
        self.tex.symlink_to(self.second)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--merge", self.first, self.second, "-t", self.tex, "--output", self.output)
        self.tex.unlink()
        self.tex.write_text(r"\cite{Alias}")
        self.backup(self.tex).symlink_to(self.second)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--merge", self.first, self.second, "-t", self.tex, "--output", self.output)
        self.assertFalse(self.output.exists())
        self.assertTrue(self.backup(self.tex).is_symlink())

    def test_comment_only_input_is_preserved_and_empty_merged_database_is_rejected(self):
        self.first.write_text("% Header with no entries\n")
        self.run_cli("--merge", self.first, self.second, "--output", self.output)
        self.assertEqual(self.output.read_text(), self.first.read_text() + self.second.read_text())
        self.second.write_text("@string{journal=\"A journal\"}\n")
        before = self.output.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("--merge", self.first, self.second, "--output", self.output)
        self.assertEqual(self.output.read_bytes(), before)

    def test_long_texfile_and_named_output_also_work_with_single_file_cleanup(self):
        self.first.write_text(article("First") + article("Alias"))
        for flag in ["--deduplicate", "--remove-duplicate-papers", "--rename-duplicate-keys"]:
            with self.subTest(flag=flag):
                self.tex.write_text(r"\cite{Alias}")
                self.run_cli(flag, self.first, "--texfile", self.tex, "--output", self.output)
                self.assertTrue(self.output.exists())
                expected = r"\cite{Alias}" if flag == "--rename-duplicate-keys" else r"\cite{First}"
                self.assertEqual(self.tex.read_text(), expected)

    def test_normal_mode_rejects_extra_inputs_and_conflicting_output_arguments(self):
        for arguments in [(self.first, self.second, self.third),
                          (self.first, self.second, "--output", self.output)]:
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                self.run_cli(*arguments)
        self.assertFalse(self.output.exists())

    def test_repeated_quiet_suppresses_merge_notices(self):
        self.assertEqual(self.run_cli("--merge", "-qq", self.first, self.second,
                                      "--output", self.output), "")
        self.assertTrue(self.output.exists())

    def test_help_modes_allow_merge_without_input_files(self):
        for flag in ["-h", "-H"]:
            with self.subTest(flag=flag), self.assertRaises(SystemExit) as raised:
                self.run_cli("--merge", flag)
            self.assertIn(raised.exception.code, (None, 0))


if __name__ == "__main__":
    unittest.main()
