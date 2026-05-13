#!/usr/bin/env python3
"""
gen_rwf_catalogue.py — generate rule_catalogue.py from src/eq.c.

Parses the RWF dispatch table in src/eq.c (the canonical source-of-truth for
the rule engine's subroutines) and writes a Python module containing every
subroutine name, parameter count, and equation-type mask.

Run after any change to eq.c:

    python3 support/gen_rwf_catalogue.py
    # writes support/rule_catalogue.py

The resulting module is imported by rule_lint.py to validate rule equations.

Usage:
    python3 gen_rwf_catalogue.py [--source <path/to/eq.c>] [--out <path>] [--print]

Defaults look for src/eq.c relative to this script.
"""

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# Eq-type flag bit values (from src/pathology.h:1321-1341)
# ---------------------------------------------------------------------------

EQ_BITS = {
    "EQTYPE_TESTRECALC":   0x0001,
    "EQTYPE_TESTVALIDATE": 0x0002,
    "EQTYPE_L1VALIDATE":   0x0004,
    "EQTYPE_ANALYSER":     0x0008,
    "EQTYPE_REQUEST_ADD":  0x0010,
    "EQTYPE_REQUEST_RM":   0x0020,
    "EQTYPE_REPORT":       0x0040,
    "EQTYPE_TESTACCEPT":   0x0080,
    "EQTYPE_GENERIC":      0x0100,
    "EQTYPE_AUTOVAL":      0x0200,
    "EQTYPE_REGISTRATION": 0x0400,
    "EQTYPE_REPDISP":      0x0800,
    "EQTYPE_MBSCHECK":     0x1000,
    "EQTYPE_MFA":          0x2000,
    "EQTYPE_BILLING":      0x4000,
    "EQTYPE_RETRIGGER":    0x8000,
}

EQTYPE_ALL = 0xFFFF  # ~0 truncated to the defined bits


# Short eq-type macros local to src/eq.c (lines 18953-18963)
LOCAL_MACROS = {
    "ET_RC":  EQ_BITS["EQTYPE_TESTRECALC"],
    "ET_TV":  EQ_BITS["EQTYPE_TESTVALIDATE"],
    "ET_TA":  EQ_BITS["EQTYPE_TESTACCEPT"],
    "ET_L1V": EQ_BITS["EQTYPE_L1VALIDATE"],
    "ET_AN":  EQ_BITS["EQTYPE_ANALYSER"],
    "ET_RA":  EQ_BITS["EQTYPE_REQUEST_ADD"],
    "ET_RM":  EQ_BITS["EQTYPE_REQUEST_RM"],
    "ET_RG":  EQ_BITS["EQTYPE_REGISTRATION"],
}

# Composite macros also in src/eq.c (lines 18962-18963)
LOCAL_MACROS["ET_NOT_AN"] = (
    LOCAL_MACROS["ET_RC"] | LOCAL_MACROS["ET_TV"] | LOCAL_MACROS["ET_TA"]
    | LOCAL_MACROS["ET_L1V"] | LOCAL_MACROS["ET_RA"] | LOCAL_MACROS["ET_RM"]
    | LOCAL_MACROS["ET_RG"] | EQ_BITS["EQTYPE_REPORT"] | EQ_BITS["EQTYPE_GENERIC"]
    | EQ_BITS["EQTYPE_AUTOVAL"]
)
LOCAL_MACROS["ET_INTERACTIVE"] = (
    LOCAL_MACROS["ET_RC"] | LOCAL_MACROS["ET_TV"] | LOCAL_MACROS["ET_TA"]
    | LOCAL_MACROS["ET_RA"] | LOCAL_MACROS["ET_RM"]
)

# Reverse-map from bit value to symbolic name for compact storage / display
BIT_TO_SYMBOL = {v: k for k, v in EQ_BITS.items()}


# ---------------------------------------------------------------------------
# Curated notes (hand-written, preserved across regenerations)
#
# Surfaced via --strict in the linter. Add new entries as you discover
# common foot-guns. Only need to list subroutines that have non-obvious
# usage. Keep notes short — one line each.
# ---------------------------------------------------------------------------

NOTES = {
    "loadcumulative":      "Loads ALL results (incl unvalidated); only labs that had a cumulative report printed",
    "loadhistorical":      "Validated only; requires current lab in UR list — returns 0 if lab not yet saved",
    "loadlinkedresults":   "Identical to loadhistorical in current code",
    "test_present":        "Checks lab->test[] (auto-added tests count as present)",
    "test_ordered":        "Checks lab->requests[] (does NOT include auto-added tests)",
    "test_modified":       "Compares against open-session snapshot only",
    "test_set_status":     "Only accepts 1 (<) or 2 (>); other values silently ignored",
    "test_check_status":   "Only accepts 1/2/3; other values return 0",
    "test_clear_status":   "Only accepts 1 (<) or 2 (>); other values silently ignored",
    "delta":               "Returns BOOLEAN (1 if TESTSTATUS_DELTA set) — NOT the numeric delta amount",
    "result_count":        "Takes a comma-separated STRING (e.g. \"SODIUM,POTASSIUM\") — not a test identifier",
    "jump_to_test":        "Only valid in Interactive eq types — TestRecalc/TestValidate/TestAccept/Request_Add/Request_Remove",
    "cumresult_format":    "Sets cumulative_A4 mode; use in A4 cumulative report context",
    "include_mask":        "Max 32 levels deep",
    "retrigger":           "Re-queues current equation; DEV-5812",
    "eqlogging":           "level: 0=info, 1=warning, 2=error",
    "output_testname":     "test is LAST arg",
    "output_panelname":    "panel is LAST arg",
    "output_guideline":    "Requires FSS licence",
    "output_lowprec":      "Requires FSS licence",
    "output_highprec":     "Requires FSS licence",
    "output_uncertainty":  "Requires uncertainty licence",
    "outcum_testname":     "Requires prior loadcumulative()",
    "an_result":           "Operates on raw LEVEL1_RESULT, not validated lab record",
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Match a single dispatch-table row:
#   { "name", T_FUNCTION, F_FOO, <param_count>, <eqtype_expr>, 0 },
ROW_RE = re.compile(
    r"\{\s*"
    r'"([A-Za-z_][A-Za-z0-9_]*)"\s*,\s*'   # 1: name
    r"T_FUNCTION\s*,\s*"                   # only T_FUNCTION rows (skip T_IF/T_EQ etc.)
    r"F_[A-Za-z0-9_]+\s*,\s*"              # F_ enum (not captured)
    r"(\d+)\s*,\s*"                        # 2: param count
    r"([^,]+?)\s*,\s*"                     # 3: eqtype expression (lazy until next comma)
    r"\d+\s*"                              # trailing 0
    r"\}",
    re.DOTALL,
)


def parse_eqtype_expr(expr: str) -> int:
    """Evaluate an OR-of-macros expression to an integer mask.

    Handles:
      EQTYPE_ALL
      EQTYPE_REPORT
      EQTYPE_ANALYSER | EQTYPE_L1VALIDATE
      ET_NOT_AN
      ET_INTERACTIVE | EQTYPE_REPORT
    """
    expr = expr.strip()
    if not expr:
        return 0
    # Drop any outer parens
    while expr.startswith("(") and expr.endswith(")"):
        expr = expr[1:-1].strip()

    if expr == "EQTYPE_ALL":
        return EQTYPE_ALL

    total = 0
    for term in expr.split("|"):
        term = term.strip()
        # Inner parens (e.g. (ET_RC | ET_TV))
        while term.startswith("(") and term.endswith(")"):
            term = term[1:-1].strip()
        if term in EQ_BITS:
            total |= EQ_BITS[term]
        elif term in LOCAL_MACROS:
            total |= LOCAL_MACROS[term]
        elif term == "EQTYPE_ALL":
            return EQTYPE_ALL
        elif term == "0":
            pass
        else:
            # Unknown identifier — preserve safely as ALL (don't false-warn).
            # Print to stderr so we know about it.
            print(f"  warning: unknown eqtype token '{term}' in expression "
                  f"'{expr}' — falling back to EQTYPE_ALL", file=sys.stderr)
            return EQTYPE_ALL
    return total


def parse_eqc(text: str) -> List[Tuple[str, int, int]]:
    """Parse the RWF dispatch table out of an eq.c source.

    Returns list of (name, param_count, eqtype_mask).
    Order is preserved (matches eq.c).
    """
    # Find the table boundaries: `static RWF rwf[] = {` ... matching `};`
    start = re.search(r"static\s+RWF\s+rwf\s*\[\]\s*=\s*\{", text)
    if not start:
        raise SystemExit("Could not find 'static RWF rwf[] = {' in eq.c")
    body_start = start.end()
    # Find the matching `};` at depth 0
    depth = 1
    i = body_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = text[body_start:i]

    entries = []
    for m in ROW_RE.finditer(body):
        name = m.group(1)
        params = int(m.group(2))
        eqtype = parse_eqtype_expr(m.group(3))
        entries.append((name, params, eqtype))
    return entries


# ---------------------------------------------------------------------------
# Output formatter
# ---------------------------------------------------------------------------

def mask_to_symbols(mask: int) -> List[str]:
    """Decompose a mask into the list of EQTYPE_* symbols it contains."""
    if mask == EQTYPE_ALL:
        return ["EQTYPE_ALL"]
    syms = []
    for bit, name in sorted(BIT_TO_SYMBOL.items()):
        if mask & bit:
            syms.append(name)
    return syms


def format_catalogue(entries: List[Tuple[str, int, int]]) -> str:
    """Render the catalogue as a Python module."""
    lines = [
        "# Auto-generated by support/gen_rwf_catalogue.py — do not edit by hand.",
        "# Source: src/eq.c (RWF dispatch table).",
        "# Regenerate after any change to eq.c by running:",
        "#     python3 support/gen_rwf_catalogue.py",
        "",
        "# Each entry: name -> (param_count, eqtype_mask_int, note_str)",
        "#   eqtype_mask: bitmask of EQTYPE_* flags; 0xFFFF = EQTYPE_ALL",
        "#   note: short pitfall hint surfaced by linter --strict mode",
        "",
        f"EQTYPE_ALL = 0x{EQTYPE_ALL:04X}",
        "",
        "EQTYPE_BITS = {",
    ]
    for sym, bit in sorted(EQ_BITS.items(), key=lambda kv: kv[1]):
        lines.append(f"    {sym!r:24s}: 0x{bit:04X},")
    lines.append("}")
    lines.append("")
    lines.append("SUBROUTINES = {")
    seen = set()
    for name, params, mask in entries:
        # If a name is duplicated in eq.c (it shouldn't be), keep the first
        if name in seen:
            continue
        seen.add(name)
        note = NOTES.get(name, "")
        sym_list = mask_to_symbols(mask)
        comment = " | ".join(sym_list)
        lines.append(
            f"    {name!r:32s}: ({params}, 0x{mask:04X}, "
            f"{note!r}),  # {comment}"
        )
    lines.append("}")
    lines.append("")
    lines.append(f"# Total subroutines: {len(seen)}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def default_paths() -> Tuple[str, str]:
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    source = os.path.join(repo, "src", "eq.c")
    out = os.path.join(here, "rule_catalogue.py")
    return source, out


def main(argv=None):
    src_default, out_default = default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=src_default,
                        help=f"Path to src/eq.c (default: {src_default})")
    parser.add_argument("--out", default=out_default,
                        help=f"Output Python module (default: {out_default})")
    parser.add_argument("--print", action="store_true",
                        help="Print to stdout instead of writing")
    args = parser.parse_args(argv)

    if not os.path.exists(args.source):
        print(f"Source file not found: {args.source}", file=sys.stderr)
        return 2

    with open(args.source) as f:
        text = f.read()

    entries = parse_eqc(text)
    if not entries:
        print("No subroutines parsed — check the regex against eq.c format",
              file=sys.stderr)
        return 2

    output = format_catalogue(entries)
    if args.print:
        print(output)
    else:
        with open(args.out, "w") as f:
            f.write(output)
        print(f"Wrote {len(entries)} subroutines to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
