"""CLI output and ordered concurrent lookup regressions, without network calls."""

import contextlib
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bibupdate as b
from test_mrlookup import ORIGINAL, PUBLISHED


class TerminalOutput(io.StringIO):
    def isatty(self):
        return True


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.input = Path(self.directory.name) / "papers.bib"
        self.output = Path(self.directory.name) / "updated.bib"
        self.input.write_text(ORIGINAL)

    def run_cli(self, *flags, get_json=None, terminal=False):
        output = TerminalOutput() if terminal else io.StringIO()
        response = {"all": {"results": [{"bibTexFormat": PUBLISHED}]}}
        with patch.object(b.sys, "argv", [
            "bibupdate", *flags, str(self.input), str(self.output)
        ]), contextlib.redirect_stdout(output), patch.object(
            b, "get_json", side_effect=get_json, return_value=response
        ):
            b.main()
        return output.getvalue()

    def test_default_reports_a_reference_instead_of_field_differences(self):
        log = self.run_cli()
        lines = log.splitlines()
        self.assertEqual(lines[0], "[AMRW:KoszulDuality]")
        self.assertTrue(all(line.startswith("  ") for line in lines[1:]))
        self.assertTrue(all(len(line) <= 78 for line in lines))
        reference = " ".join(log.split())
        self.assertIn("Pramod N. Achar", reference)
        self.assertIn("J. Amer. Math. Soc. 32(1) (2019), 261–310.", reference)
        self.assertNotIn("++++", log)
        self.assertNotIn("->", log)
        self.assertIn("mrnumber = {3868004}", self.output.read_text())
        self.assertEqual(self.input.read_text(), ORIGINAL)

    def test_no_flags_use_documented_defaults_and_write_beside_the_input(self):
        log = io.StringIO()
        response = {"all": {"results": [{"bibTexFormat": PUBLISHED}]}}
        with patch.object(b.sys, "argv", ["bibupdate", str(self.input)]), \
             contextlib.redirect_stdout(log), \
             patch.object(b, "get_json", return_value=response):
            b.main()
        for name in ["all", "arxiv", "check", "duplicates", "keep_fonts", "overwrite", "Help"]:
            self.assertFalse(getattr(b.options, name), name)
        self.assertEqual(b.options.lookup, "mrlookup")
        self.assertEqual(b.options.threads, 1)
        self.assertEqual(b.options.wrap, 0)
        self.assertEqual(b.options.quieter, 2)
        self.assertEqual(b.options.debugging, 0)
        self.assertEqual(b.options.ignored_fields, ["coden", "mrreviewer"])
        self.assertIs(b.options.log, log)
        self.assertIsNone(b.options.outputfile)
        self.assertTrue(self.input.with_name("updated_papers.bib").exists())
        self.assertEqual(self.input.read_text(), ORIGINAL)
        self.assertIn("[AMRW:KoszulDuality]", log.getvalue())

    def test_ignored_field_defaults_and_overrides_filter_the_written_bibliography(self):
        fields = ["coden", "mrreviewer", "fjournal", "issn"]
        extra = "".join("  %s = {Test value},\n" % field for field in fields)
        self.input.write_text(ORIGINAL.replace("\n}\n", "\n" + extra + "}\n"))
        published = PUBLISHED.replace("\n}\n", "\n" + extra + "}\n")
        response = {"all": {"results": [{"bibTexFormat": published}]}}
        cases = [
            ((), ["coden", "mrreviewer"]),
            (("-i", "FJOURNAL ISSN"), ["fjournal", "issn"]),
            (("-i", "coden", "-i", "fjournal"), ["coden", "fjournal"]),
            (("-i", ""), []),
            (("-i", "coden", "-i", "issn", "-i", "fjournal", "-i", "mrreviewer", "-i", "coden"),
             fields + ["coden"]),
        ]
        for flags, ignored in cases:
            with self.subTest(flags=flags):
                self.run_cli(*flags, get_json=lambda url: response)
                self.assertCountEqual(b.options.ignored_fields, ignored)
                written = self.output.read_text()
                for field in fields:
                    self.assertEqual(field + " = {Test value}" in written, field not in ignored)

    def test_quiet_aliases_keep_only_brief_update_notices(self):
        for flag in ["-q", "--quiet", "--quieter"]:
            with self.subTest(flag=flag):
                log = self.run_cli(flag)
                self.assertTrue(log.startswith("Updated AMRW:KoszulDuality:"))
                self.assertEqual(log.count("Updated AMRW:KoszulDuality:"), 1)
                self.assertTrue(all(len(line) <= 78 for line in log.splitlines()))
                self.assertNotIn("+", log)
                self.assertNotIn("->", log)
                self.assertNotIn("J. Amer. Math. Soc.", log)

    def test_repeated_quiet_suppresses_updates_but_still_writes_them(self):
        self.assertEqual(self.run_cli("-qq"), "")
        self.assertIn("year = {2019}", self.output.read_text())

    def test_unchanged_entries_are_not_reported(self):
        self.input.write_text(PUBLISHED)
        self.assertEqual(self.run_cli("--threads", "2", "--all"), "")

    def test_threaded_reports_can_be_sent_to_a_log_file(self):
        log_file = Path(self.directory.name) / "updates.log"
        try:
            self.assertEqual(self.run_cli(
                "--threads", "2", "--log", str(log_file)
            ), "")
        finally:
            b.options.log.close()
        self.assertIn("[AMRW:KoszulDuality]", log_file.read_text())

    def test_quiet_still_reports_network_warnings(self):
        def failed_lookup(url):
            b.bibup.warning("! unable to connect (test failure)")
            return {}
        self.assertIn("unable to connect", self.run_cli(
            "--threads", "2", "--quiet", get_json=failed_lookup
        ))
        self.assertEqual(self.run_cli("--threads", "2", "-qq", get_json=failed_lookup), "")

    def test_missing_papers_are_reported_in_normal_and_quiet_modes(self):
        for flags in [(), ("--quiet",), ("--quieter",)]:
            with self.subTest(flags=flags):
                log = self.run_cli("--threads", "2", *flags, get_json=lambda url: {})
                self.assertIn("! Didn't find AMRW:KoszulDuality:", log)
                self.assertIn("characters of tilting modules", " ".join(log.split()))
                self.assertTrue(all(len(line) <= 78 for line in log.splitlines()))
        self.assertEqual(self.run_cli("-qq", get_json=lambda url: {}), "")

    def test_missing_preprints_are_also_reported_in_quiet_mode(self):
        self.input.write_text(ORIGINAL.replace("{in press}", "{preprint}"))
        log = self.run_cli("--quiet", get_json=lambda url: {})
        self.assertIn("! Didn't find AMRW:KoszulDuality:", log)

    def test_missing_papers_are_red_only_on_terminal_outputs(self):
        for threads in ["1", "2"]:
            with self.subTest(threads=threads):
                red = self.run_cli(
                    "--threads", threads, "--quiet",
                    get_json=lambda url: {}, terminal=True,
                )
                plain = self.run_cli(
                    "--threads", threads, "--quiet", get_json=lambda url: {}
                )
                self.assertTrue(red.startswith("\033[31m! Didn't find "))
                self.assertTrue(red.endswith("\033[0m\n"))
                self.assertEqual(red.replace("\033[31m", "").replace("\033[0m", ""), plain)
                self.assertNotIn("\033[", plain)

    def test_missing_paper_logs_stay_plain_even_when_stdout_is_a_terminal(self):
        log_file = Path(self.directory.name) / "missing.log"
        try:
            self.assertEqual(self.run_cli(
                "--threads", "2", "--quiet", "--log", str(log_file),
                get_json=lambda url: {}, terminal=True,
            ), "")
        finally:
            b.options.log.close()
        log = log_file.read_text()
        self.assertIn("! Didn't find AMRW:KoszulDuality:", log)
        self.assertNotIn("\033[", log)

    def test_wrapping_preserves_mathematical_titles(self):
        self.input.write_text(ORIGINAL.replace(
            "Koszul duality for {Kac-Moody} groups and characters of tilting modules",
            "Book review: {The $q$--Schur algebra} and its representations",
        ))
        log = self.run_cli("--quiet", get_json=lambda url: {})
        self.assertIn("The $q$--Schur algebra", " ".join(log.split()))

    def test_threaded_output_and_file_match_sequential_run(self):
        self.input.write_text(
            "Bibliography preamble\n" + ORIGINAL + "\n" + ORIGINAL.replace(
                "AMRW:KoszulDuality", "SecondEntry"
            )
        )
        sequential_log = self.run_cli()
        sequential_file = self.output.read_text()
        self.assertEqual(self.run_cli("--threads", "2"), sequential_log)
        self.assertEqual(self.output.read_text(), sequential_file)
        self.assertLess(sequential_file.index("AMRW:KoszulDuality"),
                        sequential_file.index("SecondEntry"))

    def test_arxiv_runs_on_main_thread_and_combines_update_report(self):
        calls = []
        def arxiv_lookup(title):
            calls.append(threading.get_ident())
            return [("1706.00183", b.clean_title(title))]
        with patch.object(b, "arxiv_search", side_effect=arxiv_lookup):
            log = self.run_cli("--threads", "2", "--arxiv")
        self.assertEqual(calls, [threading.get_ident()])
        self.assertEqual(log.count("[AMRW:KoszulDuality]"), 1)
        self.assertIn("arXiv:1706.00183", log)
        self.assertIn("eprint = {1706.00183}", self.output.read_text())

    def test_check_reports_compact_proposed_reference_without_writing_file(self):
        log = self.run_cli("--threads", "2", "--check")
        self.assertIn("[AMRW:KoszulDuality]", log)
        self.assertIn("(2019), 261–310.", " ".join(log.split()))
        self.assertNotIn("->", log)
        self.assertNotIn("====", log)
        self.assertTrue(all(len(line) <= 78 for line in log.splitlines()))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.input.read_text(), ORIGINAL)
        self.assertEqual(self.run_cli("--check", "-q"), log)
        self.assertEqual(self.run_cli("--check", "-qq"), log)

    def test_arxiv_skips_existing_identifiers_unless_all_is_requested(self):
        for existing in ["", "1234.56789"]:
            self.input.write_text(ORIGINAL.replace(
                "\n}\n", "\n  eprint = {%s},\n  archiveprefix = {Other}\n}\n" % existing
            ))
            with self.subTest(existing=existing), patch.object(
                b, "arxiv_search", return_value=[(
                    "1706.00183", b.clean_title(b.Bibtex(ORIGINAL)["title"])
                )]
            ) as search:
                self.run_cli("--arxiv")
                search.assert_not_called()
                self.assertIn("eprint = {%s}" % existing, self.output.read_text())
                self.run_cli("--arxiv", "--all")
                search.assert_called_once()
                self.assertIn("eprint = {1706.00183}", self.output.read_text())
                self.assertIn("archiveprefix = {arXiv}", self.output.read_text())

    def test_check1_is_an_alias_for_compact_check_mode(self):
        self.assertEqual(self.run_cli("--check1"), self.run_cli("--check"))
        self.assertFalse(self.output.exists())

    def test_check_combines_ams_and_arxiv_proposals_in_one_reference(self):
        with patch.object(b, "arxiv_search", return_value=[(
            "1706.00183", b.clean_title(b.Bibtex(ORIGINAL)["title"])
        )]):
            log = self.run_cli("--check", "--arxiv", "--threads", "2")
        self.assertEqual(log.count("[AMRW:KoszulDuality]"), 1)
        self.assertIn("arXiv:1706.00183", log)
        self.assertNotIn("->", log)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.input.read_text(), ORIGINAL)

    def test_check_duplicate_warnings_precede_queries_and_work_in_quiet_mode(self):
        self.input.write_text(ORIGINAL + "\n" + ORIGINAL.replace(
            "AMRW:KoszulDuality", "AnotherKey"
        ))
        original = self.input.read_text()
        def failed_lookup(url):
            self.assertIn("Duplicate paper", b.options.log.getvalue())
            return {}
        log = self.run_cli("--check", "--quiet", "--threads", "2", get_json=failed_lookup)
        self.assertIn("Duplicate paper", log)
        self.assertIn("AnotherKey", log)
        self.assertIn("line 1", log)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.input.read_text(), original)

    def test_duplicate_warnings_are_specific_to_check_mode(self):
        self.input.write_text(ORIGINAL + "\n" + ORIGINAL)
        self.assertNotIn("Duplicate", self.run_cli("--threads", "2"))
        self.assertIn("Duplicate citation key", self.run_cli("--check"))

    def test_duplicates_only_is_offline_and_preserves_all_bibliography_files(self):
        duplicate = ORIGINAL + "\n" + ORIGINAL.replace("AMRW:KoszulDuality", "AnotherKey")
        self.input.write_text(duplicate)
        self.output.write_text("Existing output\n")
        for flag in ["-d", "--list-duplicates"]:
            with self.subTest(flag=flag), patch.object(
                b, "lookup_entries", side_effect=AssertionError("AMS lookup attempted")
            ), patch.object(b, "arxiv_search", side_effect=AssertionError("arXiv lookup attempted")):
                log = self.run_cli(flag, "--check", "--arxiv", "--overwrite", "--threads", "2")
                self.assertIn("Duplicate paper", log)
                self.assertIn("AnotherKey", log)
                self.assertIn("line 1", log)
                self.assertEqual(b.options.debugging, 0)
                self.assertEqual(self.input.read_text(), duplicate)
                self.assertEqual(self.output.read_text(), "Existing output\n")
                self.assertEqual(sorted(p.name for p in self.input.parent.iterdir()),
                                 ["papers.bib", "updated.bib"])

    def test_duplicates_only_without_output_path_never_creates_a_file(self):
        self.input.write_text(ORIGINAL + "\n" + ORIGINAL)
        log = io.StringIO()
        with patch.object(b.sys, "argv", ["bibupdate", "-d", str(self.input)]), \
             contextlib.redirect_stdout(log), \
             patch.object(b, "get_json", side_effect=AssertionError("Network lookup attempted")):
            b.main()
        self.assertIn("Duplicate citation key", log.getvalue())
        self.assertEqual(list(self.input.parent.iterdir()), [self.input])

    def test_duplicates_only_has_purple_quiet_warnings_and_silent_unique_input(self):
        self.assertEqual(self.run_cli("-d"), "")
        self.assertFalse(self.output.exists())
        self.input.write_text(ORIGINAL + "\n" + ORIGINAL)
        plain = self.run_cli("-d")
        self.assertEqual(self.run_cli("-d", "--quiet"), plain)
        self.assertNotIn("\033[", plain)
        self.assertIn("\033[35m! Duplicate citation key", self.run_cli("-d", terminal=True))
        self.assertEqual(self.run_cli("-d", "-qq"), "")

    def test_hidden_debugging_option_remains_available_by_its_long_name(self):
        with patch.object(b.bibup, "debugging", False):
            self.run_cli("--debugging", "-d")
            self.assertEqual(b.options.debugging, 1)
            self.assertTrue(b.options.list_duplicates)

    def test_threads_work_with_mathscinet_and_mref(self):
        def response(url):
            if "/freetools/mref?" in url:
                return {"paperId": 3868004}
            return {"bibTexFormat": PUBLISHED}
        self.input.write_text(PUBLISHED.replace("{2019}", "{2018}"))
        for mode in ["--mathscinet", "--mref"]:
            with self.subTest(mode=mode):
                log = self.run_cli("--threads", "2", "--all", mode, get_json=response)
                self.assertIn("[MR3868004]", log)
                self.assertIn("year = {2019}", self.output.read_text())

    def test_invalid_thread_counts_are_rejected(self):
        for count in ["0", "-1", "two", "1.5"]:
            with self.subTest(count=count), patch.object(b.sys, "argv", [
                "bibupdate", "--threads", count
            ]), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as raised:
                    b.process_options()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("positive integer", errors.getvalue())


class ThreadingTests(unittest.TestCase):
    def test_lookups_overlap_but_logs_and_results_remain_in_input_order(self):
        main_thread = threading.get_ident()
        first_started = threading.Event()
        second_started = threading.Event()
        lock = threading.Lock()
        counts = {"active": 0, "maximum": 0}
        worker_threads = []

        class MainThreadLog(io.StringIO):
            def write(self, text):
                if threading.get_ident() != main_thread:
                    raise AssertionError("worker wrote directly to the output log")
                return super().write(text)

        class Entry:
            def __init__(self, key):
                self.key = key

            def has_valid_pub_type(self):
                return True

            def mrlookup(self):
                with lock:
                    counts["active"] += 1
                    counts["maximum"] = max(counts["maximum"], counts["active"])
                    worker_threads.append(threading.get_ident())
                try:
                    b.bib_print("start " + self.key)
                    if self.key == "first":
                        first_started.set()
                        if not second_started.wait(3):
                            raise AssertionError("second lookup did not overlap the first")
                    elif self.key == "second":
                        if not first_started.wait(3):
                            raise AssertionError("first lookup did not overlap the second")
                        second_started.set()
                    b.bib_print("end " + self.key)
                finally:
                    with lock:
                        counts["active"] -= 1

        entries = [Entry(key) for key in ["first", "second", "third", "fourth"]]
        log = MainThreadLog()
        with patch.object(b, "options", SimpleNamespace(
            threads=2, lookup="mrlookup", log=log
        ), create=True):
            results = list(b.lookup_entries(iter(entries)))
        self.assertEqual(results, entries)
        self.assertEqual(counts["maximum"], 2)
        self.assertNotIn(main_thread, worker_threads)
        self.assertEqual(log.getvalue().splitlines(), [
            message + " " + entry.key
            for entry in entries for message in ["start", "end"]
        ])


if __name__ == "__main__":
    unittest.main()
