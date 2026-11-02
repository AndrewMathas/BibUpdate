"""Local duplicate detection without network access or bibliography mutations."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bibupdate as b


def article(key, title="A paper on representation theory", author="Achar, Pramod N.", **fields):
    fields = dict(title=title, author=author, **fields)
    return "@article{%s,\n%s\n}\n" % (
        key, "\n".join("  %s = {%s}," % item for item in fields.items())
    )


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(b.bibup, "fix_fonts", b.replace_fonts, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def warnings(self, *papers):
        entries = list(b.parse_entries("\n".join(papers)))
        before = [(entry.cite_key, dict(entry)) for entry in entries]
        warnings = list(b.duplicate_warnings(entries))
        self.assertEqual([(entry.cite_key, dict(entry)) for entry in entries], before)
        self.assertTrue(all(len(line) <= 78 for warning in warnings for line in warning.splitlines()))
        return warnings

    def test_repeated_key_reports_all_source_lines_once(self):
        warnings = self.warnings(
            article("Same", title="First paper"),
            article("Same", title="Second paper"),
            article("Same", title="Third paper"),
        )
        self.assertEqual(warnings, ["! Duplicate citation key 'Same': lines 1, 6, 11."])

    def test_source_lines_include_preamble_and_blank_lines(self):
        entries = list(b.parse_entries("Preamble\n\n" + article("A") + "\n\n" + article("B")))
        self.assertEqual([entry.source_line for entry in entries], [3, 9])

    def test_shared_identifiers_detect_different_titles_and_keys(self):
        for first, second in [
            ({"doi": "10.1090/jams/905"}, {"doi": "https://doi.org/10.1090/JAMS/905"}),
            ({"mrnumber": "MR03868004"}, {"mrnumber": "3868004"}),
            ({"eprint": "1706.00183v2"}, {"eprint": "https://arxiv.org/abs/1706.00183"}),
            ({"eprint": "math/0201001v1"}, {"eprint": "math/0201001v3"}),
        ]:
            with self.subTest(first=first):
                warnings = self.warnings(
                    article("FirstKey", title="Original title", **first),
                    article("SecondKey", title="Revised title", **second),
                )
                self.assertEqual(len(warnings), 1)
                self.assertIn("Duplicate paper (shared", warnings[0])
                self.assertIn("'FirstKey' (line 1)", warnings[0])
                self.assertIn("'SecondKey'", warnings[0])

    def test_title_and_author_normalization_handles_bibtex_variants(self):
        warnings = self.warnings(
            article("One", title="Koszul duality for {Kac-Moody} groups",
                    author="Achar, Pramod N. and Riche, Simon"),
            article("Two", title="KOSZUL duality for Kac–Moody groups.",
                    author="P. N. Achar and S. Riche"),
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("Possible duplicate paper (same title and authors)", warnings[0])

    def test_same_title_with_different_authors_is_not_a_duplicate(self):
        self.assertEqual(self.warnings(
            article("One", author="Achar, Pramod"),
            article("Two", author="Riche, Simon"),
        ), [])

    def test_missing_authors_and_invalid_identifiers_do_not_create_matches(self):
        self.assertEqual(self.warnings(
            article("One", author="", doi="unknown", mrnumber="", eprint="none"),
            article("Two", author="", doi="unknown", mrnumber="", eprint="none"),
        ), [])

    def test_distinct_mathematics_in_titles_is_preserved(self):
        self.assertEqual(self.warnings(
            article("One", title="The $q$-Schur algebra"),
            article("Two", title="The $q^2$-Schur algebra"),
        ), [])

    def test_transitive_identifier_matches_form_one_group(self):
        warnings = self.warnings(
            article("One", title="Title A", doi="10.1234/example"),
            article("Two", title="Title B", doi="10.1234/example", mrnumber="12345"),
            article("Three", title="Title C", mrnumber="MR12345"),
        )
        self.assertEqual(len(warnings), 1)
        for key in ["One", "Two", "Three"]:
            self.assertIn("'%s'" % key, warnings[0])

    def test_repeated_key_and_duplicate_paper_are_both_reported(self):
        warnings = self.warnings(article("One"), article("One"), article("Two"))
        self.assertEqual(len(warnings), 2)
        self.assertIn("Duplicate citation key", warnings[0])
        self.assertIn("Possible duplicate paper", warnings[1])

    def test_keys_in_custom_entry_types_are_also_checked(self):
        warnings = self.warnings(
            article("Same", title="First paper").replace("@article", "@online"),
            article("Same", title="Second paper").replace("@article", "@online"),
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("Duplicate citation key 'Same'", warnings[0])

    def test_comments_are_not_treated_as_duplicate_papers_or_keys(self):
        self.assertEqual(self.warnings(
            article("Same").replace("@article", "@comment"),
            article("Same"),
        ), [])


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.input = self.directory / "papers.bib"
        self.output = self.directory / "cleaned.bib"

    def run_cli(self, source, *flags, output=True):
        self.input.write_bytes(source.encode("utf-8"))
        arguments = ["bibupdate", *flags, str(self.input)]
        if output:
            arguments.append(str(self.output))
        with patch.object(b.sys, "argv", arguments), \
             contextlib.redirect_stdout(io.StringIO()) as log, \
             patch.object(b, "lookup_entries", side_effect=AssertionError("AMS lookup attempted")), \
             patch.object(b, "arxiv_search", side_effect=AssertionError("arXiv lookup attempted")):
            b.main()
        return log.getvalue()

    def test_listing_filters_keys_and_papers_and_preserves_existing_output(self):
        source = (article("Repeated", title="First title")
                  + article("Repeated", title="Another title")
                  + article("Alias", title="First title"))
        self.output.write_text("Keep this output\n")
        for flag, keys, papers in [("--list-keys", True, False),
                                   ("--list-papers", False, True),
                                   ("--list-duplicates", True, True), ("-d", True, True)]:
            with self.subTest(flag=flag):
                log = self.run_cli(source, flag, "--arxiv", "--overwrite")
                self.assertEqual("Duplicate citation key" in log, keys)
                self.assertEqual("duplicate paper" in log, papers)
                self.assertEqual(self.input.read_text(), source)
                self.assertEqual(self.output.read_text(), "Keep this output\n")
                self.assertEqual(len(list(self.directory.iterdir())), 2)
        self.assertIn("Duplicate citation key", self.run_cli(source, "--list-keys", "--list-papers"))

    def test_duplicate_papers_with_the_same_key_are_listed_and_removed(self):
        first, second = article("Same"), article("Same", author="P. N. Achar")
        self.assertIn("duplicate paper", self.run_cli(first + second, "--list-papers"))
        log = self.run_cli(first + second, "--duplicate-papers")
        self.assertEqual(self.output.read_text(), first + "\n")
        self.assertIn("Removed duplicate paper 'Same'", log)

    def test_paper_removal_retains_first_fields_without_merging_or_renaming(self):
        first = article("Repeated", title="First paper", mrreviewer="Keep me")
        different = article("Repeated", title="Different paper")
        duplicate = article("Alias", title="FIRST paper", doi="10.1234/extra")
        log = self.run_cli(first + different + duplicate, "--duplicate-papers", "--arxiv", "-i", "title")
        self.assertEqual(self.output.read_text(), first + different + "\n")
        self.assertNotIn("Renamed", log)
        self.assertNotIn("10.1234/extra", self.output.read_text())
        self.assertIn("kept 'Repeated' (line 1)", log)

    def test_keys_only_never_deletes_papers_and_reserves_future_keys(self):
        first = article("Original", title="One", author="Someone, A.")
        repeated = article("Original", title="Two", author="Achar, Pramod and S. Riche", year="2019")
        later = article("AcharRiche2019", title="Reserved", author="Other, B.")
        source = first + repeated + repeated + later
        log = self.run_cli(source, "--duplicate-keys")
        expected = (first + repeated.replace("{Original,", "{AcharRiche2019a,", 1)
                    + repeated.replace("{Original,", "{AcharRiche2019b,", 1) + later)
        self.assertEqual(self.output.read_text(), expected)
        self.assertEqual(log.count("Renamed duplicate key"), 2)
        self.assertNotIn("Removed", log)

    def test_key_suffixes_continue_after_z(self):
        source = "".join(article("Key", title=str(i), author="Smith, A.", year="2020")
                         for i in range(29))
        self.run_cli(source, "--duplicate-keys")
        entries = list(b.source_entries(self.output.read_text()))
        self.assertEqual(entries[-1].cite_key, "Smith2020aa")
        self.assertEqual(len({entry.cite_key for entry in entries}), 29)

    def test_key_names_use_editors_unicode_accents_or_anonymous_fallback(self):
        source = (article("Same", title="First")
                  + article("Same", title="Second", author="", editor="García, María", year="2021")
                  + article("Same", title="Third", author="", year="2022")
                  + article("Same", title="Fourth", author=r"Garc\'ia, Maria"))
        self.run_cli(source, "--duplicate-keys")
        self.assertEqual([e.cite_key for e in b.source_entries(self.output.read_text())],
                         ["Same", "Garcia2021", "Anonymous2022", "Garcia"])

    def test_cleanup_preserves_macros_comments_quoted_and_nested_fields_exactly(self):
        preamble = '% @article{NotAnEntry, title={Ignore}}\n@string{journal = "Journal"}\n'
        retained = (r'''@article ( First,
  title = "A {nested} title with an @ sign",
  author = "Smith, Alice",
  journal = journal # " Supplement",
  custom_field = {See @article{This, is not an entry} and {nested} data},
  mrreviewer = {Keep this field},
  note = {Quoted "word" and escaped \{brace\}},
)
''')
        duplicate = article("Second", title="A nested title with an @ sign", author="A. Smith")
        tail = '\n% End of database\n@preamble{"LaTeX preamble"}\n'
        source = preamble + retained + duplicate + tail
        self.run_cli(source, "--duplicates", "--wrap", "15", "-i", "mrreviewer")
        self.assertEqual(self.output.read_text(), preamble + retained + "\n" + tail)
        self.assertEqual(self.input.read_text(), source)

    def test_quote_in_an_existing_key_does_not_start_a_quoted_field(self):
        source = article('FeinbergLee"HomRepLKLRTypesAD')
        self.assertEqual(self.run_cli(source, "--duplicates"), "")
        self.assertEqual(self.output.read_text(), source)

    def test_cleanup_preserves_crlf_newlines(self):
        first, second = article("Same", title="One"), article("Same", title="Two")
        source = (first + second).replace("\n", "\r\n")
        expected = (first + second.replace("@article{Same,", "@article{Achar,", 1))
        self.run_cli(source, "--duplicate-keys")
        self.assertEqual(self.output.read_bytes(), expected.replace("\n", "\r\n").encode())

    def test_transitive_matches_keep_first_and_repair_literal_references(self):
        first = article("One", title="Title A", doi="10.1234/example")
        middle = article("Two", title="Title B", doi="10.1234/example", mrnumber="12345")
        last = article("Three", title="Title C", mrnumber="MR12345")
        referring = ('''@inproceedings{Ref,
  title = {A different paper},
  crossref = "Two",
  xref = {Three},
  related = {Two, Three, Other},
  entryset = {Two,Other},
  xdata = {Three},
  note = {Two and Three should stay unchanged here},
}
''')
        log = self.run_cli(first + middle + last + referring, "--duplicates")
        result = self.output.read_text()
        self.assertTrue(result.startswith(first))
        self.assertNotIn("@article{Two,", result)
        self.assertNotIn("@article{Three,", result)
        self.assertIn('crossref = "One"', result)
        self.assertIn("related = {One, One, Other}", result)
        self.assertIn("entryset = {One,Other}", result)
        self.assertIn("xref = {One}", result)
        self.assertIn("xdata = {One}", result)
        self.assertIn("note = {Two and Three should stay unchanged here}", result)
        self.assertEqual(log.count("Removed duplicate paper"), 2)

    def test_removed_references_use_retained_keys_after_renaming(self):
        first = article("Repeated", title="First paper", author="Other, A.")
        second = article("Repeated", title="Second paper", author="Smith, Alice", year="2020")
        alias = article("Removed", title="Second paper", author="A. Smith")
        reference = article("Ref", title="Third paper", crossref="Removed")
        log = self.run_cli(first + second + alias + reference, "--duplicates")
        self.assertIn("@article{Smith2020,", self.output.read_text())
        self.assertIn("crossref = {Smith2020}", self.output.read_text())
        self.assertIn("kept 'Smith2020'", log)

    def test_no_changes_is_identical_and_cleanup_is_idempotent(self):
        source = "% Original formatting\n" + article("A") + "\n\n"
        self.assertEqual(self.run_cli(source, "--duplicates"), "")
        self.assertEqual(self.output.read_text(), source)
        self.run_cli(article("A") + article("B") + article("A", title="Different"), "--duplicates")
        cleaned = self.output.read_text()
        self.assertEqual(self.run_cli(cleaned, "--duplicates"), "")
        self.assertEqual(self.output.read_text(), cleaned)

    def test_cleanup_aliases_and_combined_options_produce_identical_files(self):
        source = article("A") + article("B") + article("A", title="Different")
        outputs = []
        for flags in [("--duplicates",), ("--deduplicate",),
                      ("--duplicate-papers", "--duplicate-keys"),
                      ("--remove-duplicate-papers", "--rename-duplicate-keys")]:
            self.run_cli(source, *flags)
            outputs.append(self.output.read_text())
        self.assertTrue(all(output == outputs[0] for output in outputs))

    def test_cleanup_writes_default_output_and_overwrite_keeps_backup(self):
        source = article("A") + article("B")
        self.run_cli(source, "--duplicates", output=False)
        self.assertTrue((self.directory / "updated_papers.bib").is_file())
        self.assertEqual(self.input.read_text(), source)
        self.run_cli(source, "--duplicates", "--overwrite")
        self.assertEqual(self.input.with_suffix(".bib.bak").read_text(), source)
        self.assertNotIn("@article{B,", self.input.read_text())

    def test_unclosed_input_is_rejected_before_output_or_backup_is_touched(self):
        source = article("A") + '@article{B,\n title = {Unclosed}\n'
        self.output.write_text("Existing output")
        with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
            self.run_cli(source, "--duplicates", "--overwrite")
        self.assertIn("unclosed", errors.getvalue())
        self.assertEqual(self.input.read_text(), source)
        self.assertEqual(self.output.read_text(), "Existing output")
        self.assertEqual(len(list(self.directory.iterdir())), 2)

    def test_cleanup_rejects_listing_and_check_combinations(self):
        for flags in [("--duplicates", "--list-keys"), ("--duplicate-keys", "--check")]:
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                self.run_cli(article("A"), *flags)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
