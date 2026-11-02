# bibupdate

[![Version](https://img.shields.io/badge/version-1.4.0--r0-success)](https://github.com/AndrewMathas/BibUpdate)
[![Requires Python 3.9+](https://img.shields.io/badge/requires-python3.9%2B-important)](https://www.python.org/)
[![GPLv3](https://img.shields.io/badge/license-GPLv3-blueviolet.svg)](https://www.gnu.org/licenses/gpl-3.0.en.html)

Automatically update the entries of a bibtex file

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
used to protect capitals in titles (for example `{Kac-Moody}`) are removed
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

    eprint = {2211.07069},
    archiveprefix = {arXiv}

[bibupdate] searches the [arXiv] using the significant words in the title and
only adds these fields when there is a unique paper whose title is a good
(fuzzy) match. This option works together with any of the `-m`, `-M` or
`-n` lookup tools. The [arXiv] lookup changes only `eprint` and `archiveprefix`.
Entries with an `eprint` field, even an empty one, are skipped unless `-a` or
`--check` is used. When such entries are checked, these two fields can be replaced.
Entries without a usable title cannot be searched.

[bibupdate] waits at least 3 seconds after each completed [arXiv]
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

- `coden`
- `mrreviewer`

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

- `\Bbb X` --> `\mathbb{X}`
- `\scr X` --> `\mathcal{X}`
- `\germ X` --> `\mathfrak{X}`

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
  fields containing `@` or `={...}` can be misread or omitted. Inspect the
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
  data. Requests use a 30-second timeout. Connection errors and
  timeouts produce warnings, and processing continues. A missed lookup does
  not prove that the paper is absent from the database.

## Tests

The files in `tests/` contain automated checks to catch bugs after changes:

- [test_mrlookup.py](tests/test_mrlookup.py): paper searches and updates,
  including title braces and author names.
- [test_duplicates.py](tests/test_duplicates.py): duplicate listing, paper removal,
  collision-free key renaming and preservation of bibliography source text.
- [test_threads_output.py](tests/test_threads_output.py): parallel lookups,
  output formatting, colours, and quiet/check modes.
- [test_documentation.py](tests/test_documentation.py): documentation generation
  and preservation of handwritten manual text.
- [test_ctan.py](tests/test_ctan.py): release archive contents and CTAN upload
  metadata generation.
- [test_settings.py](tests/test_settings.py): metadata discovery through symlinks
  and standalone operation without an ini file.
- [test_release.py](tests/test_release.py): release readiness checks and failure
  handling before packaging.

Run all tests from the project directory:

```bash
python3 -m unittest discover -s tests -v
```

The tests use simulated lookup responses, so no network access is needed.
Documentation checks require Pandoc and latexmk to be available.

## Preparing a release

From a source checkout, update `bibupdate.ini`, the script's embedded
`default_metadata` and copyright notice, and the first heading of `CHANGES.md`.
The changelog heading must name the release version; bold headings such as
`**v2.0** -- CTAN release` and headings such as
`## Version 2.0.0 - 2026-11-02` are both accepted. A zero patch component may be
omitted in the changelog. Any date in the heading must match the ini file.
Release versions in the ini file must be `X.Y.Z`,
without a prerelease suffix, and the copyright end year must match the release
year. Rebuild the documentation and commit the prepared files:

```bash
python3 setup.py readme
# Review and commit the release changes, then:
python3 setup.py release
```

`./make_release` runs the same release command. It checks metadata consistency,
the changelog, a clean Git working tree (including untracked files), generated
documentation, PDF freshness and metadata, the LaTeX log, and all tests before
creating `bibupdate.tar.gz` and `bibupdate.ctan`. Checks use Pandoc and `pdfinfo`;
rebuilding the manual also needs latexmk and TeX. The release check does not
change metadata or rebuild files automatically.
Terminal reports use green ticks for passing checks and test summaries, red
crosses and text for failures, and yellow notices for skipped tests or overrides.
Redirected output contains no colour escape sequences.

Use `./make_release -m` (or `--manual`) to rebuild the README and manual only,
`./make_release -t` (or `--tests`) to run the tests only, or `./make_release -mt`
to do both. These maintenance actions do not run readiness checks or create an
archive, so they also work while preparing an uncommitted release. They cannot
be combined with the release-only flags below. The same options are available
with `python3 setup.py release`.

Use `-c` or `--check-only` to inspect readiness without creating an archive, `-s` or
`--skip-tests` to omit the test suite, and `-f` or `--force` to building
the tar file. Missing or changing package files still prevent an
archive from being created. `--check-only` returns a nonzero exit status when
any check fails, including with `--force`.

`python3 setup.py ctan` packages the existing release files without readiness
checks. Both commands prepare files locally; upload separately using
`ctanupload -F bibupdate.ctan` from the source directory.

## Licence

Andrew Mathas

[bibupdate] Version 1.4.0-r0. Copyright (C) 2014-2026

GNU General Public License, Version 3, 29 June 2007

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
[bibupdate]: https://github.com/AndrewMathas/BibUpdate
[arXiv]: http://arxiv.org/
[LaTeX]: http://en.wikipedia.org/wiki/LaTeX
[hyperref]: http://www.ctan.org/pkg/hyperref
[free tools]: https://mathscinet.ams.org/mathscinet/freetools
[GPL]: http://www.gnu.org/licenses/gpl.html
