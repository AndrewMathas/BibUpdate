## Version 2.0 - November 2026 (CTAN release)

- Adds `--check-only`, `--skip-tests` and `--force` to the release command
- Adds `--threads` for ordered concurrent AMS lookups; arXiv lookups remain sequential
- Adds `-X`/`--arxiv` lookups and `-n`/`--mref` support
- Adds `make_release -m` and `-t` to rebuild documentation and run tests separately
- Forces manual recompilation on every `make_release -m` run
- Adds missing fields even when existing fields are unchanged
- Adds network timeouts and continues processing after failed lookups
- Adds offline duplicate paper removal and collision-free key renaming
- Adds release checks for metadata, documentation, Git status and tests
- Adds separate commands to list duplicate papers and citation keys
- Builds the CTAN archive and upload metadata with `setup.py ctan`
- Builds the README and manual from built-in help using Pandoc and latexmk
- Fixes `--ignored-fields` overrides and allows `-i ""` to keep every field
- Fixes braced-title searches and uses the first author's surname for `mrlookup`
- Fixes bugs found during the Python 3 port
- Fixes metadata discovery when the script is launched through a symlink
- Groups command-line options by task in the help, README and manual
- Highlights duplicate warnings in purple and missed lookups in red
- Keeps the hidden debugging option available as `--debugging`
- Omits trailing Markdown link definitions from full terminal help
- Streamlined code using ChatGPT
- Ports the script to Python 3 and uses standard-library fuzzy title matching
- Preserves handwritten manual text and indents option descriptions
- Preserves retained entries and repairs local references during duplicate cleanup
- Prevents archive replacement if package files change during packaging
- Prints compact references with separate citation keys and 78-column wrapping
- Retains empty braced fields when deciding which lookups to skip
- Shows missed lookups in `--quiet` mode without separator bars
- Shows proposed AMS and arXiv changes in compact `--check` output
- Supports running without `bibupdate.ini` using embedded metadata
- Updates AMS lookups to use the current JSON APIs
- Updates documented defaults and known issues
- Uses Markdown for the README and changelog
- Uses `--duplicates` for cleanup and `-d`/`--list-duplicates` for listing

## Version 1.3

- Adds support for CTAN uploads
- Generates README files in reStructuredText, LaTeX and PDF from built-in help
- Improves option names

## Version 1.2 - July 2014

- Adds field wrapping to bibliography output
- Backs up existing non-empty output files
- Fixes the default for `--font-replace`

## Version 1.1 - July 2014 (initial PyPI release)

- Adds limited MathSciNet validation
- Adds options and improves documentation
- Improves reporting of differences between bibliography entries
- Optimises `mrlookup` searches and BibTeX field extraction
- Supports more general BibTeX field values

## Version 1.0 - July 2012 (initial release)
