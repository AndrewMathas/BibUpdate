#!/usr/bin/env python3

r"""
===============================================
bibupdate - update the entries of a bibtex file
===============================================

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.

See https://github.com/AndrewAtLarge/BibUpdate for more details
about the bibupdate program.

Andrew Mathas andrew.mathas@gmail.com
Copyright (C) 2012-2021
"""

######################################################
import bdb
import difflib
import itertools
import json
import os
import re
import shutil
import sys
import textwrap
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

##########################################################
# Define bibupdate's meta data. This is used for global variables, to generate
# the doc-string and README files and in setup.py.

# bibupdate.ini is the single source of truth for the metadata below (it is what
# the release/bump script edits). The embedded default_metadata is only used as
# a fallback when bibupdate.ini cannot be found, so that bibupdate.py can run
# as a self-contained script.
default_metadata = {
    "author": "Andrew Mathas",
    "author_email": "andrew.mathas@gmail.com",
    "copyright": "2014-2021",  # @bump@
    "description": "Automatically update the entries of a bibtex file",
    "keywords": "bibtex, mrlookup, MathSciNet, latex",
    "license": "GNU General Public License, Version 3, 29 June 2007",
    "name": "Andrew Mathas",
    "program": "bibupdate",
    "release date": "30 April 2021 AEST",  # @bump@
    "repository": "https://github.com/AndrewAtLarge/BibUpdate",
    "url": "https://github.com/AndrewAtLarge/BibUpdate",
    "version": "1.4.0-r0",  # @bump@
}


class Settings(dict):
    r"""
    A class for holding bibupdate's metadata and global variables. The metadata
    is read from `ini_file` (bibupdate.ini), falling back to `default_metadata`
    if the file cannot be found. Each metadata item is available both as a
    dictionary entry, `bibup[key]`, and as an attribute, `bibup.key`.
    """

    def __init__(self, ini_file):
        super().__init__()
        metadata = dict(default_metadata)
        try:
            with open(ini_file) as ini:
                for line in ini:
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip().lower()
                        if key != "":
                            metadata[key] = val.strip()
        except IOError:
            pass  # fall back to the embedded default_metadata
        for key, val in metadata.items():
            self[key] = val  # available as bibup[key]
            setattr(self, key, val)  # available as bibup.key


# Launchers may be symlinks in a bin directory; metadata belongs to the script.
ini_file_path = lambda f: os.path.join(os.path.dirname(os.path.realpath(__file__)), f)
bibup = Settings(ini_file_path("bibupdate.ini"))
bibup.debugging = False  # debugging off by default


##########################################################
# We trap system errors so that we can exit cleanly when the program is
# killed and so that we can automatically tap into a debugger when debugging.
def CleanExceptHook(type, value, tb):
    r"""
    Exit cleanly when program is killed, or jump into pdb if debugging
    """
    if type == KeyboardInterrupt:
        bib_error("program killed. Exiting...")
    elif (
        not bibup.debugging
        or hasattr(sys, "ps1")
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
        or not sys.stderr.isatty()
        or issubclass(type, bdb.BdbQuit)
        or issubclass(type, SyntaxError)
    ):
        sys.__excepthook__(type, value, tb)
    else:
        import pdb
        import traceback

        # we are NOT in interactive mode, print the exception...
        traceback.print_exception(type, value, tb)
        print()
        # ...then start the debugger in post-mortem mode.
        pdb.pm()


# ...and a hook to grab the exception
sys.excepthook = CleanExceptHook

bibup.bibupdate_version = (
    "%(prog)s, version {0.version}: {0.description}\n{0.license}".format(bibup)
)

##########################################################
# Everything that bibupdate uses is in the python (3) standard library.
import argparse
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor

# We use fuzzy matching to decide whether two titles are "the same". This is
# done using difflib.SequenceMatcher.ratio(), which returns a number between 0 and 1
# measuring how similar the two strings are. We treat titles that are more than
# 90% similar as a match.
good_match_ratio = 0.9
DEFAULT_IGNORED_FIELDS = ("coden", "mrreviewer")


def good_match(one, two):
    r"""
    Return True or False depending on whether or not the lower cased strings
    `one` and `two` are a good (fuzzy) match for each other.
    """
    return (
        difflib.SequenceMatcher(None, one.lower(), two.lower()).ratio()
        > good_match_ratio
    )


lookup_log = threading.local()


def display_text(value):
    """Remove capitalization braces and normalize spacing for terminal output."""
    value = value.replace("{", "").replace("}", "").replace("~", " ")
    return " ".join(value.split())


def wrap_report(text, indent=""):
    """Keep report lines within 78 columns, with indented continuation lines."""
    return textwrap.fill(
        text,
        width=78,
        initial_indent=indent,
        subsequent_indent="  ",
        break_on_hyphens=False,
    )


def bib_print(*args):
    r"""
    Default printing mechanism. Defaults to sys.stdout but can be overridden
    by the command line options.
    """
    messages = getattr(lookup_log, "messages", None)
    if messages is not None:
        messages.extend(args)
    else:
        for a in args:
            # Color at the final write, after worker messages are replayed,
            # so redirected output and log files stay free of escape codes.
            if options.log.isatty():
                if a.startswith("! Didn't find "):
                    a = "\033[31m" + a + "\033[0m"
                elif a.startswith(("! Duplicate ", "! Possible duplicate ")):
                    a = "\033[35m" + a + "\033[0m"
            options.log.write(a + "\n")


def bib_error(*args):
    r"""
    Print errors to sys.stderr and also to the log file if it is not sys.stdout.
    Then exit with an error code of 2.
    """
    if options.log != sys.stdout:
        bib_print("%s error:" % options.prog)
        for a in args:
            bib_print(a)

    sys.stderr.write("%s error: " % options.prog)
    for a in args:
        sys.stderr.write(a + "\n")
    sys.exit(2)


##########################################################
# The AMS provides its "free tools" (mrlookup, mref and lookup by MR number) as
# JSON web APIs served from mathscinet.ams.org. These endpoints sit behind
# Cloudflare, which returns a 403 to the default Python-urllib User-Agent, so we
# present our own User-Agent string when calling them.
ams_api = "https://mathscinet.ams.org/mathscinet/api"
user_agent = "bibupdate (https://github.com/AndrewAtLarge/BibUpdate)"
url_timeout = 30  # seconds to wait for a response before giving up on a request


def get_url(url):
    r"""
    Fetch `url`, sending a User-Agent (the default Python-urllib one is blocked
    by the AMS), and return the decoded response body as a string. Returns None,
    after printing a warning, if the request fails or times out. A single failed
    lookup should not abort a run over a whole bibliography.
    """
    try:
        bibup.debug("S %s" % url)
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        lookup = urllib.request.urlopen(request, timeout=url_timeout)
        page = lookup.read().decode("utf-8", "replace")
        lookup.close()  # close the url feed
        return page
    except (IOError, OSError) as err:
        bibup.warning("! unable to connect to %s (%s)" % (url, err))
        return None


def get_json(url):
    r"""
    Fetch `url` and return the response parsed as JSON (a dict). Returns an empty
    dict if the request fails or the response is not valid JSON.
    """
    page = get_url(url)
    if page is None:
        return {}
    try:
        return json.loads(page)
    except ValueError:
        bibup.debug("MR non-JSON response")
        return {}


def article_bibtex(paper_id):
    r"""
    Return the bibtex string for the AMS article with the given `paper_id` (its
    MR number), or None if it cannot be retrieved.
    """
    data = get_json("%s/public/articles-with-formats/%s" % (ams_api, paper_id))
    return data.get("bibTexFormat")


def mrlookup_bibtex(data):
    r"""
    Extract the list of bibtex strings from an mrlookup JSON response. The
    response has the form {"all": {"results": [...]}, "dml": {"results": [...]}}
    where each result has a `bibTexFormat` field.
    """
    bibtex = []
    for section in ("all", "dml"):
        for result in data.get(section, {}).get("results", []):
            if result.get("bibTexFormat"):
                bibtex.append(result["bibTexFormat"])
    return bibtex


##########################################################
# The arXiv provides a public API that returns an Atom (XML) feed. We search on
# the significant words in the title and then fuzzily match the titles of the
# papers that are returned. The arXiv asks that successive API calls be spaced
# out by at least 3 seconds, so we throttle our requests accordingly.
arxiv_api = "https://export.arxiv.org/api/query"
arxiv_atom = {"atom": "http://www.w3.org/2005/Atom"}
arxiv_stopwords = set(
    "a an and are as at by for from in is of on or the to via with".split()
)
arxiv_delay = 3  # seconds requested by the arXiv between API calls
arxiv_last_call = [
    0.0
]  # time of the last arXiv API call (in a list so we can mutate it)


def arxiv_search(title):
    r"""
    Search the arXiv for `title` and return a list of `(arxiv_id, title)` pairs
    for the papers that are returned. The arxiv_id has any version suffix (such
    as v2) removed, so that it has the form 2211.07069 or math/0201001.
    """
    # search on the "significant" words in the title
    words = [
        w
        for w in re.findall(r"[A-Za-z]{3,}", clean_title(title))
        if w.lower() not in arxiv_stopwords
    ]
    if words == []:
        return []
    query = " AND ".join("ti:%s" % w for w in words)
    url = "%s?%s" % (
        arxiv_api,
        urllib.parse.urlencode({"search_query": query, "max_results": 10}),
    )

    # throttle: the arXiv asks for a delay between successive API calls
    wait = arxiv_delay - (time.time() - arxiv_last_call[0])
    if wait > 0:
        time.sleep(wait)
    page = get_url(url)
    arxiv_last_call[0] = time.time()
    if page is None:
        return []  # the request failed; get_url has already warned

    try:
        feed = ET.fromstring(page)
    except ET.ParseError:
        bibup.debug("arXiv: invalid XML response")
        return []

    results = []
    for entry in feed.findall("atom:entry", arxiv_atom):
        aid = entry.findtext("atom:id", "", arxiv_atom).strip()
        aid = re.sub(r"^https?://arxiv\.org/abs/", "", aid)  # strip the url prefix
        aid = re.sub(r"v\d+$", "", aid)  # strip the version suffix
        results.append((aid, entry.findtext("atom:title", "", arxiv_atom)))
    return results


##########################################################

# define a regular expression for extracting papers from BibTeX file
bibtex_entry = re.compile(r"(@\s*[A-Za-z]*\s*{[^@]*})\s*", re.DOTALL)

# regular expression for cleaning TeX from title etc
remove_tex = re.compile(r'[{}\'"_$]+')
remove_mathematics = re.compile(r"\$[^\$]+\$")  # assume no nesting
# savagely remove all maths from title
clean_title = lambda title: remove_tex.sub("", remove_mathematics.sub("", title))

# to help in checking syntax define recognised/valid types of entries in a bibtex database
bibtex_pub_types = [
    "article",
    "book",
    "booklet",
    "conference",
    "inbook",
    "incollection",
    "inproceedings",
    "manual",
    "mastersthesis",
    "misc",
    "phdthesis",
    "proceedings",
    "techreport",
    "unpublished",
]

# need to massage some of the font specifications returned by mrlookup to "standard" latex fonts.
fonts_to_replace = {"Bbb": "mathbb", "scr": "mathcal", "germ": "mathfrak"}
# a factory regular expression to replace expressions like \scr C and \scr{ Cat} in one hit
font_replacer = re.compile(
    r"\\(%s)\s*(?:(\w)|\{([\s\w]*)\})"
    % "|".join("%s" % f for f in fonts_to_replace.keys())
)


def replace_fonts(string):
    r"""
    Return a new version of `string` with various fonts commands replaced with
    their more standard LaTeX variants.

    Currently::

        - \Bbb X   and \BBb {X*}  --> \mathbb{X*}
        - \scr X*  and \scr {X*}  --> \mathcal{X*}
        - \germ X* and \germ{X*}  --> \mathfrak{X*}
    """
    return font_replacer.sub(
        lambda match: (
            r"\%s{%s}"
            % (fonts_to_replace[match.group(1)], match.group(2) or match.group(3))
        ),
        string,
    )


# overkill for "type checking" of the wrap length command line option
class NonnegativeIntegers(list):
    r"""
    A class that gives an easy test for positive integers::

        >>> 1 in NonnegativeIntegers()
        True
        >>> -1 in NonnegativeIntegers()
        False
    """

    def __str__(self):
        return "positive integers"

    def __contains__(self, x):
        r"""
        By implementing containment we can test if `x` is in `NonnegativeIntegers()`.
        """
        return isinstance(x, int) and x >= 0


class Bibtex(OrderedDict):
    r"""
    The bibtex class holds all of the data for a bibtex entry for a manuscript.
    It is called with a string <bib_string> that contains a bibtex entry and it
    returns a dictionary with whistles for the bibtex entry together with some
    methods for updating and printing the entry. As the bibtex file is a flat
    text file, to extract the data from it we use a collection of regular
    expressions which pull out the data key by key.

    The class contains mrlookup() and mathscinet() methods that attempt to
    update the bibtex entry by searching on the corresponding AMS databases for
    the paper. If successful this overwrite all of the existing bibtex fields,
    except for the cite key. Any entries not in MathSciNet are retained.

    Most of he hard work is done by a cunning use of regular expressions to
    extract the data from the bibtex file and from mrlookup and
    mathscient...perhaps we should be using pyquery or beautiful soup for the
    latter, but these regular expressions are certainly effective.
    """

    # A regular expression to extract a bibtex entry from a string. We assume that
    # the pub_type is a word in [A-Za-z]* and allow the citation key and the
    # contents of the bibtex entry to be fairly arbitrary and of the form: {cite_key, *}.
    parse_bibtex_entry = re.compile(
        r"@(?P<pub_type>[A-Za-z]*)\s*\{\s*(?P<cite_key>\S*)\s*,\s*?(?P<keys_and_vals>.*\})[,\s]*\}",
        re.MULTILINE | re.DOTALL,
    )

    # To extract the keys and values we need to remove all spaces around equals
    # signs because otherwise we are unable to cope with = inside a bibtex field.
    keys_and_vals = re.compile(r"\s*=\s*")

    # A regular expression to extract pairs of keys and values from a bibtex
    # string. The syntax here is very lax: we assume that bibtex fields do not
    # contain the string '={'.  From the AMS the format of a bibtex entry is
    # much more rigid but we cannot assume that an arbitrary bibtex file will
    # respect the AMS conventions.  There is a small complication in that we
    # allow the value of each field to either be enclosed in braces or to be a
    # single word.
    bibtex_keys = re.compile(
        r"([A-Za-z]+)=(?:\{((?:[^=]|=(?!\{))*)\}|(\w+)),?\s*$", re.MULTILINE | re.DOTALL
    )

    # A regular expression for extracting page numbers: <first page>-*<last page>
    # or simply <page>.
    page_nums = re.compile("(?P<apage>[0-9]*)-+(?P<zpage>[0-9]*)")

    def __init__(self, bib_string):
        """
        Given a string <bib_string> that contains a bibtex entry return the corresponding Bibtex class.

        EXAMPLE:
        """
        super(Bibtex, self).__init__()  # initialise as an OrderedDict
        self.updated = False
        self.checked_fields = {}
        entry = self.parse_bibtex_entry.search(bib_string)
        if entry is None:
            self.cite_key = None
            self.bib_string = bib_string
        else:
            self.pub_type = entry.group("pub_type").strip().lower()
            self.cite_key = entry.group("cite_key").strip()
            keys_and_vals = self.keys_and_vals.sub(
                "=", entry.group("keys_and_vals")
            )  # remove spaces around =
            for key, val, word in self.bibtex_keys.findall(keys_and_vals):
                if val == "":
                    val = word  # val matches {value} whereas word matches word
                else:
                    val = " ".join(val.split())  # remove any internal space from val
                lkey = key.lower()  # keys always in lower case
                if lkey == "title":
                    self[lkey] = bibup.fix_fonts(
                        val
                    )  # only fix fonts in the title, others assumed OK
                else:
                    self[lkey] = val

            # guess whether this entry corresponds to a preprint
            self.is_preprint = (
                "pages" in self
                and self["pages"].lower() in ["preprint", "to appear", "in preparation"]
            ) or not ("pages" in self and "journal" in self)

    def __str__(self):
        r"""
        Return a string for printing the bibtex entry.
        """
        if hasattr(self, "pub_type"):
            return "@%s{%s,\n  %s\n}" % (
                self.pub_type.upper(),
                self.cite_key,
                ",\n  ".join(
                    "%s = {%s}" % (key, bibup.wrapped(self[key]))
                    for key in self.keys()
                    if key not in options.ignored_fields
                ),
            )
        else:
            return self.bib_string

    def __getitem__(self, key, default=""):
        """
        We override `__getitem__` so that `self[key]` returns `default` if the `self[key]`
        does not exist.
        """
        try:
            return OrderedDict.__getitem__(self, key)
        except KeyError:
            return default

    def has_valid_pub_type(self):
        r"""
        Return True if the entry has a valid pub_type, as determined by
        bibtex_pub_types, and False otherwise.
        """
        return hasattr(self, "pub_type") and (self.pub_type in bibtex_pub_types)

    def bibliography(self):
        """Return the key and a reference wrapped into an indented 78-column block."""

        def field(name):
            return self.checked_fields.get(name, self[name])

        authors = []
        for author in re.split(r"\s+and\s+", field("author")):
            parts = author.split(",")
            if len(parts) == 2:
                author = "%s %s" % (parts[1].strip(), parts[0].strip())
            if author.strip():
                authors.append(display_text(author))
        if len(authors) > 1:
            author_text = ", ".join(authors[:-1]) + " and " + authors[-1]
        else:
            author_text = "".join(authors)

        publication = display_text(field("journal") or field("booktitle"))
        if field("volume"):
            publication += " " + display_text(field("volume"))
        if field("number"):
            publication += "(%s)" % display_text(field("number"))
        if field("year"):
            publication += " (%s)" % display_text(field("year"))
        if field("pages"):
            publication += ", " + display_text(field("pages")).replace("--", "–")
        if field("publisher"):
            publication += (", " if publication.strip() else "") + display_text(
                field("publisher")
            )

        parts = [author_text, display_text(field("title")), publication.strip(" ,")]
        if field("eprint"):
            parts.append("%s:%s" % (field("archiveprefix") or "arXiv", field("eprint")))
        reference = " ".join(part.rstrip(".") + "." for part in parts if part)
        return (
            wrap_report("[%s]" % self.cite_key)
            + "\n"
            + wrap_report(reference, indent="  ")
        )

    def report_update(self):
        """Report once, after both AMS and optional arXiv updates have finished."""
        if options.check:
            if self.checked_fields:
                bib_print(self.bibliography())
            return
        if self.updated:
            if options.quieter == 2:
                bibup.verbose(self.bibliography())
            else:
                bibup.warning(
                    wrap_report(
                        "Updated %s: %s" % (self.cite_key, display_text(self["title"]))
                    )
                )

    def update_entry(self, bibtex_matches):
        """
        Given `bibtex_matches`, a list of candidate bibtex entries (as strings)
        returned by one of the AMS lookup tools, update `self` if there is a
        unique good match. If there is a good match then we overwrite all of our
        fields with those from the match (and keep any other fields).
        """
        # attempt to match self with the bibtex entries returned by the AMS
        matches = [Bibtex(b) for b in bibtex_matches if b is not None]
        matches = [
            mr for mr in matches if mr.cite_key is not None and mr.has_valid_pub_type()
        ]
        bibup.debug("MR number of matches=%d" % len(matches))
        clean_ti = clean_title(self["title"])
        bibup.debug(
            "MR ti=%s.%s"
            % (
                clean_ti,
                "".join("\nMR -->%s." % clean_title(mr["title"]) for mr in matches),
            )
        )
        if clean_ti != "":
            matches = [
                mr for mr in matches if good_match(clean_ti, clean_title(mr["title"]))
            ]
        bibup.debug("MR number of clean matches=%d" % len(matches))

        if len(matches) == 1:
            match = matches[0]
            differences = [
                key
                for key in match
                if key not in options.ignored_fields and self[key] != match[key]
            ]
            if differences:
                if options.check:
                    self.checked_fields.update((key, match[key]) for key in differences)
                else:
                    for key in differences:
                        self[key] = match[key]
                    self.updated = True
            else:
                bibup.debug("No change")

        else:  # no good match, or more than one good match
            bibup.warning(
                wrap_report(
                    "! Didn't find %s: %s"
                    % (self.cite_key, display_text(self["title"]) or "???")
                )
            )

    def mrlookup(self):
        """
        Use mrlookup to search for a more up-to-date version of this entry. To
        search with mrlookup we look for papers published by the first author in
        the given year with the right page numbers. Most of the work here is in
        finding the correct search parameters.
        """
        # only check mrlookup for books or articles for which we don't already have an mrnumber field
        if not options.all and (
            "mrnumber" in self
            or self.pub_type not in ["book", "article", "inproceedings", "incollection"]
        ):
            return

        bibup.debug("=" * 30)
        # the mrlookup free tool accepts author, title, journal, firstPage,
        # lastPage and year fields
        search = {
            "author": "",
            "title": "",
            "journal": "",
            "firstPage": "",
            "lastPage": "",
            "year": "",
        }

        if "pages" in self and not self.is_preprint:
            pages = self.page_nums.search(self["pages"])
            if not pages is None:
                search["firstPage"] = pages.group("apage")  # first page
                search["lastPage"] = pages.group("zpage")  # last page
            elif self["pages"].isdigit():
                search["firstPage"] = self["pages"]  # first page

        # the year is reliable only if we also have page numbers
        if "year" in self and (self.pub_type == "book" or search["firstPage"] != ""):
            search["year"] = self["year"]

        # mrlookup's author field searches for one author. Joining surnames
        # with "and" gives no results for multi-author papers. Search using
        # only the first surname; update_entry still requires a unique title
        # match before accepting any result.
        if self["author"].strip():
            first_author = re.split(r"\s+and\s+|&", self["author"], maxsplit=1)[0]
            first_author = first_author.replace("~", " ").strip()
            if "," in first_author:
                search["author"] = first_author.split(",", 1)[0].strip()
            elif first_author:
                search["author"] = first_author.rsplit(None, 1)[-1]

        if (
            "title" in self
            and len(self["title"]) > 0
            and (search["firstPage"] == "" or self.is_preprint)
        ):
            search["title"] = clean_title(self["title"])

        url = "%s/freetools/mrlookup?%s" % (ams_api, urllib.parse.urlencode(search))
        self.update_entry(mrlookup_bibtex(get_json(url)))

    def mathscinet(self):
        """
        Use the paper's MR number to check/update the entry, if it exists.
        """
        if options.all or "mrnumber" in self:
            mrnumber = re.search(r"\d+", self["mrnumber"])
            if mrnumber is None:
                return  # nothing to search on
            self.update_entry([article_bibtex(mrnumber.group())])

    def mref(self):
        """
        Use MRef to check/update the entry.
        MRef takes a free-form reference so we give it the whole entry to play with.
        """
        # only check for books or articles for which we don't already have an mrnumber field
        if not options.all and (
            "mrnumber" in self
            or self.pub_type not in ["book", "article", "inproceedings", "incollection"]
        ):
            return
        ref = " ".join("%s" % self[key] for key in self.keys())
        url = "%s/freetools/mref?%s" % (ams_api, urllib.parse.urlencode({"ref": ref}))
        paper_id = get_json(url).get("paperId")
        self.update_entry([article_bibtex(paper_id)] if paper_id is not None else [])

    def arxiv(self):
        r"""
        Search the arXiv for this entry and, if there is a unique good (fuzzy)
        title match, add the `eprint` and `archiveprefix` fields. Unlike the AMS
        lookups, this only ever adds these two fields; it does not touch any of
        the other fields in the entry.
        """
        # we need a title to search on and, unless -a is given, we do not bother
        # looking up entries that already have an eprint field
        if self["title"] == "" or (not options.all and "eprint" in self):
            return

        clean_ti = clean_title(self["title"])
        if clean_ti == "":
            return
        matches = [
            aid
            for (aid, ti) in arxiv_search(self["title"])
            if good_match(clean_ti, clean_title(ti))
        ]
        bibup.debug("arXiv number of matches=%d" % len(matches))

        if len(matches) == 1:
            eprint = matches[0]
            # only report/update if something actually changes
            if self["eprint"] != eprint or self["archiveprefix"].lower() != "arxiv":
                if options.check:
                    self.checked_fields.update(eprint=eprint, archiveprefix="arXiv")
                else:
                    self["eprint"] = eprint
                    self["archiveprefix"] = "arXiv"
                    self.updated = True
        elif not self.is_preprint:
            bibup.debug("arXiv: no unique match for %s" % self.cite_key)


def parse_entries(papers):
    """Parse entries and retain their one-based source lines for diagnostics."""
    line, previous = 1, 0
    for match in bibtex_entry.finditer(papers):
        line += papers.count("\n", previous, match.start())
        previous = match.start()
        entry = Bibtex(match.group())
        entry.source_line = line
        yield entry


def normalized_text(value):
    """Normalize case, capitalization braces, spacing and punctuation."""
    value = unicodedata.normalize("NFKC", display_text(value)).casefold()
    return " ".join(re.findall(r"\w+", value))


def paper_signatures(entry):
    """Yield identifier matches and a conservative title/author fallback."""
    doi = urllib.parse.unquote(display_text(entry["doi"])).casefold()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        yield "DOI", doi

    mrnumber = re.match(
        r"(?:mr)?(\d+)(?:\s|$)", display_text(entry["mrnumber"]).casefold()
    )
    if mrnumber and int(mrnumber.group(1)) > 0:
        yield "MR number", str(int(mrnumber.group(1)))

    if entry["archiveprefix"].casefold() in ("", "arxiv"):
        eprint = display_text(entry["eprint"]).casefold()
        eprint = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", eprint)
        eprint = re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", eprint)
        if re.fullmatch(r"\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7}", eprint):
            yield "arXiv ID", eprint

    # Compare all surnames and first initials, accepting both BibTeX name
    # orders and full given names versus initials. Never match by title alone.
    authors = []
    for author in re.split(r"\s+and\s+", entry["author"] or entry["editor"]):
        if not author.strip():
            continue
        if "," in author:
            surname, given = author.split(",", 1)
        else:
            names = author.rsplit(None, 1)
            given, surname = names if len(names) == 2 else ("", names[0])
        surname = normalized_text(surname)
        if surname:
            authors.append((surname, normalized_text(given)[:1]))
    title = normalized_text(entry["title"])
    if title and authors:
        yield "title and authors", (title, tuple(sorted(authors)))


def duplicate_groups(entries):
    """Return repeated-key groups and connected paper groups with match reasons."""
    entries = [
        entry
        for entry in entries
        if entry.cite_key is not None
        and entry.pub_type not in ("comment", "string", "preamble")
    ]
    keys, seen = {}, {}
    parents = list(range(len(entries)))
    reasons = [set() for entry in entries]

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, entry in enumerate(entries):
        keys.setdefault(entry.cite_key, []).append(entry)
        for signature in paper_signatures(entry):
            if signature in seen:
                first, second = root(seen[signature]), root(index)
                if first != second:
                    parents[second] = first
                    reasons[first].update(reasons[second])
                reasons[first].add(signature[0])
            else:
                seen[signature] = index

    groups = {}
    for index, entry in enumerate(entries):
        groups.setdefault(root(index), []).append(entry)
    return (
        {key: group for key, group in keys.items() if len(group) > 1},
        [(group, reasons[index]) for index, group in groups.items() if len(group) > 1],
    )


def duplicate_warnings(entries, keys=True, papers=True):
    """Describe selected duplicate groups without changing entries."""
    key_groups, paper_groups = duplicate_groups(entries)
    if keys:
        for key, group in key_groups.items():
            yield wrap_report(
                "! Duplicate citation key '%s': lines %s."
                % (key, ", ".join(str(e.source_line) for e in group))
            )
    if not papers:
        return
    for group, reasons in paper_groups:
        identifiers = reasons - {"title and authors"}
        if identifiers:
            label = "Duplicate paper (shared %s)" % ", ".join(sorted(identifiers))
        else:
            label = "Possible duplicate paper (same title and authors)"
        locations = ", ".join(
            "'%s' (line %d)" % (entry.cite_key, entry.source_line) for entry in group
        )
        yield wrap_report("! %s: %s." % (label, locations))


def source_boundaries(source, start, closing, quotes=True, key=False):
    """Yield top-level commas and the closing delimiter, respecting nesting."""
    depth, quoted, escaped = 0, False, False
    index = start
    while index < len(source):
        char = source[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "%" and depth == 0 and not quoted:
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline
            continue
        elif char == closing and depth == 0 and not quoted:
            yield index
            return
        elif char == '"' and depth == 0 and quotes and not key:
            quoted = not quoted
        elif char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == "," and depth == 0 and not quoted:
            yield index
            key = False
        index += 1
    raise ValueError(
        "unclosed BibTeX entry or field near line %d"
        % (source.count("\n", 0, start) + 1)
    )


def source_entries(source):
    """Read literal duplicate metadata and source spans without rewriting fields.

    This small scanner also preserves quoted values, macros, comments, nesting
    and @ signs that the update-mode regular expressions cannot round-trip.
    Macro expressions are preserved but not expanded for duplicate matching.
    """
    header = re.compile(r"%[^\n]*|@\s*([A-Za-z]+)\s*([({])")
    position, line, previous = 0, 1, 0
    while True:
        match = header.search(source, position)
        if match is None:
            return
        position = match.end()
        if match.group(1) is None:  # a comment outside an entry
            continue
        kind = match.group(1).lower()
        boundaries = list(
            source_boundaries(
                source,
                position,
                "}" if match.group(2) == "{" else ")",
                quotes=kind != "comment",
                key=kind not in ("comment", "string", "preamble"),
            )
        )
        end = boundaries[-1]
        position = end + 1
        if kind in ("comment", "string", "preamble"):
            continue
        key_end = boundaries[0]
        key = source[match.end() : key_end].strip()
        if not key or re.search(r"[\s,{}()]", key):
            raise ValueError(
                "invalid citation key near line %d"
                % (source.count("\n", 0, match.start()) + 1)
            )
        entry = Bibtex("")
        entry.pub_type, entry.cite_key = kind, key
        entry.source_start, entry.source_end = match.start(), end + 1
        line += source.count("\n", previous, match.start())
        previous, entry.source_line = match.start(), line
        entry.key_start = (
            match.end()
            + len(source[match.end() : key_end])
            - len(source[match.end() : key_end].lstrip())
        )
        entry.key_end = entry.key_start + len(key)
        entry.field_spans = {}
        start = key_end + 1
        for boundary in boundaries[1:]:
            field = re.fullmatch(
                r"(?:\s|%[^\n]*(?:\n|$))*([A-Za-z][\w-]*)\s*=\s*(.*?)\s*",
                source[start:boundary],
                re.DOTALL,
            )
            if field:
                name, value = field.group(1).lower(), field.group(2)
                offset = start + field.start(2)
                literal = None
                if value.startswith("{"):
                    if (
                        list(source_boundaries(value, 1, "}", quotes=False))[-1]
                        == len(value) - 1
                    ):
                        literal, offset = value[1:-1], offset + 1
                elif re.fullmatch(r'"(?:\\.|[^"\\])*"', value, re.DOTALL):
                    literal, offset = value[1:-1], offset + 1
                elif re.fullmatch(r"\d+", value):
                    literal = value
                if literal is not None:
                    entry[name] = literal
                    entry.field_spans[name] = (offset, offset + len(literal))
            start = boundary + 1
        yield entry


def author_key(entry):
    """Make a portable key from surnames (or editors) and an available year."""
    surnames = []
    for author in re.split(r"\s+and\s+", entry["author"] or entry["editor"]):
        words = author.split()
        name = (
            author.split(",", 1)[0] if "," in author else (words[-1] if words else "")
        )
        name = re.sub(r"\\(?:[A-Za-z]+|[^A-Za-z])\s*", "", name)
        name = unicodedata.normalize("NFKD", display_text(name))
        name = "".join(char for char in name if not unicodedata.combining(char))
        words = re.findall(r"[A-Za-z0-9]+", name)
        surnames.append("".join(word[:1].upper() + word[1:] for word in words))
    year = re.search(r"\d{4}", entry["year"])
    return ("".join(surnames) or "Anonymous") + (year.group() if year else "")


def cleanup_duplicates(source, entries, papers=False, keys=False):
    """Edit only removed entries, repeated keys and unambiguous local references."""
    removed, changes, messages, removed_pairs = set(), [], [], []
    paper_groups = duplicate_groups(entries)[1] if papers else []
    for group, reasons in paper_groups:
        retained = group[0]
        for entry in group[1:]:
            removed.add(entry.source_start)
            changes.append((entry.source_start, entry.source_end, ""))
            removed_pairs.append((entry, retained))
    survivors = [entry for entry in entries if entry.source_start not in removed]
    reserved, seen = {entry.cite_key for entry in entries}, set()
    if keys:
        for entry in survivors:
            if entry.cite_key in seen:
                base = candidate = author_key(entry)
                suffix = 0
                while candidate in reserved:
                    suffix += 1
                    number, letters = suffix, ""
                    while number:
                        number, remainder = divmod(number - 1, 26)
                        letters = chr(97 + remainder) + letters
                    candidate = base + letters
                reserved.add(candidate)
                changes.append((entry.key_start, entry.key_end, candidate))
                messages.append(
                    "Renamed duplicate key '%s' (line %d) to '%s'."
                    % (entry.cite_key, entry.source_line, candidate)
                )
                entry.cite_key = candidate
            seen.add(entry.cite_key)

    # Preserve references to removed aliases inside the .bib file. Repeated
    # original keys continue to refer to their first surviving occurrence.
    aliases, live_keys = {}, {entry.cite_key for entry in survivors}
    for group, reasons in paper_groups:
        retained = group[0]
        for entry in group:
            if entry.source_start in removed and entry.cite_key not in live_keys:
                aliases.setdefault(entry.cite_key, retained.cite_key)
    for entry in survivors:
        for name in ("crossref", "xref", "related", "entryset", "xdata"):
            if name not in entry.field_spans:
                continue
            value = entry[name]
            updated = re.sub(
                r"[^\s,]+", lambda m: aliases.get(m.group(), m.group()), value
            )
            if value != updated:
                start, end = entry.field_spans[name]
                changes.append((start, end, updated))
                messages.append(
                    "Updated %s in '%s': %s -> %s."
                    % (name, entry.cite_key, value, updated)
                )
    messages = [
        "Removed duplicate paper '%s' (line %d); kept '%s' (line %d)."
        % (entry.cite_key, entry.source_line, retained.cite_key, retained.source_line)
        for entry, retained in removed_pairs
    ] + messages
    for start, end, replacement in sorted(changes, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source, messages


def lookup_entry(entry):
    """Run one AMS lookup, buffering its messages for ordered output."""
    lookup_log.messages = []
    try:
        if entry.has_valid_pub_type():
            getattr(entry, options.lookup)()
        return entry, lookup_log.messages
    finally:
        del lookup_log.messages


def lookup_entries(entries):
    """Yield AMS results in input order, with at most --threads jobs queued."""
    if options.threads == 1:
        results = map(lookup_entry, entries)
        for entry, messages in results:
            bib_print(*messages)
            yield entry
        return

    entries = iter(entries)
    with ThreadPoolExecutor(max_workers=options.threads) as pool:
        pending = deque(
            pool.submit(lookup_entry, entry)
            for entry in itertools.islice(entries, options.threads)
        )
        try:
            while pending:
                entry, messages = pending.popleft().result()
                following = next(entries, None)
                if following is not None:
                    pending.append(pool.submit(lookup_entry, following))
                bib_print(*messages)
                yield entry
        finally:
            for future in pending:
                future.cancel()


def positive_integer(value):
    """argparse type for a strictly positive thread count."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer")
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def process_options():
    r"""
    Set up and then parse the options to bibupdate using argparse.
    """
    global options, bibup

    parser = argparse.ArgumentParser(
        description="Update and validate BibTeX files",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "bibtexfile",
        nargs="?",
        type=argparse.FileType("r"),
        default=None,
        help="bibtex file to update",
    )

    parser.add_argument(
        "outputfile", nargs="?", type=str, default=None, help="output file"
    )

    updating = parser.add_argument_group("Updating and checking")
    lookup = updating.add_mutually_exclusive_group()
    lookup.add_argument(
        "-m",
        "--mrlookup",
        action="store_const",
        const="mrlookup",
        dest="lookup",
        default="mrlookup",
        help="use mrlookup to update bibtex entries (default)",
    )

    lookup.add_argument(
        "-M",
        "--mathscinet",
        action="store_const",
        const="mathscinet",
        dest="lookup",
        help="use mathscinet to update bibtex entries (less flexible)",
    )

    lookup.add_argument(
        "-n",
        "--mref",
        action="store_const",
        const="mref",
        dest="lookup",
        help="use mref to update bibtex entries",
    )

    updating.add_argument(
        "-a",
        "--all",
        action="store_true",
        default=False,
        help="update or validate ALL BibTeX entries",
    )

    updating.add_argument(
        "-X",
        "--arxiv",
        action="store_true",
        default=False,
        help="also add arXiv eprint fields to entries",
    )

    updating.add_argument(
        "-c",
        "--check",
        "--check1",
        dest="check",
        action="store_true",
        default=False,
        help="check all entries and report duplicates without writing a file",
    )

    updating.add_argument(
        "--threads",
        type=positive_integer,
        default=1,
        metavar="N",
        help="number of concurrent AMS lookups (default: 1; try 2)",
    )

    listing = parser.add_argument_group("Duplicate listing")
    cleanup = parser.add_argument_group("Duplicate cleanup")
    for group, flags, help_text in [
        (
            listing,
            ("-d", "--list-duplicates"),
            "list duplicate papers and keys without writing a file",
        ),
        (listing, ("--list-papers",), "list duplicate papers only"),
        (listing, ("--list-keys",), "list repeated citation keys only"),
        (
            cleanup,
            ("--duplicates", "--deduplicate"),
            "remove duplicate papers and rename repeated keys",
        ),
        (
            cleanup,
            ("--duplicate-papers", "--remove-duplicate-papers"),
            "remove duplicate papers, keeping the first",
        ),
        (
            cleanup,
            ("--duplicate-keys", "--rename-duplicate-keys"),
            "rename repeated keys using authors and year",
        ),
    ]:
        group.add_argument(*flags, action="store_true", default=False, help=help_text)

    output = parser.add_argument_group("Output and formatting")
    output.add_argument(
        "-o",
        "--overwrite",
        action="store_true",
        default=False,
        help="overwrite existing bibtex file",
    )

    output.add_argument(
        "-i",
        "--ignored-fields",
        type=str,
        default=None,
        metavar="FIELDS",
        action="append",
        help="a string of bibtex fields to ignore (default: %s)"
        % " ".join(DEFAULT_IGNORED_FIELDS),
    )

    output.add_argument(
        "-k",
        "--keep_fonts",
        action="store_true",
        default=False,
        help=r"do NOT replace fonts \Bbb, \germ and \scr in titles",
    )

    output.add_argument(
        "-w",
        "--wrap",
        type=int,
        default=0,
        action="store",
        choices=NonnegativeIntegers(),
        metavar="LEN",
        help="wrap bibtex fields to specified width",
    )

    reports = parser.add_argument_group("Reports and logging")
    reports.add_argument(
        "-q",
        "--quiet",
        "--quieter",
        dest="quieter",
        action="count",
        default=2,
        help="print brief updates and missed lookups; repeat for errors only",
    )

    reports.add_argument(
        "-l",
        "--log",
        default=sys.stdout,
        type=argparse.FileType("w"),
        help="log messages to specified file (defaults to stdout)",
    )

    help_options = parser.add_argument_group("Help and version")
    help_options.add_argument(
        "-h",
        "--help",
        action="help",
        help="show grouped options and exit",
    )
    help_options.add_argument(
        "-H",
        "--Help",
        action="store_true",
        default=False,
        help="print full program description",
    )

    help_options.add_argument(
        "-v",
        "--version",
        action="version",
        version=bibup.bibupdate_version,
        help="print the program version and exit",
    )

    # Keep the internal debugging switch out of user-facing help.
    parser.add_argument(
        "--debugging", action="count", default=0, help=argparse.SUPPRESS
    )

    # parse the options
    options = parser.parse_args()
    options.prog = parser.prog
    options.cleanup_papers = options.duplicates or options.duplicate_papers
    options.cleanup_keys = options.duplicates or options.duplicate_keys
    options.listing = (
        options.list_duplicates or options.list_papers or options.list_keys
    )
    if (options.cleanup_papers or options.cleanup_keys) and (
        options.listing or options.check
    ):
        parser.error(
            "cleanup options cannot be combined with listing options or --check"
        )

    if options.Help:
        # Keep Markdown link definitions for documentation builds, not terminal help.
        help_text = re.sub(r"(?:\n\[[^\]\n]+\]:[^\n]*)+\s*$", "", __doc__)
        bib_print(help_text.rstrip())
        sys.exit()
    elif options.bibtexfile == None:
        bib_error("no bibtex file specified")

    if options.ignored_fields is None:
        options.ignored_fields = list(DEFAULT_IGNORED_FIELDS)
    else:
        # Explicit -i options replace the defaults; -i "" keeps every field.
        options.ignored_fields = [
            field for group in options.ignored_fields for field in group.lower().split()
        ]

    # if check==True then we want to check everything
    if options.check:
        options.all = True

    # define word wrapping when requested
    if options.wrap != 0:
        bibup.wrapped = lambda field: "\n".join(
            textwrap.wrap(field, options.wrap, subsequent_indent="\t")
        )
    else:
        bibup.wrapped = lambda field: field

    # define debugging, verbose and warning functions
    if options.debugging > 0:
        options.quieter = 2
        bibup.debugging = True
        bibup.debug = bib_print
        if options.debugging == 4:
            # drop into the debugger
            import pdb

            pdb.set_trace()
    else:
        bibup.debug = lambda *arg: None
    # 2 == compact references, 3 (-q) == brief updates and warnings,
    # 4 (-qq) == errors only. Check-mode references are always printed.
    bibup.verbose = bib_print if options.quieter == 2 else lambda *arg: None
    bibup.warning = bib_print if options.quieter <= 3 else lambda *arg: None

    # a shorthand for fixed the fonts (to avoid an if-statement when calling it)
    bibup.fix_fonts = replace_fonts if not options.keep_fonts else lambda title: title


def open_output():
    """Open the selected bibliography output after backing up an existing file."""
    if options.overwrite:
        newfile = options.bibtexfile.name
    elif options.outputfile is not None:
        newfile = options.outputfile
    else:
        directory, base = os.path.split(options.bibtexfile.name)
        newfile = os.path.join(directory, "updated_" + base)
    if os.path.isfile(newfile) and os.path.getsize(newfile) > 0:
        try:
            shutil.copyfile(newfile, newfile + ".bak")
        except IOError:
            bib_error("unable to create backup file for %s" % newfile)
    try:
        return open(newfile, "w", newline="")
    except IOError:
        bib_error("unable to open new bibtex file %s" % newfile)


def main():
    r"""
    Open the files and the delegate all of the hard work to the BibTeX class.
    """
    process_options()

    # now we are ready to read the BibTeX file and start working
    try:
        if (
            options.listing or options.cleanup_papers or options.cleanup_keys
        ) and hasattr(options.bibtexfile, "reconfigure"):
            options.bibtexfile.reconfigure(newline="")
        papers = options.bibtexfile.read()  # file opened by argparse
        options.bibtexfile.close()
    except IOError:
        bib_error("unable to open bibtex file %s" % options.bibtexfile.name)

    if "@" not in papers:
        bib_error("no bibtex entries found in %s" % options.bibtexfile.name)
    asterisk = papers.index("@")

    if options.listing or options.cleanup_papers or options.cleanup_keys:
        try:
            entries = list(source_entries(papers))
            if options.listing:
                for warning in duplicate_warnings(
                    entries,
                    keys=options.list_duplicates or options.list_keys,
                    papers=options.list_duplicates or options.list_papers,
                ):
                    bibup.warning(warning)
            else:
                updated, messages = cleanup_duplicates(
                    papers,
                    entries,
                    papers=options.cleanup_papers,
                    keys=options.cleanup_keys,
                )
                with open_output() as output:
                    output.write(updated)
                for message in messages:
                    bibup.warning(wrap_report(message))
        except ValueError as error:
            bib_error(str(error))
        return

    entries = parse_entries(papers)
    if options.check:
        entries = list(entries)
        for warning in duplicate_warnings(entries):
            bibup.warning(warning)

    # if we are checking for errors then we check EVERYTHING but, in this case,
    # we don't need to create a new bibtex file
    if not options.check:
        newbibfile = open_output()
        newbibfile.write(papers[:asterisk])

    # Only AMS lookups run in workers. arXiv stays on this thread so its
    # requests remain sequential and respect the shared delay between calls.
    for bt in lookup_entries(entries):
        if bt.has_valid_pub_type():
            if options.arxiv:
                bt.arxiv()
            bt.report_update()

        # now write the new (and hopefully) improved entry
        if not options.check:
            newbibfile.write("%s\n\n" % bt)

    if not options.check:
        newbibfile.close()


# The following __doc__ string is used both to print the documentation and by
# setup.py to automatically generate the README file. It is defined before
# main() runs so that the -H option can print the full documentation.
__doc__ = r"""# bibupdate

{0.description}

Usage:

    bibupdate [-h|-H] [-a] [-X] [-c] [-d] [-k] [-i FIELDS] [-l LOG]
              [-m | -M | -n] [-o] [-q] [--threads N] [-w LEN]
              [--duplicates | --duplicate-papers | --duplicate-keys]
              [--list-duplicates | --list-papers | --list-keys]
              bibtexfile [outputfile]

This is a command line tool for updating the entries in a [BibTeX] file using the
free tools provided by the American Mathematical Society: [mrlookup], [MathSciNet]
and [MRef]. By default [bibupdate] uses [mrlookup] to update `article`, `book`,
`inproceedings` and `incollection` entries that have no `mrnumber` field. An
`mrnumber` field, even an empty one, skips this lookup unless `--all` or `--check`
is used. In addition, with the `-X` option, [bibupdate] can query the [arXiv] and add
`eprint` and `archiveprefix` fields to any entry that it finds there.

**Options by task**:

**Updating and checking**

    -m, --mrlookup        use mrlookup to update bibtex entries (default)
    -M, --mathscinet      use mathscinet to update bibtex entries (less flexible)
    -n, --mref            use mref to update bibtex entries
    -a, --all             update or validate ALL BibTeX entries
    -X, --arxiv           also add arXiv eprint fields to entries
    -c, --check           check all entries and report duplicates
    --threads N          number of concurrent AMS lookups (default: 1)

**Duplicate listing**

    -d, --list-duplicates list duplicate papers and keys without writing a file
    --list-papers        list duplicate papers only
    --list-keys          list repeated citation keys only

**Duplicate cleanup**

    --duplicates         remove duplicate papers and rename repeated keys
    --duplicate-papers   remove duplicate papers, keeping the first
    --duplicate-keys     rename repeated keys using authors and year

**Output and formatting**

    -o, --overwrite       overwrite existing bibtex file
    -i FIELDS, --ignored-fields FIELDS
                          a string of bibtex fields to ignore
    -k, --keep_fonts      do NOT replace fonts \Bbb, \germ and \scr in titles
    -w LEN, --wrap LEN    wrap bibtex fields to specified width

**Reports and logging**

    -q, --quiet           brief updates and missed lookups (repeat to suppress)
    -l LOG, --log LOG     log messages to specified file (defaults to stdout)

**Help and version**

    -h, --help            show grouped options and exit
    -H, --Help            print full program description
    -v, --version         print the program version and exit

**Note:**
As described below, you should check the new file for errors before deleting the
original version of your [BibTeX] file.

By default, [bibupdate] does not change your original database file. Instead, it creates a
new file with the name *updated_file.bib*, if your original file was *file.bib*.
It is also possible to have it replace your current file (use carefully!), or to
specify an output file.

[BibTeX] is widely used by the [LaTeX] community to maintain publication databases.
This script attempts to add missing fields to the papers in a [BibTeX] database
file by querying [mrlookup] and getting the missing information from there. This
is not completely routine because to search on [mrlookup] you need either the
authors or the title of the article and both of these can have non-standard
representations. If the article is already published then it is also possible to
use the publication year and its page numbers. To search on [mrlookup] we:

- use the first author's surname (accents and compound surnames can be problematic)
- use numeric page ranges or a single numeric page, when available for a
  published entry
- include the year for books, or when a numeric first page is available
- use the cleaned title when no numeric first page is available

The author query uses only the first author's surname: [mrlookup] does not
interpret a [BibTeX] list of authors joined by `and` as separate names. Braces
used to protect capitals in titles (for example `{{Kac-Moody}}`) are removed
for searching and matching; the title stored in the entry retains its braces.

If there is a unique match from [mrlookup] then [bibupdate]
updates the fields supplied by [mrlookup], except for the citation key and the
fields excluded by `--ignored-fields`. Local fields that are not supplied by
[mrlookup], such as `eprint`, are retained. By default, a message is printed whenever
existing fields in the database are changed. If the title of the retrieved paper
does not (fuzzily) match that of the original article then the entry is NOT
updated and a warning message is printed.

Although some care is taken to make sure that the new [BibTeX] entries correspond
to the same paper that the original entry referred to there is always a (small?)
chance the new entry corresponds to an entirely different paper. In my
experience this happens rarely, and mostly with unpublished manuscripts. In any
case, before you delete your original [BibTeX] file *you are strongly advised to
check the updated file BibTeX file carefully for errors!*

By default the program prints each updated entry's citation key on its own line,
followed by an indented bibliography-style reference. Report lines wrap at 78
columns, preserving the complete authors, title and publication details. If
[arXiv] fields are added as well, they appear in the same reference. `--check`
uses this compact layout to show entries that would change, without writing an
updated file. You can also compare the original and updated files using *diff*.

As [bibupdate] calls [mrlookup] this program will only be useful if you have
papers in your database that are listed in [MathSciNet]. As described below it is
also possible to call [MathSciNet] directly, however, this is less flexible
because the `mrnumber` field for each paper is required.

I wrote this script because I wanted to automatically add links to journals, the
[arXiv] and DOIs to the bibliographies of my papers using [hyperref]. This script
allowed me to add the missing urls and DOI fields to my [BibTeX] database. As a
bonus the script helped me to correct many minor errors that had crept into my
[BibTeX] file over the years (for example, incorrect page numbers and publication
years). Now I use the program to automatically update the preprint entries in my
database when the papers appear in [MathSciNet] after they are published.

## Options and defaults

Without options, [bibupdate] uses [mrlookup] with one AMS lookup at a time,
prints compact references to standard output, and writes `updated_file.bib`
beside the input `file.bib`. The `--all`, `--arxiv`, `--check`, `--duplicates`,
`--keep_fonts` and `--overwrite` switches are off. Field-value wrapping is off (`--wrap 0`).

### Updating and checking

#### `-m, --mrlookup`

Use mrlookup to update bibtex entries (default)

#### `-M, --mathscinet`

Use mathscinet to update bibtex entries

#### `-n, --mref`

Use mref to update bibtex entries

These three options select which of the free AMS tools is used to update the
[BibTeX] entries in the database. They are all free services provided by the
American Mathematical Society and [bibupdate] talks to them through the JSON
web APIs behind the AMS [free tools] pages.

By default [mrlookup] is used. Its advantage is that it allows flexible
searching by author, title, year and page numbers, so it can find papers even
when they do not yet have an `mrnumber`.

With `-M` the entry is looked up directly by its `mrnumber`. A field containing
digits is required: missing, empty or non-numeric MR numbers are skipped,
including with `--all` and `--check`.

With `-n` the whole entry is handed to [MRef] as a free-form reference. Its
best matching record is still checked against the local title by [bibupdate].
Like [mrlookup], [MRef] normally checks only `article`, `book`,
`inproceedings` and `incollection` entries without an `mrnumber` field; use
`--all` to check all recognised entry types.

#### `-a, --all`

Update or validate ALL BibTeX entries

By default this switch is off. With [mrlookup] or [MRef], enabling it checks all
recognised entry types, including entries with an `mrnumber` field. With
`--arxiv`, it also checks entries that already have an `eprint` field.
The `-M` tool always requires a usable MR number, even with `--all`.

#### `-X, --arxiv`

Also add arXiv eprint fields to entries

This switch is off by default. In addition to the usual AMS lookup, query the [arXiv] for each entry
and, when the paper is found there, add `eprint` and `archiveprefix`
fields to the [BibTeX] entry:

    eprint = {{2211.07069}},
    archiveprefix = {{arXiv}}

[bibupdate] searches the [arXiv] using the significant words in the title and
only adds these fields when there is a unique paper whose title is a good
(fuzzy) match. This option works together with any of the `-m`, `-M` or
`-n` lookup tools. The [arXiv] lookup changes only `eprint` and `archiveprefix`.
Entries with an `eprint` field, even an empty one, are skipped unless `-a` or
`--check` is used. When such entries are checked, these two fields can be replaced.
Entries without a usable title cannot be searched.

[bibupdate] waits at least {arxiv_delay} seconds after each completed [arXiv]
request before starting the next one. Updating a large database
with `-X` can therefore take a while.

#### `-c, --check, --check1`

Check all entries and report duplicates

This switch is off by default. It prints each entry that would change as a compact reference, using the proposed
database fields and retaining any other local fields. The original [BibTeX]
file is not changed and no output bibliography is written. `--check1` is an
alias for `--check`. Check mode implies `--all`; it runs [arXiv] lookups only
when `--arxiv` is also supplied. Proposed references remain visible even with
`-qq`.

Before network lookups, checks the input file for repeated citation keys and
papers listed under different keys. Shared DOI, MR number or arXiv identifiers
identify duplicate papers. Matching normalized titles and authors are reported
as possible duplicates; capitalization braces, punctuation, spacing and given
names versus initials are normalized. Warnings name the keys and source line
numbers for review, and remain visible with `--quiet`. Duplicate warnings
appear in purple on terminals; log files and redirected output remain plain
text.

#### `--threads N`

Number of concurrent AMS lookups

Use `--threads 2` to overlap two AMS lookups. The default is one thread;
the thread count must be a positive integer. This works with [mrlookup],
[MathSciNet] and [MRef]. Entries and log messages retain their original order.

[arXiv] lookups remain sequential and keep their delay between requests,
regardless of the thread count.

### Duplicate listing

#### `-d, --list-duplicates`

List repeated papers and citation keys without changing the bibliography

No network queries, output bibliography or backups are made, even with
`--overwrite` or an output filename. Warnings show keys and source lines in
purple on terminals, remain visible with `--quiet`, and are suppressed by `-qq`.
If no duplicates are found, nothing is printed.

#### `--list-papers`

List duplicate papers only, including possible title-and-author matches

#### `--list-keys`

List repeated citation keys only

The same listing-only behaviour and quiet levels apply to all three listing
options. Listing options may be combined to show both kinds of duplicates.

### Duplicate cleanup

#### `--duplicates` (alias `--deduplicate`)

Remove duplicate papers and rename repeated citation keys

All duplicate modes are off by default. This offline cleanup first removes
repeated papers, then renames repeated keys among the remaining entries.
It makes no AMS or [arXiv] queries. The output is `updated_file.bib` beside
`file.bib`, or the supplied output filename. Use `--overwrite` to replace the
input; existing non-empty output files are backed up to `.bak` as usual.

The retained entries keep their original fields and formatting: ignored-field,
font-conversion and wrapping options do not apply to cleanup. Cleanup cannot
be combined with listing options or `--check`. Other lookup options are ignored.

**Changed meaning:** `--duplicates` now writes a cleaned bibliography.
Use `-d` or `--list-duplicates` for the previous listing-only behaviour.

#### `--duplicate-papers` (alias `--remove-duplicate-papers`)

Remove repeated papers, keeping the first occurrence in file order

Papers match through a shared DOI, MR number or arXiv identifier, or through
matching normalized titles and authors, as described under `--check`.
Connected matches form one group, even when linked through different identifiers.
Papers with the same citation key are also compared. Title-and-author matching
is heuristic; inspect `--list-papers` before removing entries.

Only the first entry's fields are kept; fields from later entries are not merged.
Each removal reports the removed key and the retained key, with source lines.
Literal `crossref`, `xref`, `related`, `entryset` and `xdata` references inside the
bibliography are updated where the removed key has no surviving occurrence.
String macros are preserved without expansion. Citations in external LaTeX files
are not changed; use the reported mappings to update citations to removed keys.

#### `--duplicate-keys` (alias `--rename-duplicate-keys`)

Rename repeated keys without deleting papers

Keep the first occurrence of each key. Later occurrences receive a key formed
from all authors' surnames and the year, for example `AcharRiche2019`.
Editors are used if authors are missing, and `Anonymous` if neither is available.
When the key is already used, add `a`, `b`, ..., `z`, `aa`, etc., reserving all
original keys and newly generated keys. Each rename is reported with its source
line; use `--log` to save the mappings. References to the original repeated key continue to refer to its first
surviving occurrence; ambiguous citations need manual review.

Using `--duplicate-papers` together with `--duplicate-keys` is equivalent
to `--duplicates`.

### Output and formatting

#### `-o, --overwrite`

Overwrite the existing bibtex file with the updated version

This switch is off by default. Without it, the output is `updated_file.bib`
beside the input `file.bib`, or the output filename supplied as the last
argument. With `-o`, the input file is replaced and any output filename is
ignored. An existing, non-empty output file is copied to its `.bak` file before
writing, including when `-o` replaces the input. An existing `.bak` is replaced.

#### `-i FIELDS, --ignored-fields=FIELDS`

A string of [BibTeX] fields to ignore when writing the updated file

By default [bibupdate] removes the following fields from each [BibTeX] entry:

{ignored_fields}

The `-i` option replaces this default list. Repeated `-i` options are combined;
field names are case-insensitive and separated by spaces. An empty string keeps
every field:

    bibupdate -i "coden fjournal" file.bib   # ignore coden and fjournal
    bibupdate -i coden -i fjournal file.bib  # ignore coden and fjournal
    bibupdate -i "" file.bib                 # do not ignore any fields

#### `-k, --keep_fonts`

do NOT replace fonts `\Bbb`, `\germ` and `\scr` in titles

This switch is off by default, so supported font commands in titles are replaced.
The [BibTeX] entries generated by [mrlookup] use `\Bbb`, `\germ` and `\scr` for the
`\mathbb`, `\mathfrak` and `\mathscr` fonts. By default, in the *title* fields,
these fonts specifications are automatically changed to the following more
[LaTeX] friendly fonts:

- `\Bbb X` --> `\mathbb{{X}}`
- `\scr X` --> `\mathcal{{X}}`
- `\germ X` --> `\mathfrak{{X}}`

Use `-k` to retain the original font commands. This applies both to input titles
and to titles retrieved from AMS.

#### `-w LEN, --wrap LEN`

Wrap bibtex fields to specified width

The default is `0`, which leaves field values unwrapped. A positive `LEN`
wraps each field value to that width, with a tab before continuation lines.
Field names, braces and indentation add to the total line length, so `LEN`
is not a strict limit on complete output lines. Negative values are rejected.

### Reports and logging

#### `-q, --quiet, --quieter`

Print fewer messages

By default, [bibupdate] prints a wrapped reference for each updated entry and
a notice for each attempted AMS lookup that has no unique matching paper, including
preprints. With `-q` or `--quiet` it prints brief update notices, missed
lookups and network warnings, without separator bars or field-by-field changes.
Missed lookups appear in red when the output is a terminal; redirected output
and log files contain plain text. Reports wrap at 78 columns. `--quieter`
is retained as an alias. Repeat the option (`-qq`) to suppress update notices
and lookup warnings. Fatal errors are still reported.

The `--check` option prints proposed references in the compact layout,
including when combined with `-q` or `-qq`. Duplicate warnings follow the same
warning levels as missed lookups. Use `-l` to send messages to a log file.

#### `-l LOG, --log LOG`

Log output to file (defaults to stdout)

By default reports and warnings go to standard output. Use `-l` to write them
to a file instead; an existing log file is replaced. Fatal errors are also
printed to standard error.

### Help and version

#### `-h, --help`

Show a short list of options, grouped by task, and exit.

#### `-H, --Help`

Print this full description, including grouped options, defaults and known
issues, and exit.

#### `-v, --version`

Print the program version and licence, and exit.

## Known issues

- **BibTeX syntax:** the parser uses regular expressions rather than a full
  BibTeX grammar. Use brace-delimited entries with one field per line and
  braced values or simple bare words. Multiple fields on one line, quoted
  values, `#` concatenation, string-macro expansion, and
  fields containing `@` or `={{...}}` can be misread or omitted. Inspect the
  output carefully when using these forms. Duplicate listing and cleanup use
  a source scanner that preserves quoted and nested values, comments and macros;
  macros are not expanded for duplicate matching.
- **Title matching:** capitalization braces and apostrophes are removed before
  searching and comparison. Dollar-delimited mathematics is also removed,
  which can make distinct titles look alike. Other TeX commands and accents
  are not fully decoded, so
  equivalent spellings can still fail to match. A unique fuzzy title match is
  a heuristic, not a guarantee that the paper is correct.
- **Author names:** [mrlookup] uses only the first author's surname. A compound
  surname is preserved in `surname, given names` form; without a comma, only
  the last word is used. Accents, name particles and alternative spellings can
  therefore affect searches.
- **Page numbers:** numeric ranges and single numeric pages are supported.
  Article identifiers and prefixed page numbers are not handled reliably.
  If no numeric first page is extracted, the title is used instead; for
  non-book entries the year is then omitted from the query.
- **Ambiguous or missing results:** an AMS entry is updated only when one
  candidate survives title matching. No match or multiple matches leave it
  unchanged and produce a missed-lookup notice. Entries without a usable title
  have less protection against an incorrect match. Skipped entries, such as
  those with an existing `mrnumber` under the default settings, are not failed
  lookups and produce no missed-lookup notice.
- **arXiv searches:** searches use significant title words and examine at most
  ten results. Titles dominated by mathematics or short words can be missed.
  No unique title match leaves the arXiv fields unchanged; this is normally
  silent. With `--all` or `--check`, an existing identifier can be replaced by
  the identifier of the matching title, so review such changes.
- **Network services:** connections can fail, time out or return unexpected
  data. Requests use a {url_timeout}-second timeout. Connection errors and
  timeouts produce warnings, and processing continues. A missed lookup does
  not prove that the paper is absent from the database.

## Licence

{0.author}
{0.license}
[bibupdate] Version {0.version}. Copyright (C) {0.copyright}

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License ([GPL]) as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

[BibTeX]: http://www.bibtex.org/
[mrlookup]: https://mathscinet.ams.org/mathscinet/freetools/mrlookup
[MathSciNet]: https://mathscinet.ams.org/mathscinet/freetools/mrlookup
[MRef]: https://mathscinet.ams.org/mathscinet/freetools/mref
[bibupdate]: {0.url}
[arXiv]: http://arxiv.org/
[LaTeX]: http://en.wikipedia.org/wiki/LaTeX
[hyperref]: http://www.ctan.org/pkg/hyperref
[free tools]: https://mathscinet.ams.org/mathscinet/freetools
[GPL]: http://www.gnu.org/licenses/gpl.html
""".format(
    bibup,
    ignored_fields="\n".join("- `" + field + "`" for field in DEFAULT_IGNORED_FIELDS),
    arxiv_delay=arxiv_delay,
    url_timeout=url_timeout,
)

##############################################################################
if __name__ == "__main__":
    main()
