#!/usr/bin/env python3
"""
rule_lint.py — Linter for Evolution rule-engine equations.

Detects:
- Unknown subroutine names (with "did you mean?" suggestions)
- Wrong argument count
- Equation-type violations (e.g. jump_to_test in Report context)
- Common foot-guns documented in RULE_ENGINE_SUBROUTINES.md
- Language-level limitations (for loops, scientific notation, etc.)
- Unknown test/panel mnemonics (when a --testlist is supplied)
- Uppercase user variables (forbidden namespace)
- Dead code after `exit;`

Catalogue is auto-generated from src/eq.c — regenerate with:
    python3 support/gen_rwf_catalogue.py

Usage:
    rule_lint.py [OPTIONS] FILE [FILE ...]
    rule_lint.py rules/*.eq --eqtype Report --testlist tests.csv
    cat my_rule.txt | rule_lint.py --eqtype Report

Common options:
    --eqtype TYPE              Equation-type context (enables eq-type checks)
    --include-path DIR         Where to find include_mask() targets (repeatable)
    --testlist FILE.csv        Validate uppercase identifiers against this list
    --format text|json|sarif   Output format (default: text)
    --strict                   Surface informational notes on known foot-guns
    --quiet                    Suppress warnings/info; only print errors
    --max-warnings N           Exit code 3 if warning count exceeds N
    --baseline FILE.json       Only report issues new since the baseline
    --update-baseline FILE     Write current issues as the new baseline
    --list-codes               Print all linter issue codes and exit
    --explain CODE             Print explanation for a specific code and exit

Exit codes:
    0 — clean (or only suppressed warnings/info)
    1 — at least one error
    2 — usage error (bad CLI args, missing file, etc.)
    3 — --max-warnings exceeded

Inline suppression directives (in comments):
    /* lint: ignore CODE1,CODE2 */
    Placed on a line or on the line above, suppresses those codes on
    the directive's line AND the next non-blank line.

See RULE_ENGINE_SUBROUTINES.md for full reference.
"""

import argparse
import csv
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Tuple, Set, Dict


# ---------------------------------------------------------------------------
# Load catalogue (auto-generated; fallback to empty if missing)
# ---------------------------------------------------------------------------

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    from rule_catalogue import SUBROUTINES, EQTYPE_BITS, EQTYPE_ALL
except ImportError:
    print("warning: rule_catalogue.py not found — run gen_rwf_catalogue.py first. "
          "Subroutine checks will be skipped.", file=sys.stderr)
    SUBROUTINES = {}
    EQTYPE_BITS = {}
    EQTYPE_ALL = 0xFFFF


# ---------------------------------------------------------------------------
# Issue-code catalogue (used by --list-codes and --explain)
# ---------------------------------------------------------------------------

ISSUE_CODES = {
    "LANG001":  ("error",   "Scientific notation literal (e.g. 1.5e-3) — not supported. Use a plain decimal."),
    "LANG002":  ("error",   "`for` loop — not supported. Only `while` is available."),
    "LANG003":  ("error",   "`break`/`continue` — not supported. Use `exit;` to terminate the whole equation."),
    "LANG004":  ("error",   "User-defined function/subroutine — not supported. Use `include_mask()` for reuse."),
    "PARSE001": ("error",   "Unclosed parenthesis after a subroutine call."),
    "ARG001":   ("error",   "Wrong number of arguments for a known subroutine."),
    "EQTYPE001":("warning", "Subroutine may not be valid in the current --eqtype context."),
    "UNKNOWN001":("warning","Unknown subroutine name — possibly a typo. Suggestions are offered when available."),
    "VAL001":   ("warning", "test_set_status / test_clear_status only accept 1 (`<`) or 2 (`>`); other values silently ignored."),
    "VAL002":   ("warning", "test_check_status only accepts 1/2/3; other values return 0."),
    "VAL003":   ("warning", "eqlogging level should be 0 (info), 1 (warning), or 2 (error)."),
    "MISUSE001":("warning", "result_count() takes a comma-separated STRING, not a test identifier."),
    "MISUSE002":("warning", "include_mask() expects a string-literal mask name."),
    "INC001":   ("error",   "include_mask() recursion depth exceeded engine limit of 32."),
    "INC002":   ("warning", "include_mask() target file not found in any --include-path."),
    "IO001":    ("error",   "Could not read an input file (permissions, missing, etc.)."),
    "TEST001":  ("warning", "Uppercase identifier not in the --testlist catalogue (likely test/panel typo)."),
    "UPPER001": ("warning", "Uppercase identifier assigned that isn't in the --testlist and isn't a system variable. Requires --testlist to be supplied."),
    "DEAD001":  ("warning", "Unreachable code after `exit;` in the same block."),
    "UNUSED001":("warning", "User variable assigned but never read."),
    "USE001":   ("warning", "User variable read before any assignment."),
    "EMPTY001": ("warning", "Empty block — `if`, `else`, or `while` with no body."),
    "NOTE001":  ("info",    "Informational note about a subroutine's known foot-gun (only with --strict)."),
    "FIX001":   ("info",    "An auto-fix was applied or is available (--fix mode)."),
}


# Composite eq-type tags exposed via --eqtype.
# These map to single EQTYPE_* bits; check against subroutine masks via AND.
EQ_TYPE_ALIASES = {
    "TestRecalc":      "EQTYPE_TESTRECALC",
    "TestValidate":    "EQTYPE_TESTVALIDATE",
    "L1Validate":      "EQTYPE_L1VALIDATE",
    "Analyser":        "EQTYPE_ANALYSER",
    "Request_Add":     "EQTYPE_REQUEST_ADD",
    "Request_Remove":  "EQTYPE_REQUEST_RM",
    "Report":          "EQTYPE_REPORT",
    "TestAccept":      "EQTYPE_TESTACCEPT",
    "Generic":         "EQTYPE_GENERIC",
    "AutoVal":         "EQTYPE_AUTOVAL",
    "Registration":    "EQTYPE_REGISTRATION",
    "RepDisp":         "EQTYPE_REPDISP",
    "MBSCheck":        "EQTYPE_MBSCHECK",
    "MFA":             "EQTYPE_MFA",
    "Billing":         "EQTYPE_BILLING",
    "Retrigger":       "EQTYPE_RETRIGGER",
}


def eqtype_matches(declared_mask: int, user_eqtype: str) -> bool:
    """Return True if the subroutine's declared mask covers the user's context."""
    if user_eqtype not in EQ_TYPE_ALIASES:
        return True
    user_bit_name = EQ_TYPE_ALIASES[user_eqtype]
    user_bit = EQTYPE_BITS.get(user_bit_name, 0)
    return bool(declared_mask & user_bit)


def mask_to_pretty(mask: int) -> str:
    """Render a mask as a human-readable list of valid contexts."""
    if mask == EQTYPE_ALL:
        return "All"
    if mask == 0:
        return "None"
    names = []
    for tag, bit_name in EQ_TYPE_ALIASES.items():
        bit = EQTYPE_BITS.get(bit_name, 0)
        if mask & bit:
            names.append(tag)
    return ", ".join(sorted(names))


# ---------------------------------------------------------------------------
# Built-in identifiers (never warned as unknown tests)
# Source: RULE_ENGINE_SUBROUTINES.md §"Built-in Identifiers"
# ---------------------------------------------------------------------------

SYSTEM_IDENTIFIERS = {
    # Read-only system variables
    "AGE_DAYS", "SEX", "LOCATION", "WARD", "DOCTOR", "CONSULTANT",
    "CLINNOTE", "CATEGORY", "ALERT", "DIAGNOSIS", "FILEPREFIX",
    "SAMPLE_VOLUME", "SAMPLE_PERIOD",
    "GSI_MODE", "GSI_REGSTATUS", "QPS_REGSTATUS",
}

# UR_ prefixed fields
SYSTEM_IDENTIFIERS.update({
    f"UR_{f}" for f in (
        "DOB", "NAME", "GNAME", "ADDRESS1", "ADDRESS2",
        "SUBURB", "POSTCODE", "SEX", "ETHNICITY", "FINCAT",
        "SSCLIENT", "SSSAMPLES", "SSCRISP", "SSPOLDAT", "SSCRMCLS",
    )
})
SYSTEM_IDENTIFIERS.update(f"UR_GENFLAG{i}" for i in range(1, 9))
SYSTEM_IDENTIFIERS.update(f"UR_GENNUMBER{i}" for i in range(1, 9))
SYSTEM_IDENTIFIERS.update(f"UR_GENSTRING{i}" for i in range(1, 9))

# LAB_ prefixed
SYSTEM_IDENTIFIERS.update(f"LAB_GENFLAG{i}" for i in range(1, 9))
SYSTEM_IDENTIFIERS.update(f"LAB_GENNUMBER{i}" for i in range(1, 9))
SYSTEM_IDENTIFIERS.update(f"LAB_GENSTRING{i}" for i in range(1, 9))

# Test status flags (used in checkbits etc.)
SYSTEM_IDENTIFIERS.update({
    "TESTSTATUS_MODIFIED", "TESTSTATUS_VALIDATED", "TESTSTATUS_STATSDONE",
    "TESTSTATUS_OVERDUE", "TESTSTATUS_FLAGGED", "TESTSTATUS_BILLINGDONE",
    "TESTSTATUS_RESULT", "TESTSTATUS_HOLD", "TESTSTATUS_CALCULATED",
    "TESTSTATUS_DELTA", "TESTSTATUS_LAB_USE_ONLY", "TESTSTATUS_CANCELLED",
    "TESTSTATUS_LESS_THAN", "TESTSTATUS_GREATER_THAN", "TESTSTATUS_ACCEPTED",
    "TESTSTATUS_SUSPEND", "TESTSTATUS_HIGH", "TESTSTATUS_LOW",
    "TESTSTATUS_CRIT_HIGH", "TESTSTATUS_CRIT_LOW", "TESTSTATUS_CONTROL",
    "TESTSTATUS_COMPLETED", "TESTSTATUS_SUPPRESSFMT", "TESTSTATUS_DELETED",
    "TESTSTATUS_MANDATORY", "TESTSTATUS_TRFLAG", "TESTSTATUS_FLAGS",
})


# ---------------------------------------------------------------------------
# CSV test/panel list
# ---------------------------------------------------------------------------

def load_testlist(path: str) -> Tuple[Set[str], Dict[str, str]]:
    """Load a CSV of valid test/panel mnemonics.

    Supports:
      - Single-column CSV (each line is a mnemonic, no header required)
      - Multi-column CSV with header. Required header: 'mnem' (or 'mnemonic').
        Optional headers: 'type' (test/panel/...), 'active' (1/0/true/false).
        If 'active' is present, only active=1/true rows are included.

    Returns (set_of_mnemonics, mnem_to_type_dict).
    """
    mnems: Set[str] = set()
    types: Dict[str, str] = {}

    with open(path, newline="") as f:
        # Sniff for header
        first = f.readline()
        f.seek(0)
        has_header = False
        if first:
            first_lower = first.lower().strip()
            # Header detection: contains 'mnem' (or 'mnemonic')
            if "mnem" in first_lower or "mnemonic" in first_lower:
                has_header = True

        if has_header:
            reader = csv.DictReader(f)
            # Normalise header names to lowercase
            for row in reader:
                row = {(k or "").strip().lower(): (v or "").strip()
                       for k, v in row.items()}
                mnem = row.get("mnem") or row.get("mnemonic") or ""
                if not mnem:
                    continue
                active_field = row.get("active")
                if active_field is not None:
                    if active_field.lower() in ("0", "false", "no", "n", ""):
                        continue
                mnems.add(mnem.upper())
                if row.get("type"):
                    types[mnem.upper()] = row["type"].lower()
        else:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Single-column: just take the first comma-separated field
                tok = line.split(",")[0].strip()
                if tok:
                    mnems.add(tok.upper())

    return mnems, types


# ---------------------------------------------------------------------------
# Issue model
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    severity: str           # "error" | "warning" | "info"
    line: int
    column: int
    code: str
    message: str
    include_chain: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tokenisation / parsing
# ---------------------------------------------------------------------------

def strip_comments(text: str) -> str:
    out = []
    i = 0
    in_comment = False
    while i < len(text):
        if not in_comment and text[i:i + 2] == "/*":
            in_comment = True
            out.append("  ")
            i += 2
            continue
        if in_comment and text[i:i + 2] == "*/":
            in_comment = False
            out.append("  ")
            i += 2
            continue
        if in_comment:
            out.append("\n" if text[i] == "\n" else " ")
        else:
            out.append(text[i])
        i += 1
    return "".join(out)


def strip_string_contents(text: str) -> str:
    """Replace the contents of string literals with spaces, keeping the quote
    characters in place. Used before identifier scanning so we don't flag
    `R` inside `"R"` as an unknown test/panel.

    Preserves line numbers and column offsets — every character is replaced
    1-for-1 except that newlines inside strings are kept.
    """
    out = []
    i = 0
    in_str = False
    quote = ""
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < len(text):
                # Escaped character — replace both with spaces
                out.append(" " if ch != "\n" else "\n")
                out.append(" " if text[i + 1] != "\n" else "\n")
                i += 2
                continue
            if ch == quote:
                out.append(ch)
                in_str = False
            else:
                out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


CALL_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.DOTALL)
IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")


def find_matching_paren(text: str, open_idx: int) -> int:
    depth = 0
    in_str = False
    quote = ""
    i = open_idx
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == quote:
                in_str = False
        elif ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_top_level_args(args_str: str) -> List[str]:
    args = []
    cur = []
    depth = 0
    in_str = False
    quote = ""
    for ch in args_str:
        if in_str:
            cur.append(ch)
            if ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            cur.append(ch)
            continue
        if ch in "([{":
            depth += 1
            cur.append(ch)
            continue
        if ch in ")]}":
            depth -= 1
            cur.append(ch)
            continue
        if ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    rest = "".join(cur).strip()
    if rest:
        args.append(rest)
    return args


def line_col_of(text: str, idx: int) -> Tuple[int, int]:
    line = 1
    last_nl = -1
    for i in range(min(idx, len(text))):
        if text[i] == "\n":
            line += 1
            last_nl = i
    col = idx - last_nl
    return (line, col)


CONTROL_KEYWORDS = {
    "if", "while", "elif", "else", "return",
    "for", "do", "switch", "and", "or", "not",
    "eq", "ne", "gt", "ge", "lt", "le",
    "exit",
}

# Reserved word identifiers that aren't subroutines but shouldn't be flagged
RESERVED_WORDS = CONTROL_KEYWORDS | {"and", "or", "not", "eq", "ne", "gt", "ge", "lt", "le", "exit"}


# ---------------------------------------------------------------------------
# Shallow statement-level parser
#
# We don't parse expressions in full — we identify statement boundaries and
# block structure so later lints can reason about reachability, block scope,
# and variable use without regex acrobatics. Within a statement we still keep
# a slice of source text for the existing call/identifier scans.
#
# Statement shapes recognised:
#   Block:     { stmt ; stmt ; ... }
#   If:        if ( <cond_text> ) <stmt>          (optional `else <stmt>`)
#   While:     while ( <cond_text> ) <stmt>
#   Exit:      exit ;
#   Assign:    <ident>[<sub>] ... = <expr> ;
#   ExprStmt:  <expr> ;          (typically a function call)
# ---------------------------------------------------------------------------

@dataclass
class Stmt:
    kind: str                 # "block" | "if" | "while" | "exit" | "assign" | "expr"
    start: int                # char index in source (post-comment-strip)
    end: int                  # char index of terminating `;` or `}`
    text: str = ""            # slice of source for this statement
    cond_text: str = ""       # for if/while
    target: str = ""          # for assign — the LHS identifier
    children: List["Stmt"] = field(default_factory=list)
    else_branch: Optional["Stmt"] = None


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _match_ident_at(text: str, i: int) -> Tuple[Optional[str], int]:
    if i >= len(text) or not (text[i].isalpha() or text[i] == "_"):
        return None, i
    j = i
    while j < len(text) and (text[j].isalnum() or text[j] == "_"):
        j += 1
    return text[i:j], j


def parse_statements(code: str) -> List[Stmt]:
    """Parse the top-level statement list. Returns a flat list — Blocks
    embed their children. Designed for lint passes, not codegen."""
    statements: List[Stmt] = []
    i = _skip_ws(code, 0)
    while i < len(code):
        stmt, i = parse_one_statement(code, i)
        if stmt is None:
            break
        statements.append(stmt)
        i = _skip_ws(code, i)
    return statements


def parse_one_statement(code: str, i: int) -> Tuple[Optional[Stmt], int]:
    i = _skip_ws(code, i)
    if i >= len(code):
        return None, i

    # Block
    if code[i] == "{":
        end = find_matching_brace(code, i)
        if end < 0:
            return Stmt("block", i, len(code), text=code[i:]), len(code)
        children = parse_statements(code[i + 1:end])
        # Re-base child offsets to absolute positions
        for c in children:
            _rebase(c, i + 1)
        s = Stmt("block", i, end + 1, text=code[i:end + 1], children=children)
        return s, end + 1

    # Reserved-word-starting forms
    ident, j = _match_ident_at(code, i)

    if ident == "if":
        return parse_if(code, i, j)
    if ident == "while":
        return parse_while(code, i, j)
    if ident == "exit":
        # consume up to and including the `;`
        k = _skip_ws(code, j)
        if k < len(code) and code[k] == ";":
            return Stmt("exit", i, k + 1, text=code[i:k + 1]), k + 1
        return Stmt("exit", i, j, text=code[i:j]), j
    if ident == "else":
        # else without if — caller should have handled this; skip
        return None, i

    # Otherwise it's an expression statement or assignment.
    # Read until matching `;` at depth 0.
    end = _find_stmt_end(code, i)
    if end < 0:
        return None, len(code)
    body = code[i:end]
    # Detect assignment vs expression statement.
    # Walk body looking for top-level `=` that isn't `==`/`!=`/`<=`/`>=`.
    target = _assign_target(body)
    if target is not None:
        return Stmt("assign", i, end + 1, text=code[i:end + 1], target=target), end + 1
    return Stmt("expr", i, end + 1, text=code[i:end + 1]), end + 1


def parse_if(code: str, start: int, after_keyword: int) -> Tuple[Stmt, int]:
    j = _skip_ws(code, after_keyword)
    if j >= len(code) or code[j] != "(":
        # Malformed; consume the keyword and bail
        return Stmt("if", start, after_keyword, text=code[start:after_keyword]), after_keyword
    cond_end = find_matching_paren(code, j)
    if cond_end < 0:
        return Stmt("if", start, len(code), text=code[start:]), len(code)
    cond_text = code[j + 1:cond_end]
    k = _skip_ws(code, cond_end + 1)
    then_stmt, k2 = parse_one_statement(code, k)
    children = [then_stmt] if then_stmt else []
    # Check for else
    m = _skip_ws(code, k2)
    else_branch = None
    if m < len(code):
        word, after = _match_ident_at(code, m)
        if word == "else":
            n = _skip_ws(code, after)
            else_branch, k2 = parse_one_statement(code, n)
    end = k2
    s = Stmt("if", start, end, text=code[start:end], cond_text=cond_text,
             children=children, else_branch=else_branch)
    return s, end


def parse_while(code: str, start: int, after_keyword: int) -> Tuple[Stmt, int]:
    j = _skip_ws(code, after_keyword)
    if j >= len(code) or code[j] != "(":
        return Stmt("while", start, after_keyword, text=code[start:after_keyword]), after_keyword
    cond_end = find_matching_paren(code, j)
    if cond_end < 0:
        return Stmt("while", start, len(code), text=code[start:]), len(code)
    cond_text = code[j + 1:cond_end]
    k = _skip_ws(code, cond_end + 1)
    body, k2 = parse_one_statement(code, k)
    children = [body] if body else []
    return Stmt("while", start, k2, text=code[start:k2], cond_text=cond_text,
                children=children), k2


def find_matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    in_str = False
    quote = ""
    i = open_idx
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == quote:
                in_str = False
        elif ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_stmt_end(text: str, i: int) -> int:
    """Find the index of the `;` terminating the statement starting at i,
    skipping nested ()/[]/{} and strings."""
    depth = 0
    in_str = False
    quote = ""
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == quote:
                in_str = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth == 0:
            return i
        i += 1
    return -1


def _assign_target(body: str) -> Optional[str]:
    """If `body` is an assignment, return the LHS identifier. Otherwise None."""
    # Walk for top-level `=` that isn't part of ==/!=/>=/<=.
    depth = 0
    in_str = False
    quote = ""
    for i, ch in enumerate(body):
        if in_str:
            if ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch in "([{":
            depth += 1
            continue
        if ch in ")]}":
            depth -= 1
            continue
        if ch == "=" and depth == 0:
            prev = body[i - 1] if i > 0 else ""
            nxt = body[i + 1] if i + 1 < len(body) else ""
            if prev in "=!<>" or nxt == "=":
                continue
            # Extract LHS identifier (strip subscripts)
            lhs = body[:i].strip()
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", lhs)
            if m:
                return m.group(1)
            return None
    return None


def _rebase(stmt: Stmt, base: int) -> None:
    stmt.start += base
    stmt.end += base
    for c in stmt.children:
        _rebase(c, base)
    if stmt.else_branch:
        _rebase(stmt.else_branch, base)


def flatten_stmts(stmts: List[Stmt]) -> List[Stmt]:
    """Yield every statement in tree order (depth-first)."""
    out = []
    for s in stmts:
        out.append(s)
        if s.children:
            out.extend(flatten_stmts(s.children))
        if s.else_branch:
            out.append(s.else_branch)
            if s.else_branch.children:
                out.extend(flatten_stmts(s.else_branch.children))
    return out


# ---------------------------------------------------------------------------
# Linter core
# ---------------------------------------------------------------------------

def lint_file(path: str, eqtype: Optional[str], strict: bool,
              testlist: Set[str], include_paths: List[str],
              seen_files: Optional[Set[str]] = None,
              chain: Optional[List[str]] = None,
              depth: int = 0) -> List[Issue]:
    """Lint one file. Resolves include_mask() recursively if include_paths set."""
    if seen_files is None:
        seen_files = set()
    if chain is None:
        chain = []
    if depth > 32:
        return [Issue("error", 0, 0, "INC001",
                      f"include_mask() recursion depth exceeded 32 (engine limit)",
                      include_chain=list(chain))]

    try:
        abs_path = os.path.abspath(path)
    except Exception:
        abs_path = path
    if abs_path in seen_files:
        # Circular include — silently skip (some sites legitimately have
        # mutually-aware masks; engine handles via depth limit, we mirror).
        return []
    seen_files.add(abs_path)

    try:
        with open(path) as f:
            text = f.read()
    except OSError as exc:
        return [Issue("error", 0, 0, "IO001", f"could not read {path}: {exc}",
                      include_chain=list(chain))]

    issues = lint(text, eqtype=eqtype, strict=strict, testlist=testlist,
                  chain=chain)

    # Recursively lint include_mask() targets
    if include_paths:
        for inc_name in find_include_masks(text):
            target = resolve_include(inc_name, include_paths)
            if target is None:
                # The call walker would have flagged the call itself, so we
                # just emit a path warning here.
                issues.append(Issue("warning", 0, 0, "INC002",
                    f"include_mask(\"{inc_name}\") — file not found in any include-path",
                    include_chain=list(chain)))
                continue
            sub_chain = chain + [inc_name]
            issues.extend(lint_file(target, eqtype=eqtype, strict=strict,
                                    testlist=testlist,
                                    include_paths=include_paths,
                                    seen_files=seen_files, chain=sub_chain,
                                    depth=depth + 1))
    return issues


INCLUDE_RE = re.compile(
    r"\binclude_mask\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
)


# ---------------------------------------------------------------------------
# Inline suppression directives (Feature B)
#
# Syntax:
#   /* lint: ignore CODE1,CODE2 */
#
# Suppresses those codes on the directive's line and the next non-blank line.
# ---------------------------------------------------------------------------

SUPPRESS_RE = re.compile(
    r"/\*\s*lint:\s*ignore\s+([A-Z0-9_, ]+?)\s*\*/",
    re.IGNORECASE,
)


def build_suppress_map(text: str) -> Dict[int, Set[str]]:
    """Return {line_no -> set_of_codes} suppressions for this file."""
    suppress: Dict[int, Set[str]] = {}
    lines = text.split("\n")
    for m in SUPPRESS_RE.finditer(text):
        # line_no is 1-based; m.start() index → newline-count + 1
        nl_before = text.count("\n", 0, m.start())
        directive_line = nl_before + 1
        codes = {c.strip().upper() for c in m.group(1).split(",") if c.strip()}
        if not codes:
            continue
        # Apply to the directive's own line
        suppress.setdefault(directive_line, set()).update(codes)
        # And to the next non-blank line
        next_line = directive_line + 1
        while next_line <= len(lines):
            if lines[next_line - 1].strip():
                suppress.setdefault(next_line, set()).update(codes)
                break
            next_line += 1
    return suppress


def find_include_masks(text: str) -> List[str]:
    """Extract the string-literal arguments to include_mask() calls."""
    code = strip_comments(text)
    return [m.group(1) for m in INCLUDE_RE.finditer(code)]


def resolve_include(name: str, include_paths: List[str]) -> Optional[str]:
    """Look up an include_mask name in the configured paths."""
    candidates = [name, name + ".eq", name + ".mask", name + ".rule"]
    for d in include_paths:
        for c in candidates:
            p = os.path.join(d, c)
            if os.path.isfile(p):
                return p
    return None


def lint(text: str, eqtype: Optional[str] = None, strict: bool = False,
         testlist: Optional[Set[str]] = None,
         chain: Optional[List[str]] = None) -> List[Issue]:
    chain = chain or []
    issues = []
    code = strip_comments(text)

    # ---- Language-level lints ----

    for m in re.finditer(r"\b\d+(?:\.\d+)?[eE][+-]?\d+\b", code):
        line, col = line_col_of(code, m.start())
        issues.append(Issue("error", line, col, "LANG001",
            f"Scientific notation `{m.group()}` is not supported. Use a plain decimal.",
            include_chain=list(chain)))

    for m in re.finditer(r"\bfor\s*\(", code):
        line, col = line_col_of(code, m.start())
        issues.append(Issue("error", line, col, "LANG002",
            "`for` loops are not supported — only `while`.",
            include_chain=list(chain)))

    for m in re.finditer(r"\b(break|continue)\b", code):
        line, col = line_col_of(code, m.start())
        issues.append(Issue("error", line, col, "LANG003",
            f"`{m.group()}` is not supported. Use `exit;` to terminate the whole equation.",
            include_chain=list(chain)))

    for m in re.finditer(r"\b(def|function|sub)\s+[A-Za-z_][A-Za-z_0-9]*\s*\(", code):
        line, col = line_col_of(code, m.start())
        issues.append(Issue("error", line, col, "LANG004",
            "Named function/subroutine definitions are not supported. "
            "Use `include_mask()` for reuse.",
            include_chain=list(chain)))

    # ---- Walk function calls ----

    # Collect identifiers that appear in call position so we don't double-flag
    # them as "unknown test name" below.
    called_names: Set[str] = set()

    pos = 0
    while True:
        m = CALL_RE.search(code, pos)
        if not m:
            break

        name = m.group(1)
        if name in CONTROL_KEYWORDS:
            pos = m.end()
            continue

        called_names.add(name)
        open_paren = m.end() - 1
        close_paren = find_matching_paren(code, open_paren)
        line, col = line_col_of(code, m.start())

        if close_paren < 0:
            issues.append(Issue("error", line, col, "PARSE001",
                f"Unclosed parenthesis after `{name}`",
                include_chain=list(chain)))
            pos = m.end()
            continue

        args_str = code[open_paren + 1:close_paren]
        args = split_top_level_args(args_str) if args_str.strip() else []
        arg_count = len(args)

        if name in SUBROUTINES:
            expected, mask, note = SUBROUTINES[name]

            if expected != arg_count:
                issues.append(Issue("error", line, col, "ARG001",
                    f"`{name}` expects {expected} argument(s), got {arg_count}",
                    include_chain=list(chain)))

            if eqtype and not eqtype_matches(mask, eqtype):
                issues.append(Issue("warning", line, col, "EQTYPE001",
                    f"`{name}` may not be valid in {eqtype} context "
                    f"(declared: {mask_to_pretty(mask)})",
                    include_chain=list(chain)))

            if note and strict:
                issues.append(Issue("info", line, col, "NOTE001",
                    f"`{name}`: {note}", include_chain=list(chain)))

            issues.extend(_specific_arg_checks(name, args, line, col, chain))

        else:
            if name.isupper():
                # Probably a test/panel name with parens — uncommon, skip.
                pass
            else:
                # "Did you mean?" suggestions
                suggestions = difflib.get_close_matches(
                    name, SUBROUTINES.keys(), n=3, cutoff=0.7)
                msg = f"Unknown subroutine `{name}`"
                if suggestions:
                    msg += f" — did you mean: {', '.join(suggestions)}?"
                else:
                    msg += " — possible typo or not in the catalogue"
                issues.append(Issue("warning", line, col, "UNKNOWN001", msg,
                    include_chain=list(chain)))

        pos = close_paren + 1

    # ---- Uppercase user-variable detection (Feature C) ----
    # Only meaningful when a --testlist is provided: without it we can't tell
    # whether `SODIUM = 5;` is a legitimate test result write (shorthand for
    # `SODIUM[0][0] = 5;`) or a convention-violating user variable.
    #
    # With a testlist:
    #   - In-testlist names: legit test/panel result write — skip
    #   - System identifiers: legit (e.g. UR_DOB = ...) — skip
    #   - Subroutine names being assigned (rare): skip
    #   - Otherwise: convention violation → UPPER001
    if testlist is not None:
        scan = strip_string_contents(code)
        for m in re.finditer(r"(?<![=!<>])\b([A-Z][A-Z0-9_]*)\s*=(?!=)", scan):
            ident = m.group(1)
            if ident in SYSTEM_IDENTIFIERS:
                continue
            if ident in testlist:
                continue
            if ident in SUBROUTINES:
                continue
            if ident in {"EQTYPE_ALL"} or ident.startswith("EQTYPE_") or ident.startswith("TESTSTATUS_"):
                continue
            # Skip if subscripted before the `=` (TEST[i] = ...): walk back
            # to check that the preceding non-whitespace token isn't a `]`.
            prev = m.start() - 1
            while prev >= 0 and scan[prev] == " ":
                prev -= 1
            if prev >= 0 and scan[prev] == "]":
                continue
            line, col = line_col_of(scan, m.start())
            issues.append(Issue("warning", line, col, "UPPER001",
                f"`{ident}` looks like a user variable but UPPERCASE is reserved "
                f"for tests, panels, and system variables. Use lowercase for "
                f"user-defined variables (or add `{ident}` to the --testlist "
                f"if it is a real test/panel).",
                include_chain=list(chain)))

    # (DEAD001 is handled by the AST pass below — see _scan_dead_code.)

    # ---- AST-based lints (Tier 2): unused vars, empty blocks, better dead code ----
    try:
        ast = parse_statements(code)
    except Exception:
        ast = []
    issues.extend(_ast_lints(code, ast, chain))

    # ---- Test/panel mnemonic validation ----

    if testlist is not None:
        # Collect uppercase identifiers that:
        #  - aren't in SYSTEM_IDENTIFIERS
        #  - aren't called names
        #  - aren't in the testlist
        # Flag once per (name, line). Use a string-stripped copy so we don't
        # match identifier-shaped substrings inside string literals.
        scan_code = strip_string_contents(code)
        seen_warning: Set[Tuple[str, int]] = set()
        for m in IDENT_RE.finditer(scan_code):
            ident = m.group(1)
            if not ident.isupper():
                continue
            if not any(c.isalpha() for c in ident):
                continue
            if ident in SYSTEM_IDENTIFIERS:
                continue
            if ident in called_names:
                continue
            if ident in testlist:
                continue
            if ident in {"EQTYPE_ALL", "EQTYPE_TESTRECALC"}:
                continue
            line, col = line_col_of(code, m.start())
            if (ident, line) in seen_warning:
                continue
            seen_warning.add((ident, line))
            suggestions = difflib.get_close_matches(
                ident, testlist, n=3, cutoff=0.7)
            msg = f"`{ident}` not in configured test/panel catalogue"
            if suggestions:
                msg += f" — did you mean: {', '.join(suggestions)}?"
            issues.append(Issue("warning", line, col, "TEST001", msg,
                include_chain=list(chain)))

    return issues


def _ast_lints(code: str, stmts: List[Stmt], chain: List[str]) -> List[Issue]:
    """Lints that need block / statement structure: dead-code in nested blocks,
    empty blocks, unused variables, used-before-set."""
    issues = []
    cc = list(chain)

    # ---- Empty block detection (EMPTY001) ----
    # if (cond) { }  /  while (cond) { }  /  else { }
    for s in flatten_stmts(stmts):
        if s.kind == "block" and not s.children:
            # Skip outer top-level blocks (rare) — only flag empty as a body
            line, col = line_col_of(code, s.start)
            issues.append(Issue("warning", line, col, "EMPTY001",
                "Empty block — no statements between `{` and `}`",
                include_chain=cc))

    # ---- Better dead-code detection (DEAD001) ----
    # An `exit;` statement is unreachable code if anything non-trivial follows
    # it in the same containing block.
    _scan_dead_code(code, stmts, issues, cc)

    # ---- Unused / used-before-set variable detection (UNUSED001/USE001) ----
    # Limit to lowercase user variables (UPPERCASE handled by UPPER001).
    # Iterate over assignments to find LHS targets; collect identifier reads
    # from RHS / expr statements / conditions.
    assigned: Dict[str, List[Tuple[int, int]]] = {}  # name -> [(line, col), ...]
    reads: Set[str] = set()
    read_lines: Dict[str, Tuple[int, int]] = {}

    def collect_reads(text: str, base: int, exclude_lhs: bool = False) -> None:
        # If exclude_lhs, the LHS identifier of an assignment is skipped
        # (it appears before `=` so we trim there first).
        scan = strip_string_contents(text)
        if exclude_lhs:
            # Trim from start until first `=` not followed by `=` at depth 0
            depth = 0
            i = 0
            while i < len(scan):
                ch = scan[i]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "=" and depth == 0 and (i + 1 >= len(scan) or scan[i + 1] != "="):
                    scan = scan[i + 1:]
                    base = base + i + 1
                    break
                i += 1
        for m in IDENT_RE.finditer(scan):
            name = m.group(1)
            if not name or not name[0].islower():
                continue
            if name in RESERVED_WORDS:
                continue
            if name in SUBROUTINES:
                # It's a subroutine call (or shadowed name) — not a variable read.
                # Look at what immediately follows in the original text.
                after = base + m.end()
                # Skip whitespace
                k = after
                while k < len(code) and code[k] in " \t\r\n":
                    k += 1
                if k < len(code) and code[k] == "(":
                    continue
            reads.add(name)
            line, col = line_col_of(code, base + m.start())
            read_lines.setdefault(name, (line, col))

    for s in flatten_stmts(stmts):
        if s.kind == "assign":
            tgt = s.target
            if tgt and tgt[0].islower():
                line, col = line_col_of(code, s.start)
                assigned.setdefault(tgt, []).append((line, col))
            # Collect reads from the RHS
            collect_reads(s.text, s.start, exclude_lhs=True)
        elif s.kind in ("expr",):
            collect_reads(s.text, s.start)
        elif s.kind in ("if", "while"):
            collect_reads(s.cond_text, s.start)

    # UNUSED001: assigned but never read
    for name, positions in assigned.items():
        if name in reads:
            continue
        # Suppress some convention-y names — i, j, k, count, n, val are typical
        # one-off scratch variables. They're frequently set then used, and our
        # parser may miss the read in a nested expr we don't fully analyse.
        # Skip these to avoid false positives.
        if name in {"i", "j", "k", "n"}:
            continue
        for line, col in positions:
            issues.append(Issue("warning", line, col, "UNUSED001",
                f"Variable `{name}` is assigned but never read",
                include_chain=cc))

    # USE001: read appears before any assignment textually
    for name in reads:
        if name not in assigned:
            # This is fine — many language built-ins look like lowercase names
            # we don't catalogue. Don't flag.
            continue
        first_read_line = read_lines.get(name, (1 << 30, 0))[0]
        first_assign_line = min(p[0] for p in assigned[name])
        if first_read_line < first_assign_line:
            line, col = read_lines[name]
            issues.append(Issue("warning", line, col, "USE001",
                f"Variable `{name}` is read before it is assigned (first assignment at line {first_assign_line})",
                include_chain=cc))

    return issues


def _scan_dead_code(code: str, stmts: List[Stmt], issues: List[Issue],
                    cc: List[str]) -> None:
    """Walk every block of statements; warn when statements follow an
    unconditional `exit;` within the same block."""
    # Top-level
    _scan_block(code, stmts, issues, cc)
    # Recurse
    for s in flatten_stmts(stmts):
        if s.kind == "block" and s.children:
            _scan_block(code, s.children, issues, cc)


def _scan_block(code: str, stmts: List[Stmt], issues: List[Issue],
                cc: List[str]) -> None:
    for idx, s in enumerate(stmts):
        if s.kind == "exit" and idx + 1 < len(stmts):
            after = stmts[idx + 1]
            line, col = line_col_of(code, after.start)
            issues.append(Issue("warning", line, col, "DEAD001",
                "Statement after `exit;` is unreachable. "
                "`exit;` terminates the whole equation immediately.",
                include_chain=cc))
            return  # don't emit again for subsequent statements in this block


def _specific_arg_checks(name: str, args: List[str], line: int, col: int,
                         chain: List[str]) -> List[Issue]:
    issues = []
    cc = list(chain)

    if name in ("test_set_status", "test_clear_status") and len(args) == 2:
        val = args[1].strip()
        if val.isdigit():
            n = int(val)
            if n not in (1, 2):
                issues.append(Issue("warning", line, col, "VAL001",
                    f"`{name}` only accepts 1 (`<`) or 2 (`>`); value {n} is silently ignored",
                    include_chain=cc))

    if name == "test_check_status" and len(args) == 2:
        val = args[1].strip()
        if val.isdigit():
            n = int(val)
            if n not in (1, 2, 3):
                issues.append(Issue("warning", line, col, "VAL002",
                    f"`test_check_status` only accepts 1/2/3; value {n} returns 0",
                    include_chain=cc))

    if name == "result_count" and len(args) == 1:
        arg = args[0].strip()
        if not (arg.startswith('"') or arg.startswith("'")):
            bare = arg.replace("_", "").replace(",", "")
            if bare.isalnum() and bare.isupper() and len(arg) >= 2:
                issues.append(Issue("warning", line, col, "MISUSE001",
                    "`result_count` takes a comma-separated STRING "
                    "(e.g. \"SODIUM,POTASSIUM\"), not a test identifier. "
                    "It does NOT count loaded cumulative records.",
                    include_chain=cc))

    if name == "include_mask" and len(args) == 1:
        arg = args[0].strip()
        if not (arg.startswith('"') or arg.startswith("'")):
            issues.append(Issue("warning", line, col, "MISUSE002",
                "`include_mask` expects a literal string mask name",
                include_chain=cc))

    if name == "eqlogging" and len(args) >= 1:
        val = args[0].strip()
        if val.isdigit():
            n = int(val)
            if n not in (0, 1, 2):
                issues.append(Issue("warning", line, col, "VAL003",
                    f"`eqlogging` level should be 0 (info), 1 (warning), or 2 (error); got {n}",
                    include_chain=cc))

    return issues


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------

SEVERITY_COLOURS = {
    "error":   "\033[31merror\033[0m",
    "warning": "\033[33mwarning\033[0m",
    "info":    "\033[34minfo\033[0m",
}


def format_text(filename: str, issue: Issue, colour: bool) -> str:
    sev = SEVERITY_COLOURS[issue.severity] if colour else issue.severity
    chain = ""
    if issue.include_chain:
        chain = f" (via include_mask: {' ← '.join(reversed(issue.include_chain))})"
    return f"{filename}:{issue.line}:{issue.column}: {sev} [{issue.code}] {issue.message}{chain}"


def format_json(filename: str, issues: List[Issue]) -> str:
    out = []
    for issue in issues:
        d = asdict(issue)
        d["file"] = filename
        out.append(d)
    return json.dumps(out, indent=2)


def format_sarif(filename: str, issues: List[Issue]) -> str:
    """Render issues as SARIF v2.1.0 JSON (suitable for CI annotation)."""
    rules_seen = {}
    results = []
    for issue in issues:
        rules_seen[issue.code] = issue.severity
        sarif_level = {
            "error": "error",
            "warning": "warning",
            "info": "note",
        }[issue.severity]
        msg = issue.message
        if issue.include_chain:
            msg += f" (via include_mask: {' ← '.join(reversed(issue.include_chain))})"
        results.append({
            "ruleId": issue.code,
            "level": sarif_level,
            "message": {"text": msg},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": filename},
                    "region": {
                        "startLine": max(issue.line, 1),
                        "startColumn": max(issue.column, 1),
                    },
                },
            }],
        })

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "rule_lint",
                    "informationUri": "https://example.invalid/rule_lint",
                    "rules": [{"id": rid} for rid in sorted(rules_seen)],
                },
            },
            "results": results,
        }],
    }
    return json.dumps(sarif, indent=2)


# ---------------------------------------------------------------------------
# Multi-file JSON / SARIF aggregation (Feature A + output formats)
# ---------------------------------------------------------------------------

def format_json_multi(per_file: List[Tuple[str, List[Issue]]]) -> str:
    out = []
    for filename, issues in per_file:
        for issue in issues:
            d = asdict(issue)
            d["file"] = filename
            out.append(d)
    return json.dumps(out, indent=2)


def format_sarif_multi(per_file: List[Tuple[str, List[Issue]]]) -> str:
    rules_seen = {}
    results = []
    for filename, issues in per_file:
        for issue in issues:
            rules_seen[issue.code] = issue.severity
            sarif_level = {
                "error": "error",
                "warning": "warning",
                "info": "note",
            }[issue.severity]
            msg = issue.message
            if issue.include_chain:
                msg += f" (via include_mask: {' ← '.join(reversed(issue.include_chain))})"
            results.append({
                "ruleId": issue.code,
                "level": sarif_level,
                "message": {"text": msg},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": filename},
                        "region": {
                            "startLine": max(issue.line, 1),
                            "startColumn": max(issue.column, 1),
                        },
                    },
                }],
            })

    rules = [
        {
            "id": rid,
            "shortDescription": {"text": ISSUE_CODES[rid][1]} if rid in ISSUE_CODES else {"text": rid},
            "defaultConfiguration": {
                "level": {"error": "error", "warning": "warning", "info": "note"}.get(
                    ISSUE_CODES.get(rid, ("warning", ""))[0], "warning")
            },
        }
        for rid in sorted(rules_seen)
    ]

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "rule_lint",
                    "informationUri": "https://example.invalid/rule_lint",
                    "rules": rules,
                },
            },
            "results": results,
        }],
    }
    return json.dumps(sarif, indent=2)


# ---------------------------------------------------------------------------
# Suppression filter (Feature B)
# ---------------------------------------------------------------------------

def apply_suppressions(issues: List[Issue],
                       suppress_map: Dict[int, Set[str]]) -> List[Issue]:
    kept = []
    for issue in issues:
        codes = suppress_map.get(issue.line, set())
        if issue.code in codes or "ALL" in codes:
            continue
        kept.append(issue)
    return kept


# ---------------------------------------------------------------------------
# Baseline diff (Feature H)
# ---------------------------------------------------------------------------

def _baseline_key(filename: str, issue: Issue) -> str:
    """Stable identity for a baseline entry. Line numbers excluded so the
    baseline survives line drift; message included so multi-instance issues
    stay distinct."""
    return f"{filename}|{issue.code}|{issue.message}"


def load_baseline(path: str) -> Set[str]:
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"baseline file {path} is malformed (expected JSON list)")
    keys = set()
    for entry in data:
        keys.add(f"{entry.get('file', '')}|{entry.get('code', '')}|"
                 f"{entry.get('message', '')}")
    return keys


def save_baseline(path: str, per_file: List[Tuple[str, List[Issue]]]) -> None:
    flat = []
    for filename, issues in per_file:
        for issue in issues:
            flat.append({
                "file": filename,
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "include_chain": issue.include_chain,
            })
    with open(path, "w") as f:
        json.dump(flat, f, indent=2, sort_keys=True)
    print(f"Wrote baseline of {len(flat)} issue(s) to {path}", file=sys.stderr)


def filter_against_baseline(
    per_file: List[Tuple[str, List[Issue]]],
    baseline: Set[str],
) -> List[Tuple[str, List[Issue]]]:
    filtered = []
    for filename, issues in per_file:
        kept = [i for i in issues if _baseline_key(filename, i) not in baseline]
        filtered.append((filename, kept))
    return filtered


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auto-fix mode (--fix)
#
# Only mechanical, semantically-safe fixes are applied. Anything that would
# alter program behaviour beyond cosmetics is left for manual review.
# ---------------------------------------------------------------------------

# Each fixer takes the file text, returns (new_text, list_of_descriptions).
# A description is a human-readable note about each fix applied.

def _fix_scientific_notation(text: str) -> Tuple[str, List[Tuple[int, str]]]:
    """Replace `1.5e-3` etc. with plain decimal literals.
    Returns (new_text, list_of_(line_no, description))."""
    fixes: List[Tuple[int, str]] = []
    pattern = re.compile(r"\b(\d+(?:\.\d+)?)[eE]([+-]?\d+)\b")
    out_parts: List[str] = []
    last = 0
    for m in pattern.finditer(text):
        mantissa = m.group(1)
        exponent = int(m.group(2))
        # Convert exactly via Python decimal — we use Python float printing
        # then trim. For typical lab constants this is fine.
        try:
            value = float(mantissa) * (10 ** exponent)
        except OverflowError:
            continue
        # Format without scientific notation
        formatted = f"{value:.10f}".rstrip("0").rstrip(".")
        if not formatted:
            formatted = "0"
        if "." not in formatted and exponent < 0:
            formatted = formatted + ".0"
        out_parts.append(text[last:m.start()])
        out_parts.append(formatted)
        last = m.end()
        line_no = text.count("\n", 0, m.start()) + 1
        fixes.append((line_no, f"replaced `{m.group()}` with `{formatted}`"))
    out_parts.append(text[last:])
    return "".join(out_parts), fixes


def _fix_trailing_whitespace(text: str) -> Tuple[str, List[Tuple[int, str]]]:
    """Strip trailing whitespace from every line."""
    fixes: List[Tuple[int, str]] = []
    out_lines = []
    for idx, line in enumerate(text.split("\n"), start=1):
        stripped = re.sub(r"[ \t]+$", "", line)
        if stripped != line:
            fixes.append((idx, "stripped trailing whitespace"))
        out_lines.append(stripped)
    return "\n".join(out_lines), fixes


SAFE_FIXERS = [
    ("scientific notation", _fix_scientific_notation),
    ("trailing whitespace", _fix_trailing_whitespace),
]


def apply_fixes(text: str) -> Tuple[str, List[Tuple[int, str]]]:
    """Run all safe fixers in sequence. Returns the final text and an
    aggregated list of (line, description) for each applied fix."""
    fixes_all: List[Tuple[int, str]] = []
    for label, fn in SAFE_FIXERS:
        text, fixes = fn(text)
        for ln, desc in fixes:
            fixes_all.append((ln, f"[{label}] {desc}"))
    return text, fixes_all


def print_codes_table() -> None:
    print(f"{'code':<11} {'severity':<8}  description")
    print(f"{'-' * 11} {'-' * 8}  {'-' * 60}")
    for code in sorted(ISSUE_CODES):
        sev, desc = ISSUE_CODES[code]
        print(f"{code:<11} {sev:<8}  {desc}")


def explain_code(code: str) -> int:
    code = code.upper()
    if code not in ISSUE_CODES:
        print(f"Unknown code '{code}'. Use --list-codes to see all.",
              file=sys.stderr)
        return 2
    sev, desc = ISSUE_CODES[code]
    print(f"{code} [{sev}]")
    print(desc)
    return 0


def lint_one_text(filename: str, text: str, eqtype: Optional[str],
                  strict: bool, testlist: Optional[Set[str]]) -> List[Issue]:
    issues = lint(text, eqtype=eqtype, strict=strict, testlist=testlist)
    return apply_suppressions(issues, build_suppress_map(text))


def lint_one_file(filename: str, eqtype: Optional[str], strict: bool,
                  testlist: Optional[Set[str]],
                  include_paths: List[str]) -> List[Issue]:
    # Read the top-level file ourselves so we can apply its suppression map.
    # Includes are linted recursively below — each include's directives apply
    # within its own file.
    try:
        with open(filename) as f:
            text = f.read()
    except OSError as exc:
        return [Issue("error", 0, 0, "IO001", f"could not read {filename}: {exc}")]

    suppress = build_suppress_map(text)
    issues = lint(text, eqtype=eqtype, strict=strict, testlist=testlist)
    issues = apply_suppressions(issues, suppress)

    if include_paths:
        for inc_name in find_include_masks(text):
            target = resolve_include(inc_name, include_paths)
            if target is None:
                issues.append(Issue("warning", 0, 0, "INC002",
                    f"include_mask(\"{inc_name}\") — file not found in any include-path"))
                continue
            sub_issues = lint_file(target, eqtype=eqtype, strict=strict,
                                   testlist=testlist or set(),
                                   include_paths=include_paths,
                                   seen_files={os.path.abspath(filename)},
                                   chain=[inc_name],
                                   depth=1)
            # Re-load each include file once to apply its own suppression map.
            try:
                with open(target) as f:
                    sub_text = f.read()
                sub_suppress = build_suppress_map(sub_text)
                sub_issues = apply_suppressions(sub_issues, sub_suppress)
            except OSError:
                pass
            issues.extend(sub_issues)
    return issues


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Linter for Evolution rule-engine equations.",
        epilog="See RULE_ENGINE_SUBROUTINES.md for the language reference. "
               "Inline suppression: /* lint: ignore CODE1,CODE2 */")
    parser.add_argument("files", nargs="*",
                        help="Rule equation files to lint. Glob expansion is "
                             "handled by the shell. With no files, reads stdin.")
    parser.add_argument("--eqtype",
                        help="Equation type context. Valid: "
                             + ", ".join(sorted(EQ_TYPE_ALIASES.keys())))
    parser.add_argument("--strict", action="store_true",
                        help="Also emit informational notes about known foot-guns")
    parser.add_argument("--include-path", action="append", default=[],
                        help="Directory to search for include_mask() targets "
                             "(can be given multiple times)")
    parser.add_argument("--testlist",
                        help="CSV of valid test/panel mnemonics. "
                             "See --help for format.")
    parser.add_argument("--format", choices=["text", "json", "sarif"],
                        default="text",
                        help="Output format (default: text)")
    parser.add_argument("--no-colour", action="store_true",
                        help="Disable colourised text output")
    parser.add_argument("--quiet", action="store_true",
                        help="Print errors only (suppress warnings/info)")
    parser.add_argument("--max-warnings", type=int, default=None,
                        help="Fail with exit code 3 if warning count exceeds N")
    parser.add_argument("--baseline",
                        help="Path to a baseline JSON file. Issues already in "
                             "the baseline are not reported.")
    parser.add_argument("--update-baseline",
                        help="Write current issues as the new baseline to this path "
                             "(does NOT lint against a previous baseline).")
    parser.add_argument("--list-codes", action="store_true",
                        help="Print all issue codes with descriptions and exit")
    parser.add_argument("--explain",
                        help="Print explanation for a single issue code and exit")
    parser.add_argument("--fix", action="store_true",
                        help="Apply safe mechanical fixes (scientific notation, "
                             "trailing whitespace) to each input file and write "
                             "back in-place. Skips stdin. Reports applied fixes "
                             "and then continues with linting.")
    parser.add_argument("--fix-dry-run", action="store_true",
                        help="Show what --fix would change without writing files")
    args = parser.parse_args(argv)

    if args.list_codes:
        print_codes_table()
        return 0
    if args.explain:
        return explain_code(args.explain)

    if args.eqtype and args.eqtype not in EQ_TYPE_ALIASES:
        print(f"Unknown --eqtype '{args.eqtype}'. "
              f"Valid: {', '.join(sorted(EQ_TYPE_ALIASES.keys()))}",
              file=sys.stderr)
        return 2

    testlist = None
    if args.testlist:
        try:
            testlist, _types = load_testlist(args.testlist)
            print(f"Loaded {len(testlist)} test/panel mnemonics from "
                  f"{args.testlist}", file=sys.stderr)
        except OSError as exc:
            print(f"Could not read testlist {args.testlist}: {exc}",
                  file=sys.stderr)
            return 2

    # ---- Apply --fix first if requested ----
    if (args.fix or args.fix_dry_run) and args.files:
        any_fixed = False
        for filename in args.files:
            try:
                with open(filename) as f:
                    original = f.read()
            except OSError as exc:
                print(f"Could not read {filename}: {exc}", file=sys.stderr)
                return 2
            fixed, fixes = apply_fixes(original)
            if not fixes:
                continue
            any_fixed = True
            if args.fix_dry_run:
                print(f"--- {filename} (dry-run, would apply {len(fixes)} fix(es))",
                      file=sys.stderr)
            else:
                with open(filename, "w") as f:
                    f.write(fixed)
                print(f"--- {filename} (applied {len(fixes)} fix(es))",
                      file=sys.stderr)
            for ln, desc in fixes:
                print(f"  line {ln}: {desc}", file=sys.stderr)
        if args.fix_dry_run and any_fixed:
            print("(no files written — --fix-dry-run)", file=sys.stderr)

    # ---- Collect issues across all input files ----
    per_file: List[Tuple[str, List[Issue]]] = []
    if args.files:
        for filename in args.files:
            issues = lint_one_file(filename, eqtype=args.eqtype,
                                   strict=args.strict, testlist=testlist,
                                   include_paths=args.include_path)
            per_file.append((filename, issues))
    else:
        text = sys.stdin.read()
        issues = lint_one_text("<stdin>", text, eqtype=args.eqtype,
                               strict=args.strict, testlist=testlist)
        per_file.append(("<stdin>", issues))

    # ---- Baseline filtering ----
    if args.update_baseline:
        save_baseline(args.update_baseline, per_file)
        # Update-baseline mode does NOT emit issues; it just records them.
        return 0

    if args.baseline:
        try:
            baseline = load_baseline(args.baseline)
            per_file = filter_against_baseline(per_file, baseline)
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"Could not read baseline {args.baseline}: {exc}",
                  file=sys.stderr)
            return 2

    # ---- Quiet filter ----
    if args.quiet:
        per_file = [(f, [i for i in issues if i.severity == "error"])
                    for f, issues in per_file]

    # ---- Sort within each file ----
    for _, issues in per_file:
        issues.sort(key=lambda i: (i.include_chain, i.line, i.column))

    # ---- Render ----
    if args.format == "json":
        print(format_json_multi(per_file))
    elif args.format == "sarif":
        print(format_sarif_multi(per_file))
    else:
        colour = sys.stdout.isatty() and not args.no_colour
        for filename, issues in per_file:
            for issue in issues:
                print(format_text(filename, issue, colour))

    # ---- Totals + exit ----
    all_issues = [i for _, issues in per_file for i in issues]
    n_err = sum(1 for i in all_issues if i.severity == "error")
    n_warn = sum(1 for i in all_issues if i.severity == "warning")
    n_info = sum(1 for i in all_issues if i.severity == "info")
    n_files = len(per_file)

    if args.format == "text" and all_issues:
        print(f"\n{n_files} file(s); {n_err} error(s), {n_warn} warning(s), "
              f"{n_info} info", file=sys.stderr)

    if args.max_warnings is not None and n_warn > args.max_warnings:
        print(f"warning count {n_warn} exceeds --max-warnings {args.max_warnings}",
              file=sys.stderr)
        return 3
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
