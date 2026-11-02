"""Maintainer commands: readme (or docs), ctan, and release.

Documentation generation needs Pandoc; PDF builds also need latexmk and TeX.
These are build dependencies only: bibupdate.py remains a standalone script.
Other commands retain the legacy setuptools packaging interface.
"""

import argparse
import ast
import contextlib
import datetime
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import quote

import bibupdate

ROOT = Path(__file__).resolve().parent
PREAMBLE_BEGIN = "% BEGIN GENERATED BIBUPDATE PREAMBLE"
PREAMBLE_END = "% END GENERATED BIBUPDATE PREAMBLE"
BODY_BEGIN = "% BEGIN GENERATED BIBUPDATE HELP"
BODY_END = "% END GENERATED BIBUPDATE HELP"
LICENCE_BEGIN = "% BEGIN GENERATED BIBUPDATE LICENCE"
LICENCE_END = "% END GENERATED BIBUPDATE LICENCE"
GENERATED_REGIONS = [
    (PREAMBLE_BEGIN, PREAMBLE_END),
    (BODY_BEGIN, BODY_END),
    (LICENCE_BEGIN, LICENCE_END),
]
CTAN_FILES = (
    "bibupdate.py",
    "README.md",
    "bibupdate.tex",
    "bibupdate.pdf",
    "bibupdate.ini",
    "LICENSE.txt",
)

ctan_specs = """# Generated: {today}
contribution=bibupdate
version={version}
name={author}
email={author_email}
summary={description}
directory=/support/bibupdate
DoNotAnnounce=0
announce={description}
notes=Requires python
license=free
freeversion=gpl
file=bibupdate.tar.gz
"""


def replace_region(source, begin, end, content):
    """Replace one marked region, refusing to guess where manual text belongs."""
    if source.count(begin) != 1 or source.count(end) != 1:
        raise ValueError(
            "bibupdate.tex must contain exactly one %s / %s pair" % (begin, end)
        )
    before, _, rest = source.partition(begin)
    old, separator, after = rest.partition(end)
    if not separator:
        raise ValueError("Generated-region markers are in the wrong order")
    return before + begin + "\n" + content.strip() + "\n" + end + after


def readme_markdown():
    """Add web badges and testing notes to the README."""
    version = quote(bibupdate.bibup.version.replace("-", "--"), safe="")
    badges = (
        "[![Version](https://img.shields.io/badge/version-%s-success)](%s)\n"
        "[![Requires Python 3.9+](https://img.shields.io/badge/requires-python3.9%%2B-important)](https://www.python.org/)\n"
        "[![GPLv3](https://img.shields.io/badge/license-GPLv3-blueviolet.svg)](https://www.gnu.org/licenses/gpl-3.0.en.html)"
    ) % (version, bibupdate.bibup.url)
    testing = """## Tests

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

"""
    readme = bibupdate.__doc__.replace("\n\n", "\n\n" + badges + "\n\n", 1)
    return readme.replace("## Licence\n", testing + "## Licence\n", 1)


def build_documentation(directory=ROOT, make_pdf=True):
    """Refresh the README and the manual's generated regions from built-in help."""
    directory = Path(directory)
    manual = directory / "bibupdate.tex"
    source = manual.read_text(encoding="utf-8")
    # Validate the template before running optional tools or changing files.
    for begin, end in GENERATED_REGIONS:
        replace_region(source, begin, end, "")

    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise FileNotFoundError(
            "Building documentation requires Pandoc (https://pandoc.org)."
        )
    document = json.loads(
        subprocess.run(
            [pandoc, "--from=gfm", "--to=json"],
            input=bibupdate.__doc__,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    )
    # The banner supplies the title. Leave all remaining headings unnumbered,
    # and render the final licence separately, after the handwritten notes.
    blocks = document["blocks"]
    if blocks and blocks[0]["t"] == "Header" and blocks[0]["c"][0] == 1:
        blocks = blocks[1:]
    licence_start = None
    for index, block in enumerate(blocks):
        if block["t"] == "Header":
            level, attributes, title = block["c"]
            attributes[1].extend(["unnumbered", "unlisted"])
            if level == 2 and title == [{"t": "Str", "c": "Licence"}]:
                licence_start = index
    if licence_start is None:
        raise ValueError("Built-in Markdown help must end with a '## Licence' section")

    def latex_fragment(blocks):
        formatted = []
        in_description = False
        for block in blocks:
            if block["t"] == "Header" and block["c"][0] <= 4:
                if in_description:
                    formatted.append(
                        {
                            "t": "RawBlock",
                            "c": ["latex", r"\end{BibUpdateOptionDescription}"],
                        }
                    )
                    in_description = False
                formatted.append(block)
                if block["c"][0] == 4:
                    formatted.append(
                        {
                            "t": "RawBlock",
                            "c": ["latex", r"\begin{BibUpdateOptionDescription}"],
                        }
                    )
                    in_description = True
            else:
                formatted.append(block)
        if in_description:
            formatted.append(
                {"t": "RawBlock", "c": ["latex", r"\end{BibUpdateOptionDescription}"]}
            )
        fragment = dict(document, blocks=formatted)
        result = subprocess.run(
            [pandoc, "--from=json", "--to=latex", "--syntax-highlighting=none"],
            input=json.dumps(fragment),
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        return (
            result.replace(r"\paragraph*", r"\BibUpdateOptionHeading*")
            .replace(
                r"\begin{verbatim}",
                r"\begin{Verbatim}[breaklines=true,fontsize=\small]",
            )
            .replace(r"\end{verbatim}", r"\end{Verbatim}")
        )

    body = latex_fragment(blocks[:licence_start])
    licence = latex_fragment(blocks[licence_start:])
    preamble = r"""\usepackage[T1]{fontenc}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\makeatletter
\newcommand{\BibUpdateOptionHeading}{\@startsection{paragraph}{4}{0pt}
  {-6pt plus -2pt minus -1pt}{3pt}
  {\normalfont\bfseries\color{NavyBlue}}}
\makeatother
\newenvironment{BibUpdateOptionDescription}
  {\begin{list}{}{\setlength{\leftmargin}{2em}\setlength{\rightmargin}{0pt}%
    \setlength{\listparindent}{0pt}%
    \setlength{\topsep}{0pt}\setlength{\parsep}{4pt plus 1pt}%
    \setlength{\itemsep}{0pt}}\item\relax}
  {\end{list}}"""
    source = replace_region(source, PREAMBLE_BEGIN, PREAMBLE_END, preamble)
    source = replace_region(
        source,
        BODY_BEGIN,
        BODY_END,
        "% Generated from bibupdate.__doc__; edit the help, then run "
        "python3 setup.py readme.\n" + body,
    )
    source = replace_region(source, LICENCE_BEGIN, LICENCE_END, licence)
    (directory / "README.md").write_text(readme_markdown(), encoding="utf-8")
    manual.write_text(source, encoding="utf-8")
    print("Updated README.md and the generated regions in bibupdate.tex", flush=True)

    if make_pdf:
        latexmk = shutil.which("latexmk")
        if latexmk is None:
            raise FileNotFoundError(
                "PDF builds require latexmk. Install it with your TeX distribution "
                "or use: python3 setup.py readme --no-pdf"
            )
        subprocess.run(
            [
                latexmk,
                "-pdf",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-no-shell-escape",
                manual.name,
            ],
            cwd=directory,
            check=True,
        )


def release_report(status, message, color_text=False, stream=None):
    """Format maintainer reports, keeping redirected output free of ANSI codes."""
    stream = sys.stdout if stream is None else stream
    terminal = getattr(stream, "isatty", lambda: False)()
    reset = "\033[0m" if terminal else ""
    if status == "heading":
        bold = "\033[1m" if terminal else ""
        print("\n" + bold + message + reset + "\n", file=stream, flush=True)
        return
    symbol, color = {
        "success": ("✓", "\033[1;38;2;34;139;34m"),
        "failure": ("✗", "\033[1;31m"),
        "warning": ("!", "\033[1;33m"),
        "skip": ("-", "\033[1;33m"),
    }[status]
    try:
        symbol.encode(getattr(stream, "encoding", None) or "utf-8")
    except (LookupError, UnicodeEncodeError):
        symbol = {"success": "+", "failure": "x", "warning": "!", "skip": "-"}[status]
    if not terminal:
        color = ""
    body = str(message).rstrip("\n").replace("\n", "\n    ")
    if status == "success" and not color_text:
        text = "  %s%s%s %s" % (color, symbol, reset, body)
    else:
        text = "%s  %s %s%s" % (color, symbol, body, reset)
    print(text, file=stream, flush=True)


def build_ctan_package(directory=ROOT):
    """Package existing release files and write ctanupload metadata."""
    directory = Path(directory)
    missing = [name for name in CTAN_FILES if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing CTAN release files: "
            + ", ".join(missing)
            + ". Run python3 setup.py readme to rebuild documentation."
        )
    metadata = dict(
        bibupdate.Settings(directory / "bibupdate.ini"),
        today=datetime.date.today().strftime("%d, %b %Y"),
    )
    # Finish both outputs before replacing a previous release archive.
    with tempfile.TemporaryDirectory(prefix=".ctan-", dir=directory) as temporary:
        temporary = Path(temporary)
        archive = temporary / "bibupdate.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            for name in CTAN_FILES:
                source = directory / name
                before = source.stat()
                package.add(source, arcname="bibupdate/" + name, recursive=False)
                after = source.stat()
                if (before.st_mtime_ns, before.st_size, before.st_ino) != (
                    after.st_mtime_ns,
                    after.st_size,
                    after.st_ino,
                ):
                    raise OSError(
                        "%s changed while packaging; wait for its writer to finish"
                        % name
                    )
        configuration = temporary / "bibupdate.ctan"
        configuration.write_text(ctan_specs.format(**metadata), encoding="utf-8")
        archive.replace(directory / archive.name)
        configuration.replace(directory / configuration.name)
    release_report(
        "success", "Created bibupdate.tar.gz and bibupdate.ctan", color_text=True
    )
    print("To upload to ctan run: ctanupload -F bibupdate.ctan")


def run_tests(directory=ROOT):
    """Run offline tests and return a compact summary, raising on failure."""
    directory = Path(directory)
    if not any((directory / "tests").glob("test_*.py")):
        raise ValueError("no tests found")
    # Discovery imports the script from this checkout, even from another cwd.
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=directory,
        text=True,
        capture_output=True,
    )
    report = result.stdout + result.stderr
    if re.search(r"Ran 0 tests? in ", report):
        raise ValueError("test discovery ran no tests")
    if result.returncode != 0:
        # Show failed tests and tracebacks without a long list of passing tests.
        details = "\n".join(
            line for line in report.splitlines() if not re.search(r"\.\.\. ok$", line)
        )
        raise ValueError(details)
    summary = re.search(r"Ran [1-9]\d* tests? in [^\n]+", report)
    if summary is None:
        raise ValueError("test discovery ran no tests")
    return summary.group() + "; " + report.strip().splitlines()[-1]


def check_release(directory=ROOT, skip_tests=False):
    """Report all readiness failures without modifying the release files."""
    directory = Path(directory)
    failures = []
    release_report("heading", "Checking bibupdate release readiness")

    def check(label, operation):
        try:
            detail = operation()
        except (
            OSError,
            ValueError,
            SyntaxError,
            subprocess.CalledProcessError,
        ) as error:
            message = str(error)
            failures.append(label + ": " + message)
            release_report("failure", "%s: %s" % (label, message))
        else:
            message = label + (": " + str(detail) if detail else "")
            release_report("success", message, color_text=bool(detail))

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    metadata = {}

    def read_metadata():
        for line in (
            (directory / "bibupdate.ini").read_text(encoding="utf-8").splitlines()
        ):
            if "=" in line:
                key, value = line.split("=", 1)
                metadata[key.strip().lower()] = value.strip()
        missing = sorted(set(bibupdate.default_metadata) - metadata.keys())
        require(not missing, "missing ini keys: " + ", ".join(missing))
        return "version " + metadata["version"]

    check("INI metadata", read_metadata)
    version = metadata.get("version", "")

    def check_version():
        require(
            re.fullmatch(r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2}", version),
            "expected X.Y.Z without a prerelease suffix, got " + repr(version),
        )

    check("Release version", check_version)

    release_date = None

    def check_date():
        nonlocal release_date
        value = metadata.get("release date", "")
        date_text = " ".join(value.split()[:3])
        for format in ("%d %B %Y", "%Y-%m-%d"):
            try:
                release_date = datetime.datetime.strptime(date_text, format).date()
                break
            except ValueError:
                pass
        require(release_date is not None, "invalid release date: " + repr(value))
        require(release_date <= datetime.date.today(), "release date is in the future")

    check("Release date", check_date)

    def check_source_metadata():
        tree = ast.parse((directory / "bibupdate.py").read_text(encoding="utf-8"))
        definitions = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "default_metadata"
                for target in node.targets
            )
        ]
        require(
            len(definitions) == 1, "expected one literal default_metadata dictionary"
        )
        embedded = ast.literal_eval(definitions[0])
        require(isinstance(embedded, dict), "default_metadata must be a dictionary")
        differences = sorted(
            key for key, value in metadata.items() if embedded.get(key) != value
        )
        require(
            not differences,
            "embedded defaults differ from ini: " + ", ".join(differences),
        )

    check("Standalone metadata", check_source_metadata)

    def copyright_year(value):
        years = re.findall(r"\d{2,4}", value)
        require(bool(years), "copyright year is missing")
        year = int(years[-1])
        return 2000 + year if year < 100 else year

    def check_copyright():
        require(release_date is not None, "a valid release date is needed")
        require(
            copyright_year(metadata.get("copyright", "")) == release_date.year,
            "ini copyright end year differs from the release year",
        )
        tree = ast.parse((directory / "bibupdate.py").read_text(encoding="utf-8"))
        header = ast.get_docstring(tree) or ""
        match = re.search(r"(?im)^Copyright\s*\(C\)\s*(.+)$", header)
        require(match is not None, "script header has no Copyright (C) notice")
        require(
            copyright_year(match.group(1)) == release_date.year,
            "script header copyright end year differs from the release year",
        )

    check("Copyright years", check_copyright)

    def check_changes():
        require(release_date is not None, "a valid release date is needed")
        source = (directory / "CHANGES.md").read_text(encoding="utf-8")
        first = next((line.strip() for line in source.splitlines() if line.strip()), "")
        match = re.search(
            r"\bv?(\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?)(?=\s|\*|$)", first, re.I
        )

        def normalized(value):
            number, separator, suffix = value.partition("-")
            if number.count(".") == 1:
                number += ".0"
            return number + separator + suffix

        require(
            match is not None and normalized(match.group(1)) == normalized(version),
            "first changelog heading must name version " + version,
        )
        date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", first)
        if date:
            require(
                date.group() == release_date.isoformat(),
                "changelog date differs from the ini release date",
            )
        date = re.search(r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b", first)
        if date:
            require(
                datetime.datetime.strptime(date.group(), "%d %B %Y").date()
                == release_date,
                "changelog date differs from the ini release date",
            )

    check("Changelog", check_changes)

    def check_git():
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=directory,
            text=True,
            capture_output=True,
        )
        require(result.returncode == 0, result.stderr.strip() or "not a Git checkout")
        require(
            not result.stdout.strip(), "uncommitted files:\n" + result.stdout.rstrip()
        )

    check("Git working tree", check_git)

    def check_files():
        missing = [name for name in CTAN_FILES if not (directory / name).is_file()]
        require(not missing, "missing release files: " + ", ".join(missing))

    check("Package files", check_files)

    def check_documentation():
        # Regenerate in isolation, preserving the source tree and any running editor.
        with tempfile.TemporaryDirectory(prefix="bibupdate-doc-check-") as temporary:
            temporary = Path(temporary)
            shutil.copy2(directory / "bibupdate.tex", temporary / "bibupdate.tex")
            with contextlib.redirect_stdout(io.StringIO()):
                build_documentation(temporary, make_pdf=False)
            stale = [
                name
                for name in ("README.md", "bibupdate.tex")
                if (temporary / name).read_bytes() != (directory / name).read_bytes()
            ]
            require(
                not stale,
                "out-of-date generated sources: "
                + ", ".join(stale)
                + "; run python3 setup.py readme",
            )

    check("Generated documentation", check_documentation)

    def check_pdf():
        pdf = directory / "bibupdate.pdf"
        before = pdf.stat()
        outdated = [
            name
            for name in ("bibupdate.py", "bibupdate.ini", "bibupdate.tex")
            if (directory / name).stat().st_mtime_ns > before.st_mtime_ns
        ]
        require(
            not outdated,
            "PDF is older than "
            + ", ".join(outdated)
            + "; run python3 setup.py readme",
        )
        pdfinfo = shutil.which("pdfinfo")
        require(pdfinfo is not None, "pdfinfo is required (install Poppler)")
        result = subprocess.run(
            [pdfinfo, "bibupdate.pdf"],
            cwd=directory,
            text=True,
            capture_output=True,
            check=True,
        )
        fields = dict(
            line.split(":", 1) for line in result.stdout.splitlines() if ":" in line
        )
        expected = "bibupdate - " + version
        require(
            fields.get("Title", "").strip() == expected,
            "PDF title does not match " + repr(expected),
        )
        require(int(fields.get("Pages", "0")) > 0, "PDF has no pages")
        after = pdf.stat()
        require(
            (before.st_mtime_ns, before.st_size, before.st_ino)
            == (after.st_mtime_ns, after.st_size, after.st_ino),
            "PDF changed during validation; wait for its writer to finish",
        )

    check("Manual PDF", check_pdf)

    def check_log():
        log = (directory / "bibupdate.log").read_text(
            encoding="utf-8", errors="replace"
        )
        require(
            (directory / "bibupdate.log").stat().st_mtime_ns
            >= (directory / "bibupdate.tex").stat().st_mtime_ns,
            "LaTeX log is older than the manual source; rebuild the manual",
        )
        problems = [
            line.strip()
            for line in log.splitlines()
            if re.search(
                r"^!|undefined (?:references|citations)|(?:Reference|Citation).*undefined|"
                r"Rerun to get|Label\(s\) may have changed|There were multiply-defined labels",
                line,
            )
        ]
        require(
            not problems, "unresolved LaTeX diagnostics:\n" + "\n".join(problems[:10])
        )

    check("LaTeX log", check_log)

    if skip_tests:
        release_report("skip", "Test suite skipped (--skip-tests)")
    else:
        check("Test suite", lambda: run_tests(directory))
    return failures


def release(directory=ROOT, check_only=False, force=False, skip_tests=False):
    failures = check_release(directory, skip_tests=skip_tests)
    if failures:
        release_report("failure", "%d release check(s) failed." % len(failures))
    else:
        release_report("success", "All release checks passed.", color_text=True)
    if check_only:
        return not failures
    if failures and not force:
        release_report(
            "failure",
            "Archive not created. Resolve the failures or explicitly use --force.",
        )
        return False
    if failures:
        release_report("warning", "Forced build of bibupdate.tar.gz (--force).")
    build_ctan_package(directory)
    return True


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("readme", "docs"):
        parser = argparse.ArgumentParser(
            description="Refresh README.md and bibupdate.tex from the built-in help, "
            "then compile bibupdate.pdf."
        )
        parser.add_argument(
            "--no-pdf",
            action="store_true",
            help="refresh the documentation sources without compiling",
        )
        args = parser.parse_args(sys.argv[2:])
        try:
            build_documentation(make_pdf=not args.no_pdf)
        except (
            ImportError,
            OSError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            release_report(
                "failure", "Documentation build failed: %s" % error, stream=sys.stderr
            )
            raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "release":
        parser = argparse.ArgumentParser(
            description="Check release readiness and build the CTAN archive, or run "
            "only the requested maintenance actions (-m/-t)."
        )
        parser.add_argument(
            "-c",
            "--check-only",
            action="store_true",
            help="check release readiness without packaging",
        )
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="force building bibupdate.tar.gz",
        )
        parser.add_argument(
            "-s", "--skip-tests", action="store_true", help="skip the test suite"
        )
        parser.add_argument(
            "-m",
            "--manual",
            action="store_true",
            help="rebuild README.md and the manual (combine with -t)",
        )
        parser.add_argument(
            "-t",
            "--tests",
            action="store_true",
            help="run the test suite (combine with -m)",
        )
        args = parser.parse_args(sys.argv[2:])
        if (args.manual or args.tests) and (
            args.check_only or args.force or args.skip_tests
        ):
            parser.error(
                "-m/-t cannot be combined with --check-only, --force or --skip-tests"
            )
        try:
            if args.manual or args.tests:
                if args.manual:
                    build_documentation()
                    release_report("success", "Documentation rebuilt.", color_text=True)
                if args.tests:
                    release_report(
                        "success", "Test suite: " + run_tests(), color_text=True
                    )
                return
            ready = release(
                check_only=args.check_only, force=args.force, skip_tests=args.skip_tests
            )
        except (
            OSError,
            ValueError,
            tarfile.TarError,
            subprocess.CalledProcessError,
        ) as error:
            release_report("failure", "Release failed: %s" % error, stream=sys.stderr)
            raise SystemExit(1)
        if not ready:
            raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "ctan":
        parser = argparse.ArgumentParser(
            description="Create bibupdate.tar.gz and ctanupload metadata from existing release files."
        )
        parser.parse_args(sys.argv[2:])
        try:
            build_ctan_package()
        except (OSError, tarfile.TarError) as error:
            release_report(
                "failure", "CTAN packaging failed: %s" % error, stream=sys.stderr
            )
            raise SystemExit(1)
    else:
        # Setuptools is needed only for the legacy package-building commands.
        from setuptools import setup

        setup(
            name="bibupdate",
            author=bibupdate.bibup["author"],
            author_email=bibupdate.bibup["author_email"],
            description=bibupdate.bibup["description"],
            keywords=bibupdate.bibup["keywords"],
            license=bibupdate.bibup["license"],
            url=bibupdate.bibup["url"],
            version=bibupdate.bibup["version"],
            long_description=readme_markdown(),
            long_description_content_type="text/markdown",
            py_modules=["bibupdate"],
            classifiers=[
                "Development Status :: 5 - Production/Stable",
                "Environment :: Console",
                "Intended Audience :: Science/Research",
                "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
                "Natural Language :: English",
                "Programming Language :: Python :: 3.7",
                "Topic :: Text Processing",
                "Topic :: Text Processing :: Markup :: LaTeX",
                "Topic :: Utilities",
            ],
            install_requires=[],
            entry_points={"console_scripts": ["bibupdate = bibupdate:main"]},
        )


if __name__ == "__main__":
    main()
