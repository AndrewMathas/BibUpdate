"""Regression tests for multi-author mrlookup searches (no network required)."""

import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import bibupdate as b


ORIGINAL = """@ARTICLE{AMRW:KoszulDuality,
  author = {Achar, Pramod N. and Makisumi, Shotaro and Riche, Simon and Williamson, Geordie},
  title = {Koszul duality for {Kac-Moody} groups and characters of tilting modules},
  journal = {J. Amer. Math. Soc.},
  year = {2018},
  pages = {in press},
  doi = {10.1090/jams/905}
}
"""

# The bibliographic fields returned by AMS for this paper, including its
# different capitalization braces and publication year.
PUBLISHED = """@article {MR3868004,
  AUTHOR = {Achar, Pramod N. and Makisumi, Shotaro and Riche, Simon and Williamson, Geordie},
  TITLE = {Koszul duality for {K}ac-{M}oody groups and characters of tilting modules},
  JOURNAL = {J. Amer. Math. Soc.},
  VOLUME = {32},
  YEAR = {2019},
  NUMBER = {1},
  PAGES = {261--310},
  MRNUMBER = {3868004},
  DOI = {10.1090/jams/905},
}
"""


class MrlookupTests(unittest.TestCase):
    def setUp(self):
        self.options = SimpleNamespace(
            all=False, check=False, ignored_fields=[], log=io.StringIO()
        )
        self.patchers = [
            patch.object(b, "options", self.options, create=True),
            patch.object(b.bibup, "fix_fonts", b.replace_fonts, create=True),
            patch.object(b.bibup, "debug", lambda *args: None, create=True),
            patch.object(b.bibup, "verbose", lambda *args: None, create=True),
            patch.object(b.bibup, "warning", lambda *args: None, create=True),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def lookup(self, entry, results):
        with patch.object(b, "get_json", return_value={
            "all": {"results": [{"bibTexFormat": result} for result in results]},
            "dml": {"results": []},
        }) as get_json:
            entry.mrlookup()
        return parse_qs(urlparse(get_json.call_args[0][0]).query,
                        keep_blank_values=True)

    def test_multi_author_paper_updates_despite_different_title_braces(self):
        entry = b.Bibtex(ORIGINAL)
        entry["eprint"] = "1706.00183"
        query = self.lookup(entry, [PUBLISHED])
        self.assertEqual(query["author"], ["Achar"])
        self.assertEqual(query["title"], [
            "Koszul duality for Kac-Moody groups and characters of tilting modules"
        ])
        self.assertEqual(query["year"], [""])
        self.assertEqual(entry.cite_key, "AMRW:KoszulDuality")
        self.assertEqual(entry["mrnumber"], "3868004")
        self.assertEqual(entry["year"], "2019")
        self.assertEqual(entry["volume"], "32")
        self.assertEqual(entry["pages"], "261--310")
        self.assertEqual(entry["eprint"], "1706.00183")
        self.assertIn("{K}ac-{M}oody", entry["title"])

    def test_name_formats_use_first_surname(self):
        for authors, surname in [
            ("Achar, Pramod N.", "Achar"),
            ("Pramod N. Achar and Simon Riche", "Achar"),
            ("van Beethoven, Ludwig and Riche, Simon", "van Beethoven"),
            ("Pramod~N.~Achar and Simon Riche", "Achar"),
            ("Achar, Pramod N. & Riche, Simon", "Achar"),
            (r'K{\"o}nig, Steffen and Riche, Simon', r'K{\"o}nig'),
        ]:
            with self.subTest(authors=authors):
                entry = b.Bibtex(ORIGINAL)
                entry["author"] = authors
                query = self.lookup(entry, [])
                self.assertEqual(query["author"], [surname])

    def test_title_only_search_remains_available(self):
        entry = b.Bibtex(ORIGINAL)
        del entry["author"]
        query = self.lookup(entry, [PUBLISHED])
        self.assertEqual(query["author"], [""])
        self.assertEqual(entry["mrnumber"], "3868004")

    def test_published_page_and_year_search_is_preserved(self):
        entry = b.Bibtex(ORIGINAL)
        entry["year"] = "2019"
        entry["pages"] = "261--310"
        query = self.lookup(entry, [PUBLISHED])
        self.assertEqual(query["firstPage"], ["261"])
        self.assertEqual(query["lastPage"], ["310"])
        self.assertEqual(query["year"], ["2019"])
        self.assertEqual(query["title"], [""])
        self.assertEqual(entry["mrnumber"], "3868004")

    def test_single_pages_article_ids_and_book_years_use_documented_queries(self):
        for pub_type, pages, expected in [
            ("article", "261", ("261", "", "2019", "")),
            ("article", "Art. ID rnm032, 24", ("", "", "", b.clean_title(b.Bibtex(ORIGINAL)["title"]))),
            ("book", "", ("", "", "2019", b.clean_title(b.Bibtex(ORIGINAL)["title"]))),
        ]:
            with self.subTest(pub_type=pub_type, pages=pages):
                entry = b.Bibtex(ORIGINAL)
                entry.pub_type = pub_type
                entry["pages"] = pages
                entry["year"] = "2019"
                query = self.lookup(entry, [])
                self.assertEqual(tuple(query[key][0] for key in
                                       ["firstPage", "lastPage", "year", "title"]), expected)

    def test_apostrophes_are_removed_from_search_titles(self):
        entry = b.Bibtex(ORIGINAL)
        entry["title"] = "James's Conjecture"
        query = self.lookup(entry, [])
        self.assertEqual(query["title"], ["Jamess Conjecture"])

    def test_mathscinet_requires_digits_even_when_all_is_requested(self):
        for all_entries in [False, True]:
            for mrnumber in [None, "", "unknown"]:
                with self.subTest(all=all_entries, mrnumber=mrnumber):
                    self.options.all = all_entries
                    entry = b.Bibtex(ORIGINAL)
                    if mrnumber is not None:
                        entry["mrnumber"] = mrnumber
                    with patch.object(b, "get_json") as get_json:
                        entry.mathscinet()
                        get_json.assert_not_called()

    def test_unrelated_title_does_not_update(self):
        entry = b.Bibtex(ORIGINAL)
        before = dict(entry)
        unrelated = PUBLISHED.replace(
            "Koszul duality for {K}ac-{M}oody groups and characters of tilting modules",
            "An unrelated paper by the same author",
        )
        self.lookup(entry, [unrelated])
        self.assertEqual(dict(entry), before)

    def test_ambiguous_title_does_not_update(self):
        entry = b.Bibtex(ORIGINAL)
        before = dict(entry)
        other = PUBLISHED.replace("MR3868004", "MR9999999").replace(
            "{3868004}", "{9999999}"
        )
        self.lookup(entry, [PUBLISHED, other])
        self.assertEqual(dict(entry), before)

    def test_check_mode_reports_without_mutating(self):
        self.options.check = True
        entry = b.Bibtex(ORIGINAL)
        before = dict(entry)
        self.lookup(entry, [PUBLISHED])
        entry.report_update()
        self.assertEqual(dict(entry), before)
        self.assertIn("261–310", self.options.log.getvalue())
        self.assertNotIn("->", self.options.log.getvalue())

    def test_existing_mrnumber_is_skipped_unless_all_requested(self):
        entry = b.Bibtex(ORIGINAL)
        entry["mrnumber"] = "3868004"
        with patch.object(b, "get_json") as get_json:
            entry.mrlookup()
            get_json.assert_not_called()
        self.options.all = True
        self.lookup(entry, [PUBLISHED])
        self.assertEqual(entry["year"], "2019")

    def test_empty_braced_fields_are_retained_and_empty_mrnumber_skips_lookup(self):
        source = ORIGINAL.replace("\n}\n", "\n  mrnumber = {},\n  eprint = {}\n}\n")
        entry = b.Bibtex(source)
        for field in ["mrnumber", "eprint"]:
            self.assertIn(field, entry)
            self.assertEqual(entry[field], "")
        with patch.object(b, "get_json") as get_json:
            entry.mrlookup()
            get_json.assert_not_called()
        self.options.all = True
        self.lookup(entry, [PUBLISHED])
        self.assertEqual(entry["mrnumber"], "3868004")

    def test_new_fields_are_added_even_when_existing_fields_are_identical(self):
        entry = b.Bibtex(PUBLISHED)
        del entry["mrnumber"]
        self.lookup(entry, [PUBLISHED])
        self.assertEqual(entry["mrnumber"], "3868004")
        self.assertTrue(entry.updated)


if __name__ == "__main__":
    unittest.main()
