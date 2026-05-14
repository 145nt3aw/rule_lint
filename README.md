# Rule Lint

A linter for **Evolution rule-engine equations** — catches authoring mistakes,
language-level errors, and known foot-guns before they hit production.

Available as:

- A **command-line tool** for developers and CI ([docs/USER_GUIDE.md](docs/USER_GUIDE.md))
- A **standalone GUI app** for end users on macOS and Windows 11
  ([docs/PACKAGING.md](docs/PACKAGING.md))

Zero runtime dependencies — pure Python 3.7+ with stdlib only. The packaged
binaries bundle their own Python.

---

## Quick start (developers)

```bash
# Lint a single rule file
python3 rule_lint.py myrule.eq --eqtype Report

# Lint a directory, validate against your test catalogue
python3 rule_lint.py rules/*.eq \
    --eqtype TestRecalc \
    --testlist tests.csv

# List every issue code the linter knows about
python3 rule_lint.py --list-codes
```

Full reference: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Quick start (end users)

Download a release binary for your platform and run it:

| Platform | Download | Install |
|---|---|---|
| **macOS** | `RuleLint-macos.zip` | Unzip, drag `RuleLint.app` to Applications |
| **Windows 11** | `RuleLint.exe` | Double-click to launch |

The first launch may show a security warning — see [docs/PACKAGING.md](docs/PACKAGING.md#end-user-instructions-what-to-put-on-the-download-page) for the workaround.

---

## Workflow spreadsheet importer (GUI)

The GUI also has a **File → Import Workflow XLSX…** action that turns a
structured spreadsheet of rules into draft `.eq` files, grouped by
**Req Add** / **Req Delete** / **Modify** (the last split by Department).
See [docs/USER_GUIDE.md](docs/USER_GUIDE.md#workflow-xlsx-importer) for the
column schema and the trigger/action DSL.

## What it checks

24 distinct issue codes across these categories:

| Category | Examples |
|---|---|
| **Language** | scientific notation, `for` loops, `break`/`continue`, user-defined functions |
| **Subroutines** | unknown names (with "did you mean?" suggestions), wrong argument count, equation-type mismatches |
| **Foot-guns** | `delta()` misuse, `test_set_status` invalid values, `result_count` with test identifier |
| **Structure** | unreachable code after `exit;`, empty blocks, unused variables, used-before-set |
| **Test names** | identifier not in supplied catalogue (with suggestions) |
| **Includes** | unresolved `include_mask()` targets, recursion depth |

All codes documented with `--list-codes` or in
[docs/USER_GUIDE.md](docs/USER_GUIDE.md#issue-code-reference).

## CI integration

```bash
# JSON for custom tooling
python3 rule_lint.py rules/*.eq --format json > out.json

# SARIF for GitHub / GitLab annotations
python3 rule_lint.py rules/*.eq --format sarif > out.sarif

# Adopt on a dirty codebase: record current issues, only flag new ones
python3 rule_lint.py rules/*.eq --update-baseline .baseline.json
python3 rule_lint.py rules/*.eq --baseline .baseline.json --max-warnings 0
```

## Auto-fixing

A conservative `--fix` mode applies only mechanically-safe changes:

- Scientific notation literals → plain decimals
- Trailing whitespace stripped

```bash
python3 rule_lint.py myrule.eq --fix-dry-run    # preview
python3 rule_lint.py myrule.eq --fix            # apply in-place
```

Structural changes (e.g. `for` → `while`) are **not** auto-fixed — they
require a human eye.

---

## Repository layout

```
.
├── rule_lint.py              CLI linter (the main tool)
├── rule_lint_gui.py          Tkinter GUI wrapper
├── rule_catalogue.py         Auto-generated catalogue of ~352 subroutines
├── gen_rwf_catalogue.py      Generator for the catalogue (reads src/eq.c)
├── rule_lint.spec            PyInstaller build spec
├── build_release.py          One-command build script
├── docs/
│   ├── USER_GUIDE.md         Full CLI reference + examples
│   ├── PACKAGING.md          How to build + distribute binaries
│   └── LANGUAGE_REFERENCE.md The rule-engine language itself
└── .github/workflows/build.yml   CI for macOS + Windows binaries
```

## Catalogue maintenance

The list of valid subroutines is auto-generated from the Evolution engine's
source (`src/eq.c`). Regenerate after engine changes:

```bash
# With src/eq.c sitting beside this checkout:
python3 gen_rwf_catalogue.py --source /path/to/src/eq.c
```

The generated `rule_catalogue.py` ships with this repo so users can lint
without needing the engine source. If you maintain a fork of the engine
locally, regenerate periodically to keep the linter in sync.

---

## Building binaries

PyInstaller does not cross-compile — build each platform on a host of that
platform.

```bash
pip install pyinstaller
python3 build_release.py
# Output appears in dist/
```

Or push a `v*` tag to trigger the CI workflow which builds both macOS and
Windows binaries in parallel and attaches them to the GitHub Release.

Full instructions: [docs/PACKAGING.md](docs/PACKAGING.md).

---

## License

MIT — see [LICENSE](LICENSE).
