# Rule Lint — User Guide

A linter for Evolution rule-engine equations. Catches the foot-guns documented
in [LANGUAGE_REFERENCE.md](LANGUAGE_REFERENCE.md) plus a range of
language-level mistakes, before they hit production.

Lives in [../rule_lint.py](../rule_lint.py). Pure Python 3.7+, no
dependencies beyond the standard library.

---

## Quick start

```bash
# Lint a single file
python3 ../rule_lint.py myrule.eq

# Lint many files (shell glob)
python3 ../rule_lint.py rules/*.eq

# With equation-type context
python3 ../rule_lint.py myrule.eq --eqtype Report

# Full check with test catalogue + include resolution
python3 ../rule_lint.py rules/*.eq \
    --eqtype TestRecalc \
    --include-path masks/ \
    --testlist tests.csv \
    --strict
```

Exit codes:
- `0` — clean (warnings may still be present)
- `1` — at least one error
- `2` — usage error (bad CLI, missing file, etc.)
- `3` — `--max-warnings` exceeded

---

## What it checks

### Language-level (always on)

| Code | Severity | Check |
|---|---|---|
| `LANG001` | error | Scientific notation literal — not supported (`1.5e-3`). Auto-fixable. |
| `LANG002` | error | `for(...)` loop — only `while` is available |
| `LANG003` | error | `break` / `continue` — not supported, use `exit;` |
| `LANG004` | error | User-defined `def`/`function`/`sub` — not supported, use `include_mask()` |
| `PARSE001` | error | Unclosed parenthesis after a call |

### Subroutine-level

| Code | Severity | Check |
|---|---|---|
| `UNKNOWN001` | warning | Unknown subroutine name; offers "did you mean?" suggestions |
| `ARG001` | error | Wrong number of arguments for a known subroutine |
| `EQTYPE001` | warning | Subroutine not valid in the current `--eqtype` context |
| `NOTE001` | info | Known foot-gun note (only with `--strict`) |

### Value-specific

| Code | Severity | Check |
|---|---|---|
| `VAL001` | warning | `test_set_status` / `test_clear_status` value ≠ 1 or 2 |
| `VAL002` | warning | `test_check_status` value ≠ 1/2/3 |
| `VAL003` | warning | `eqlogging` level ≠ 0/1/2 |
| `MISUSE001` | warning | `result_count()` called with test identifier instead of string |
| `MISUSE002` | warning | `include_mask()` called without a string literal |

### AST / structural

| Code | Severity | Check |
|---|---|---|
| `UPPER001` | warning | Uppercase identifier assigned that isn't in the `--testlist` and isn't a system variable. **Requires `--testlist`** (see note below). |
| `DEAD001` | warning | Statement after `exit;` in the same block (unreachable) |
| `EMPTY001` | warning | Empty `{ }` block — no statements |
| `UNUSED001` | warning | User variable assigned but never read |
| `USE001` | warning | User variable read before any assignment |

> **About UPPER001.** Per the rule language, `SODIUM = 5;` is a legitimate
> test result write (shorthand for `SODIUM[0][0] = 5;`). The linter can only
> tell a legitimate test write from a convention-violating user variable when
> it knows the test/panel catalogue. UPPER001 is therefore only active when
> you pass `--testlist FILE.csv`. Without it, the check is silently disabled
> to avoid false positives on every test assignment in the rule.

### Test catalogue (with `--testlist`)

| Code | Severity | Check |
|---|---|---|
| `TEST001` | warning | Uppercase identifier not in the test/panel CSV catalogue |

### Include resolution (with `--include-path`)

| Code | Severity | Check |
|---|---|---|
| `INC001` | error | `include_mask()` recursion depth exceeds 32 (engine limit) |
| `INC002` | warning | `include_mask()` target file not found in any include path |

### Auto-fix

| Code | Severity | Check |
|---|---|---|
| `FIX001` | info | Auto-fix applied or available |
| `IO001` | error | Could not read an input file |

To list these at any time: `python3 ../rule_lint.py --list-codes`
To explain one: `python3 ../rule_lint.py --explain DEAD001`

---

## Commands & options

### Input

```
FILE [FILE ...]            Rule files to lint. Multiple OK. With no files, reads stdin.
```

### Context

```
--eqtype TYPE              Equation-type context. One of:
                             TestRecalc, TestValidate, L1Validate, Analyser,
                             Request_Add, Request_Remove, Report, TestAccept,
                             Generic, AutoVal, Registration, RepDisp,
                             MBSCheck, MFA, Billing, Retrigger

--include-path DIR         Where to find include_mask() targets. Repeatable.
                             Resolves <DIR>/<name>, .eq, .mask, .rule extensions.

--testlist FILE.csv        CSV of valid test/panel mnemonics — see "Test catalogue
                             CSV format" below.

--strict                   Surface NOTE001 informational hints on known
                             foot-guns even when no other issue applies.
```

### Output

```
--format text|json|sarif   Output format. Default: text.
                             json   — flat array, one object per issue
                             sarif  — SARIF v2.1.0 for CI annotation

--no-colour                Disable ANSI colour in text output.

--quiet                    Print errors only; suppress warnings and info.
```

### CI gates

```
--max-warnings N           Exit 3 if total warning count > N.

--baseline FILE.json       Skip issues already recorded in FILE.json.
                             Useful when adopting the linter on a dirty
                             codebase — fix-as-you-go without exit code 1.

--update-baseline FILE     Write current issues to FILE.json as the new
                             baseline. Does not lint against a previous one.
                             Use this to refresh the baseline after a clean-up.
```

### Auto-fix

```
--fix                      Apply safe mechanical fixes in-place:
                             - Scientific notation → decimal literal
                             - Trailing whitespace stripped per line

--fix-dry-run              Show what --fix would change without writing files.
```

Auto-fix is intentionally conservative. Structural changes (e.g. `for` → `while`)
are not auto-fixed because they could change semantics. Apply with confidence;
review the diff if your team requires it.

### Discovery / help

```
--list-codes               Print all issue codes with descriptions and exit.
--explain CODE             Print explanation for a specific code and exit.
-h, --help                 Standard argparse help.
```

### Inline suppression

In rule source files:

```
/* lint: ignore CODE1,CODE2 */
```

Placed on the line with the offending code, or on the line directly above it,
suppresses those codes. Use `ALL` as a wildcard:

```
/* lint: ignore ALL */
problematic_legacy_call(some, weird, args);
```

---

## Test catalogue CSV format

When you pass `--testlist FILE.csv`, the linter validates every uppercase
identifier in the rule against this list, warning with `TEST001` for unknown
mnemonics. System identifiers (`AGE_DAYS`, `UR_DOB`, `LAB_GENSTRING1`,
`TESTSTATUS_DELTA`, etc.) are whitelisted automatically.

### Two supported shapes

**Multi-column with header:**

```csv
mnem,name,type,active
SODIUM,Sodium,test,1
POTASSIUM,Potassium,test,1
GLUCOSE,Glucose,test,1
ELECTROLYTES,Electrolyte panel,panel,1
LEGACY,Old test,test,0
```

- Required column: `mnem` (or `mnemonic`)
- Optional columns: `name`, `type`, `active`
- If `active` is present, rows with `active=0/false/no/n/<blank>` are excluded
- Header detection is automatic — the first row must contain `mnem` (case-insensitive)

**Single column (no header):**

```csv
SODIUM
POTASSIUM
GLUCOSE
```

Each non-blank line becomes a mnemonic. Lines beginning with `#` are treated
as comments and ignored.

### Generating the CSV

If you've got the test catalogue in the application database, you can dump it
with a tool of your choice (SQL query, config-export, etc.) into either of
the above shapes. The CSV is just a flat list — no special encoding needed.

---

## Common workflows

### Local development

```bash
# Lint as you edit
python3 ../rule_lint.py myrule.eq --eqtype TestRecalc --strict

# What does that warning actually mean?
python3 ../rule_lint.py --explain UPPER001
```

### Adopting the linter on a dirty codebase

```bash
# 1. Record the current set of issues as the baseline (one-off)
python3 ../rule_lint.py rules/*.eq \
    --eqtype TestRecalc \
    --testlist tests.csv \
    --update-baseline .rule_lint_baseline.json

# 2. From now on, CI only sees NEW issues
python3 ../rule_lint.py rules/*.eq \
    --eqtype TestRecalc \
    --testlist tests.csv \
    --baseline .rule_lint_baseline.json
```

Refresh the baseline whenever you've cleaned up a batch of historical issues.

### CI integration (GitHub Actions)

```yaml
- name: Lint rule equations
  run: |
    python3 ../rule_lint.py rules/*.eq \
        --eqtype TestRecalc \
        --testlist config/tests.csv \
        --baseline .rule_lint_baseline.json \
        --max-warnings 0 \
        --format sarif > rule_lint.sarif

- name: Upload SARIF
  if: always()
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: rule_lint.sarif
```

The SARIF upload gives you PR annotations on the affected lines, automatically.

### CI integration (GitLab)

```yaml
rule_lint:
  stage: test
  script:
    - python3 ../rule_lint.py rules/*.eq
        --eqtype TestRecalc
        --testlist config/tests.csv
        --format json > rule_lint.json
  artifacts:
    paths: [rule_lint.json]
```

### Pre-commit hook

`.git/hooks/pre-commit`:

```bash
#!/bin/sh
files=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.(eq|rule|mask)$')
if [ -z "$files" ]; then
    exit 0
fi
python3 ../rule_lint.py $files --quiet
```

This runs the linter on staged rule files and blocks the commit on errors.

### Bulk cleanup workflow

```bash
# Dry-run all auto-fixes across the tree
python3 ../rule_lint.py rules/*.eq --fix-dry-run

# If it looks safe, apply
python3 ../rule_lint.py rules/*.eq --fix

# Re-lint to see what's left
python3 ../rule_lint.py rules/*.eq --eqtype TestRecalc
```

---

## The catalogue (`rule_catalogue.py`)

The list of valid subroutines, their parameter counts, and equation-type
restrictions is **auto-generated** from `src/eq.c`. Don't edit it by hand.

### Regenerate when `eq.c` changes

```bash
python3 ../gen_rwf_catalogue.py
# Wrote 352 subroutines to ../rule_catalogue.py
```

This parses the RWF dispatch table at `src/eq.c:19144-19511` and writes
`../rule_catalogue.py`. The linter imports the result at startup.

### CI: detect a stale catalogue

```bash
python3 ../gen_rwf_catalogue.py --print > /tmp/rule_catalogue.new
diff -q /tmp/rule_catalogue.new ../rule_catalogue.py || {
    echo "rule_catalogue.py is out of date — regenerate"
    exit 1
}
```

Drop this in CI to catch unsynced catalogue files.

### Catalogue contents

Each subroutine entry looks like:

```python
'jump_to_test': (1, 0x00B3, 'Only valid in Interactive eq types — ...'),
#  (arg_count, eqtype_mask, foot_gun_note)
```

The mask is an OR of `EQTYPE_*` bit values from `pathology.h`. `0xFFFF`
means "all contexts." `mask_to_pretty()` in the linter converts these to
human-readable lists like `"TestRecalc, TestValidate, ..."`.

Notes are hand-curated in `../gen_rwf_catalogue.py` (the `NOTES` dict).
Add new entries there as you discover new foot-guns; re-running the
generator preserves them across regenerations.

---

## Issue code reference

Full descriptions:

```bash
python3 ../rule_lint.py --list-codes
python3 ../rule_lint.py --explain DEAD001
```

### Summary table

| Code | Severity | One-liner |
|---|---|---|
| `LANG001` | error | Scientific notation literal — use a plain decimal |
| `LANG002` | error | `for` loop — use `while` |
| `LANG003` | error | `break`/`continue` — use `exit;` |
| `LANG004` | error | User-defined function — use `include_mask()` |
| `PARSE001` | error | Unclosed parenthesis after a call |
| `ARG001` | error | Wrong argument count |
| `INC001` | error | `include_mask()` recursion exceeded 32 |
| `IO001` | error | Could not read input file |
| `UNKNOWN001` | warning | Unknown subroutine (with suggestions) |
| `EQTYPE001` | warning | Subroutine not valid in this --eqtype |
| `VAL001` | warning | `test_set_status` value ≠ 1/2 |
| `VAL002` | warning | `test_check_status` value ≠ 1/2/3 |
| `VAL003` | warning | `eqlogging` level ≠ 0/1/2 |
| `MISUSE001` | warning | `result_count()` with test ident, not string |
| `MISUSE002` | warning | `include_mask()` without string literal |
| `INC002` | warning | `include_mask()` target not found |
| `TEST001` | warning | Identifier not in --testlist |
| `UPPER001` | warning | Uppercase identifier assigned, not in --testlist (requires --testlist) |
| `DEAD001` | warning | Unreachable code after `exit;` |
| `EMPTY001` | warning | Empty `{ }` block |
| `UNUSED001` | warning | Variable assigned, never read |
| `USE001` | warning | Variable read before assigned |
| `NOTE001` | info | Foot-gun note (--strict only) |
| `FIX001` | info | Auto-fix applied or available |

---

## Examples

### A rule with several issues

Input (`bad.eq`):

```c
/* example with several issues */

MYVAR = 1.5e-3;            /* UPPER001 + LANG001 */
strn_len("hi");            /* UNKNOWN001 with "did you mean strlen?" */
result_count(SODIUM);      /* MISUSE001 */
test_set_status(GLUCOSE, 7); /* VAL001 */
if (SODIUM gt 100) {
    exit;
    val = 5;               /* DEAD001 */
}
unused_var = 10;           /* UNUSED001 */
```

Run:

```bash
python3 ../rule_lint.py bad.eq --no-colour
```

Output:

```
bad.eq:3:1: warning [UPPER001] `MYVAR` looks like a user variable but UPPERCASE is reserved ...
bad.eq:3:9: error [LANG001] Scientific notation `1.5e-3` is not supported. Use a plain decimal.
bad.eq:4:1: warning [UNKNOWN001] Unknown subroutine `strn_len` — did you mean: strlen?
bad.eq:5:1: warning [MISUSE001] `result_count` takes a comma-separated STRING ...
bad.eq:6:1: warning [VAL001] `test_set_status` only accepts 1 (`<`) or 2 (`>`); value 7 is silently ignored
bad.eq:9:5: warning [DEAD001] Statement after `exit;` is unreachable.
bad.eq:11:1: warning [UNUSED001] Variable `unused_var` is assigned but never read

1 file(s); 1 error(s), 6 warning(s), 0 info
```

### Auto-fixing the scientific notation

```bash
python3 ../rule_lint.py bad.eq --fix --no-colour
```

`MYVAR = 1.5e-3` becomes `MYVAR = 0.0015` (the LANG001 error vanishes).
The other warnings remain — they aren't safely auto-fixable.

### Including a shared mask

`main.eq`:
```c
include_mask("shared_checks");
output_results(80, 50, 0, 9, 0, 1, "R", "", 40, SODIUM);
```

`masks/shared_checks.eq`:
```c
if (SODIUM lt 100) {
    set_abnormal(SODIUM);
}
typo_fn();   /* this will be flagged via include resolution */
```

Run with include resolution:

```bash
python3 ../rule_lint.py main.eq --include-path masks/ --eqtype Report
```

Output:

```
main.eq:3:1: warning [UNKNOWN001] Unknown subroutine `typo_fn` — possible typo ...
              (via include_mask: shared_checks)
```

The include chain is reported so you know which mask the issue came from.

### Suppressing a known-OK issue

```c
/* legacy interface — known to use an UPPERCASE convention here */
/* lint: ignore UPPER001 */
MYLEGACY = 1;
```

Or same-line:

```c
MYLEGACY = 1;  /* lint: ignore UPPER001 */
```

---

## Troubleshooting

### "warning: rule_catalogue.py not found"

```
warning: rule_catalogue.py not found — run gen_rwf_catalogue.py first.
Subroutine checks will be skipped.
```

You haven't generated the catalogue. Run:

```bash
python3 ../gen_rwf_catalogue.py
```

This is a one-off; re-run only when `src/eq.c` changes.

### Too many `UNKNOWN001` warnings

Your `eq.c` is probably newer than the generated catalogue. Re-run the
generator. If the names are genuinely typos, fix them; if they're
site-specific subroutines not in the standard build, add them to `NOTES`
in `gen_rwf_catalogue.py` and re-run.

### Too many `TEST001` warnings

Your `--testlist` is incomplete. Either expand the CSV or remove `--testlist`
to disable the check entirely.

### `UPPER001` warnings on legitimate test result writes

If you see UPPER001 flagging `SODIUM = 5;` and other normal test result
writes, the linter doesn't know `SODIUM` is a real test. UPPER001 requires
a `--testlist FILE.csv` of valid mnemonics to make this distinction. Without
the testlist, UPPER001 is disabled.

If you've already supplied `--testlist` and you're still seeing warnings on
real test names, the testlist is missing those mnemonics — add them, or
suppress per-line with `/* lint: ignore UPPER001 */`.

### False positives on `UNUSED001`

Some variables genuinely are used in ways the shallow parser misses
(e.g. inside very complex nested expressions). The parser deliberately
ignores common scratch names `i`, `j`, `k`, `n` to reduce noise.

If a variable is being flagged incorrectly, suppress it inline:

```c
some_var = 10;  /* lint: ignore UNUSED001 */
```

### "EMPTY001" on intentionally empty blocks

If a block is deliberately empty (e.g. placeholder for future logic), use
the suppression directive on the line above:

```c
/* lint: ignore EMPTY001 */
if (some_cond) { }
```

### `--fix` rewrote my file in a way I didn't want

`--fix` only applies these mechanical changes:

1. Scientific notation literals → decimal literals
2. Trailing whitespace stripped

Both are semantically neutral. If you don't trust them, use `--fix-dry-run`
first to preview the changes, or version-control your rules and review the
diff after `--fix`.

### Issue codes that look new

The catalogue evolves. Run `--list-codes` for the current set. If a code
appears in output that's not in `--list-codes`, the version mismatch is
between the linter and the doc — pull the latest tree.

### Exit code 3 in CI

`--max-warnings N` exceeded. Either:
- Tighten the rules until warning count fits
- Increase N
- Capture the current set of warnings as a baseline with `--update-baseline`
  and switch the build to `--baseline`

---

## Workflow XLSX importer

The GUI (`rule_lint_gui.py`) can import a structured workflow spreadsheet and
generate draft `.eq` rule-engine files from it. Available under **File →
Import Workflow XLSX…**. To get a starter template: **File → Save Workflow CSV
Template…**.

The importer is GUI-only and reads `.xlsx` or `.csv` with the same column
shape. Implementation lives in [`../rule_lint_xlsx.py`](../rule_lint_xlsx.py).

### Spreadsheet columns

Header row is required. Column order is flexible; the importer matches by
name (case-insensitive).

| Column | Required | Purpose |
|---|---|---|
| `ERN` | yes | Rule identifier, e.g. `B.00521.7.01`. Appears in the generated comment. |
| `Department` | yes | `BIOCHM`, `HAEM`, `MICRO`, … Drives the file split for Modify rules. |
| `Workflow` | no | Free-text label, e.g. `AFP`. Appears in the generated comment. |
| `Action Category` | yes | `Req Add`, `Req Delete`, or `Modify`. Drives the output filename. |
| `Trigger Test` | yes | Primary test mnemonic used in the `if` condition. |
| `Trigger Op` | no | See operator list below. Defaults to `ordered`. |
| `Trigger Value` | depends | Numeric value for comparison / range operators. |
| `Extra Condition` | no | `;`-separated extra clauses ANDed into the `if`. See DSL below. |
| `Action Target` | depends | For Req Add/Delete: a test mnemonic. For Modify: `verb:args`. |
| `Notes` | no | Free text, copied to the generated comment. |

### Output file split

| Action Category | Output file |
|---|---|
| Req Add | `requests_add.eq` (all Req Add rows) |
| Req Delete | `requests_delete.eq` (all Req Delete rows) |
| Modify | `modify_<DEPARTMENT>.eq` (one file per Department) |

Each row becomes one `if … then … endif` block, headed by a `/* ERN: … */`
comment so reviewers can trace generated code back to the spreadsheet row.

### Trigger operators

| Trigger Op | Generated condition |
|---|---|
| `ordered` (default) | `test_ordered("TEST")` |
| `not_ordered` | `not test_ordered("TEST")` |
| `resulted` | `test_result("TEST") != ""` |
| `not_resulted` | `test_result("TEST") == ""` |
| `numeric` | resulted **and** numeric value > 0 |
| `<` `<=` `>` `>=` `=` | `ee_value("TEST",0,0) <op> VALUE` |
| `range` | both bounds inclusive, Trigger Value `lo-hi` |
| `critical_high` / `critical_low` | `h1flagmatch("TEST","HH")` / `"LL"` |
| `flag_h` / `flag_l` / `flag_x` | `h1flagmatch("TEST","H")` etc. |

### Extra-condition DSL

Extra Condition is one or more `prefix:argument` clauses separated by `;`.
All are ANDed into the trigger.

| Prefix | Example | Generated |
|---|---|---|
| `ordered` | `ordered:CREAT` | `test_ordered("CREAT")` |
| `resulted` | `resulted:UREA` | `test_result("UREA") != ""` |
| `value` | `value:K>=6.2` | `ee_value("K",0,0) >= 6.2` |
| `range` | `range:TSH=0.5-4.0` | both bounds inclusive |
| `critical` | `critical:NA` | `h1flagmatch("NA","HH")` |
| `flag` | `flag:K=H` | `h1flagmatch("K","H")` |
| `age` | `age:>16` | `AGE_DAYS > 5840` (years × 365) |
| `sex` | `sex:F` | `SEX == "F"` |
| `inpatient` | `inpatient:yes` | `INPATIENT == 1` |
| `facility` | `facility:GP` | `FACILITY_TYPE == "GP"` |
| `coded` | `coded:CORTREAT=CORPRE` | `test_result("CORTREAT") == "CORPRE"` |

### Modify verbs (Action Target for Modify rows)

Multiple verbs can be chained with `;`.

| Verb | Example | Emits |
|---|---|---|
| `validate` | `validate:TSH` | `validate("TSH");` |
| `list` | `list:RP->TELEPHONE` | `listinsert_test("RP","TELEPHONE");` |
| `unlist` | `unlist:RP->TELEPHONE` | `listremove_test("RP","TELEPHONE");` |
| `note` | `note:CORT=CORTPREN` | `add_testnote("CORT", codedcomment("CORTPREN"));` |
| `value` | `value:K=5.2` | `test_setvalue("K", 5.2);` |
| `coded` | `coded:STATUS=PEND` | `test_setvalue("STATUS", "PEND");` |
| `calc` | `calc:EGFR=ee_value("CREAT",0,0)*0.85` | `test_setvalue("EGFR", <expr>);` |
| `request_add` | `request_add:AFPROCHE` | `add_request("AFPROCHE");` |
| `request_delete` | `request_delete:CICLO_REFLEX` | `remove_request("CICLO_REFLEX");` |

### Reviewing generated code

Generated files are **drafts**, not final. After import:

1. The Workflow Import Summary dialog lists files written and any
   row-level errors/warnings.
2. Open each `.eq` in the main linter (**File → Open Rule File…**) and
   run **Run Lint** to catch any structural issues.
3. Review each `/* ERN: … */` block against the source row before merging
   into the engine.

---

## Architecture

For people extending the linter.

### Three files

| File | Role |
|---|---|
| [`../rule_lint.py`](../rule_lint.py) | Main linter — tokenisation, shallow AST, all check passes, CLI |
| [`../rule_catalogue.py`](../rule_catalogue.py) | Auto-generated catalogue of 352 subroutines |
| [`../gen_rwf_catalogue.py`](../gen_rwf_catalogue.py) | Parser for `src/eq.c` that emits `rule_catalogue.py` |

### Lint pipeline (per file)

```
1. Read source
2. Build inline-suppression map (/* lint: ignore */ directives)
3. Strip comments
4. Run regex-based lints:
   - Language-level (scientific notation, for, break, def)
   - Subroutine calls (arg count, eq-type, foot-guns)
   - Test/panel identifier validation (if --testlist)
5. Parse shallow AST (statements + block structure)
6. Run AST-based lints:
   - DEAD001 (post-exit), EMPTY001, UNUSED001, USE001, UPPER001
7. Apply suppression map (drop suppressed issues)
8. Resolve include_mask() recursively (if --include-path)
9. Sort, format, output
```

### Adding a new check

1. Add a code + description to `ISSUE_CODES` in `rule_lint.py`.
2. Append the check logic — either in `lint()` (text/regex-based), in
   `_ast_lints()` (statement-level), or in `_specific_arg_checks()`
   (per-subroutine argument validation).
3. Emit an `Issue(severity, line, col, code, message, include_chain=chain)`.

The harness handles suppression, sorting, formatting, exit codes, and CI
output automatically — just emit issues.

### Adding a foot-gun note

Edit the `NOTES` dict at the top of `gen_rwf_catalogue.py`, then re-run
the generator. The note is preserved across regenerations.

### Adding an auto-fix

Add a `_fix_<name>(text) -> (new_text, fixes)` function in `rule_lint.py`
and append it to the `SAFE_FIXERS` list. Apply only mechanical, semantically
neutral changes — anything that could alter behaviour belongs in a separate
explicit refactor tool, not `--fix`.

---

## Related references

- [LANGUAGE_REFERENCE.md](LANGUAGE_REFERENCE.md) — the language reference; what each subroutine does
