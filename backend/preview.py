"""Static walker over .mask source code.

A .mask file is a rule-engine equation (EQTYPE_REPDISP) that, when
executed, calls positioning subroutines (output_text, output_results, …)
to place text on a character grid. v1 of the previewer can't execute the
equation, so this module *statically walks* the AST and approximates the
output:

  * Variable assignments with literal int / string RHS are tracked in
    scope. Simple arithmetic on tracked vars is resolved.
  * if/else branches: both walked. Result is a "superset" preview.
  * while: body walked once (loop bounds rarely change layout).
  * Known positioning subroutines: emit RenderCmd entries.
  * Anything we can't resolve produces a warning with the source line
    number so the editor can flag the spot.

Returns a (commands, warnings) tuple. The FastAPI route wraps these into
a JSON response.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import rule_lint
from rule_lint import Stmt


# Match include_mask("name") calls so we can substitute them before parsing.
# Mirrors the regex in rule_lint.INCLUDE_RE but kept local to avoid importing
# a private detail. Whitespace and single-quoted forms are also tolerated.
_INCLUDE_CALL_RE = re.compile(
    r"\binclude_mask\s*\(\s*[\"']([^\"']+)[\"']\s*\)\s*;",
    re.IGNORECASE,
)
_INCLUDE_DEPTH_LIMIT = 32


# ---------------------------------------------------------- output table
# Arg-index specs for each positioning subroutine, verified against the
# shipped rule-engine source (/home/andrew/evo/src/eq.c dispatch table
# at ~19256). `just` carries the engine's "w<N>" width hint which we
# parse via _extract_width_hint(); see vprint_getwidth() in vprint.c.
POSITION_SUBS: Dict[str, dict] = {
    # output_text(x, y, font, size, just, text) — 6 args
    "output_text": {"x": 0, "y": 1, "just": 4, "text": 5, "kind": "text"},
    # output_results(x, y, cn, sn, ch, sh, just, fmt_flags, decimals, test) — 10 args
    "output_results": {"x": 0, "y": 1, "just": 6, "test": 9,
                       "kind": "field", "label": "RESULT"},
    # output_testname(x, y, cn, sn, ch, sh, just, mode, accred, test) — 10 args
    "output_testname": {"x": 0, "y": 1, "just": 6, "test": 9,
                        "kind": "field", "label": "TESTNAME"},
    # output_units(x, y, cn, sn, ch, sh, just, test) — 8 args
    "output_units": {"x": 0, "y": 1, "just": 6, "test": 7,
                     "kind": "field", "label": "UNITS"},
    # output_refrange(x, y, cn, sn, ch, sh, just, test) — 8 args
    "output_refrange": {"x": 0, "y": 1, "just": 6, "test": 7,
                        "kind": "field", "label": "REFRANGE"},
    # output_line(x1, y1, x2, y2, ...)
    "output_line": {"x": 0, "y": 1, "x2": 2, "y2": 3, "kind": "line"},
    # output_box(x, y, w, h, ...)
    "output_box": {"x": 0, "y": 1, "w": 2, "h": 3, "kind": "box"},
}


# ---------------------------------------------------------- shapes


@dataclass
class RenderCmd:
    x: int
    y: int
    kind: str             # "text" | "field" | "line" | "box"
    text: str = ""
    width: int = 0
    height: int = 0
    colour: Optional[str] = None
    bold: bool = False
    source_line: int = 0


@dataclass
class PreviewWarning:
    line: int
    message: str


@dataclass
class PreviewResult:
    commands: List[RenderCmd] = field(default_factory=list)
    warnings: List[PreviewWarning] = field(default_factory=list)
    grid_width: int = 120
    grid_height: int = 25
    # Stats for the UI badge — number of statements that were skipped
    # because of all-branches-taken expansion (helpful context).
    branches_expanded: int = 0
    tests_loaded: int = 0
    panels_loaded: int = 0


@dataclass
class TestInfo:
    mnem: str
    display_name: str = ""
    format: str = ""        # e.g. "3N.2N"
    units: str = ""
    decimals: int = 0
    width: int = 0          # derived from format


@dataclass
class Catalogue:
    """Test + panel metadata, parsed from CFtest.tsv and CFpanel.tsv
    exports produced by the rule-engine's dump_testpanel().
    """
    tests: Dict[str, TestInfo] = field(default_factory=dict)   # keyed upper-case
    panels: Dict[str, List[str]] = field(default_factory=dict)  # keyed upper-case


# ---------------------------------------------------------- helpers


# Evolution test formats: "3N.2N" → 3 int digits + decimal + 2 fraction = 6,
# "5N" → 5, "-12N" → 12 (sign reserves a slot). Also tolerate printf-style
# "%-10s" / "%5d" forms. Unknown shapes return 0 and the caller falls back
# to the placeholder's natural length.
_EVO_FMT_RE = re.compile(r"^(-?)(\d+)N(?:\.(\d+)N)?$")
_PRINTF_FMT_RE = re.compile(r"^%-?(\d+)[a-zA-Z]$")


def format_width(fmt: str) -> int:
    if not fmt:
        return 0
    s = fmt.strip()
    m = _EVO_FMT_RE.match(s)
    if m:
        whole = int(m.group(2))
        frac = int(m.group(3)) if m.group(3) else 0
        # Decimal point eats a column when there's a fractional part.
        # Leading `-` is a left-justify flag and doesn't add a slot.
        return whole + (1 + frac if frac else 0)
    m = _PRINTF_FMT_RE.match(s)
    if m:
        return int(m.group(1))
    return 0


_WIDTH_HINT_RE = re.compile(r"w(\d+)", re.IGNORECASE)


def _extract_width_hint(just: str) -> Optional[int]:
    """Parse the rule-engine's "w<N>" hint out of a justify-flags string.
    Mirrors vprint_getwidth() in /home/andrew/evo/lib/vprint.c:1799.
    """
    if not just:
        return None
    m = _WIDTH_HINT_RE.search(just)
    return int(m.group(1)) if m else None


def _norm_headers(row: Dict[str, str]) -> Dict[str, str]:
    """Lower-case + strip header keys so 'Display Name', 'display_name'
    and 'DISPLAY NAME' all match.
    """
    out: Dict[str, str] = {}
    for k, v in row.items():
        if k is None:
            continue
        key = k.strip().lower().replace("_", " ")
        out[key] = (v or "").strip() if isinstance(v, str) else ""
    return out


def parse_cftest(text: str) -> Tuple[Dict[str, TestInfo], List[str]]:
    """Parse a CFtest TSV (the format dump_testpanel() writes). Returns
    (tests-by-uppercase-mnem, warnings). Tolerates extra/missing columns;
    the only required column is 'Mnemonic'.
    """
    warnings: List[str] = []
    tests: Dict[str, TestInfo] = {}
    if not text or not text.strip():
        return tests, warnings
    reader = csv.DictReader(io.StringIO(text), dialect="excel-tab")
    if reader.fieldnames is None:
        warnings.append("CFtest: empty file or no header row")
        return tests, warnings
    headers = {h.strip().lower().replace("_", " "): h for h in reader.fieldnames if h}
    if "mnemonic" not in headers:
        warnings.append("CFtest: missing required 'Mnemonic' column")
        return tests, warnings
    for row in reader:
        norm = _norm_headers(row)
        mnem = norm.get("mnemonic", "")
        if not mnem:
            continue
        try:
            decimals = int(norm.get("precision") or 0)
        except ValueError:
            decimals = 0
        fmt = norm.get("format", "")
        info = TestInfo(
            mnem=mnem,
            display_name=norm.get("display name", ""),
            format=fmt,
            units=norm.get("units", ""),
            decimals=decimals,
            width=format_width(fmt),
        )
        tests[mnem.upper()] = info
    return tests, warnings


def parse_cfpanel(text: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """Parse a CFpanel TSV. Required columns: 'Mnemonic', 'Tests' (a
    comma-separated list of test mnemonics). Returns (panels-by-uppercase-
    mnem, warnings).
    """
    warnings: List[str] = []
    panels: Dict[str, List[str]] = {}
    if not text or not text.strip():
        return panels, warnings
    reader = csv.DictReader(io.StringIO(text), dialect="excel-tab")
    if reader.fieldnames is None:
        warnings.append("CFpanel: empty file or no header row")
        return panels, warnings
    headers = {h.strip().lower().replace("_", " "): h for h in reader.fieldnames if h}
    if "mnemonic" not in headers:
        warnings.append("CFpanel: missing required 'Mnemonic' column")
        return panels, warnings
    if "tests" not in headers:
        warnings.append("CFpanel: missing required 'Tests' column")
        return panels, warnings
    for row in reader:
        norm = _norm_headers(row)
        mnem = norm.get("mnemonic", "")
        if not mnem:
            continue
        tests_field = norm.get("tests", "")
        members = [t.strip() for t in tests_field.split(",") if t.strip()]
        panels[mnem.upper()] = members
    return panels, warnings


def _line_of(text: str, char_index: int) -> int:
    """1-based line number of a character index."""
    return text.count("\n", 0, max(0, char_index)) + 1


def _split_args(arg_src: str) -> List[str]:
    """Split a comma-separated argument string while respecting strings,
    parentheses, and brackets. Returns trimmed arg strings.
    """
    args: List[str] = []
    depth = 0
    cur = []
    in_str: Optional[str] = None
    i = 0
    while i < len(arg_src):
        c = arg_src[i]
        if in_str:
            cur.append(c)
            if c == "\\" and i + 1 < len(arg_src):
                cur.append(arg_src[i + 1])
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in ("\"", "'"):
            in_str = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        args.append(tail)
    return args


_NUM_RE = re.compile(r"^-?\d+$")
_STR_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"$')
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INDEXED_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[(.+)\]$")
_CALL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*;?\s*$",
                      re.DOTALL)
# Used by parse_tlist to find {NAME} entries and bare integer gaps.
_TLIST_TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}|(\d+)")


def parse_tlist(s: str) -> List[Tuple[str, object]]:
    """Parse a tlist string like 'l{ABSTIN}{ANDAGE}9{ACONCN}' into a list
    of (kind, value) items where kind is 'test' (value=str) or 'gap'
    (value=int rows to skip).

    A leading single-char justify flag (l/r/c, any case) is consumed and
    discarded. Malformed brace pairs (e.g. '[MANVOL}' typo) are skipped.
    """
    if not s:
        return []
    # Skip leading justify flag if present
    i = 0
    if s[0] in "lLrRcC" and len(s) > 1 and s[1] in "{0123456789":
        i = 1
    items: List[Tuple[str, object]] = []
    while i < len(s):
        m = _TLIST_TOKEN_RE.match(s, i)
        if not m:
            i += 1
            continue
        name, gap = m.group(1), m.group(2)
        if name is not None:
            items.append(("test", name))
        else:
            items.append(("gap", int(gap)))
        i = m.end()
    return items


def _looks_like_tlist(s: str) -> bool:
    """Heuristic: does this string contain at least one {NAME} pair?"""
    return "{" in s and "}" in s


# --------------------------------------------------------- if-condition eval

_TEST_ORDERED_RE = re.compile(
    r"\btest_ordered\s*\(\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1\s*\)",
    re.IGNORECASE,
)
_COND_WHITELIST_RE = re.compile(
    r"\b(True|False|and|or|not)\b|[\s()]",
)


def evaluate_condition(cond: str, fixture: Optional[set]) -> Optional[bool]:
    """Try to evaluate an if/while condition against an 'ordered tests'
    fixture. Returns True/False if the condition can be fully reduced
    to a boolean, None if anything we don't understand remains
    (caller should fall back to walking both branches).

    Supported:
      - test_ordered("X") or test_ordered(X) → membership in fixture
      - & (AND), | (OR), ! (NOT), parentheses
    Not supported (falls back to None):
      - comparisons (==, !=, <, >, <=, >=)
      - any other subroutine call
      - bare numeric/string literals
    """
    if fixture is None:
        return None

    def _sub(m: "re.Match[str]") -> str:
        # Fixture is normalised to upper-case in render_mask; do the
        # same for the source-side name so SEMCON / semcon both match.
        name = m.group(2).upper()
        return "True" if name in fixture else "False"

    expr = _TEST_ORDERED_RE.sub(_sub, cond)
    # Translate operators. `!` only when not followed by `=` to keep `!=`
    # intact (so it falls through to the whitelist and is rejected).
    expr = re.sub(r"!(?!=)", " not ", expr)
    expr = expr.replace("&", " and ").replace("|", " or ")

    # Whitelist: only literals + boolean operators + parens + whitespace.
    residue = _COND_WHITELIST_RE.sub("", expr)
    if residue:
        return None

    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))   # noqa: S307
    except Exception:
        return None


class Scope:
    """Tracks literal-int / literal-string variable assignments and
    simple integer arithmetic, including 1-D arrays. Anything we can't
    resolve raises ValueError so the walker can record a warning.
    """

    def __init__(self) -> None:
        self._vars: Dict[str, object] = {}
        self._arrays: Dict[str, Dict[int, object]] = {}

    def assign_simple(self, lhs: str, rhs: str) -> bool:
        """Store one assignment. Returns True on success, False on miss.
        Misses are silent — the walker only records warnings on usage.
        """
        m = _INDEXED_RE.match(lhs.strip())
        if m:
            name = m.group(1)
            try:
                idx = self.eval_int(m.group(2))
            except ValueError:
                return False
            try:
                val = self.eval_value(rhs)
            except ValueError:
                return False
            self._arrays.setdefault(name, {})[idx] = val
            return True
        if _IDENT_RE.match(lhs.strip()):
            name = lhs.strip()
            try:
                val = self.eval_value(rhs)
            except ValueError:
                return False
            self._vars[name] = val
            return True
        return False

    def eval_value(self, expr: str) -> object:
        """Try string literal, then int / int arithmetic, then variable /
        array lookup of any type. Raises ValueError if none resolve.
        """
        s = expr.strip()
        m = _STR_RE.match(s)
        if m:
            return m.group(1).encode().decode("unicode_escape")
        # Integer / arithmetic
        try:
            return self.eval_int(s)
        except ValueError:
            pass
        # Direct variable / array lookup of any type (covers string arrays
        # like heading[1] that eval_int can't return because it rejects
        # non-int contents).
        m = _INDEXED_RE.match(s)
        if m:
            arr = self._arrays.get(m.group(1))
            try:
                idx = self.eval_int(m.group(2))
            except ValueError:
                idx = None
            if arr is not None and idx is not None and idx in arr:
                return arr[idx]
        if _IDENT_RE.match(s) and s in self._vars:
            return self._vars[s]
        raise ValueError(f"unresolved: {s!r}")

    def eval_int(self, expr: str) -> int:
        """Evaluate a simple integer expression: literal, variable,
        array index, or sum/difference of those. Anything fancier raises
        ValueError.
        """
        s = expr.strip()
        if _NUM_RE.match(s):
            return int(s)

        # Sum / difference at top level (respect brackets)
        ops = list(_top_level_addops(s))
        if ops:
            total = 0
            sign = 1
            cursor = 0
            for op_idx, op in ops + [(len(s), "+")]:
                piece = s[cursor:op_idx].strip()
                if piece:
                    total += sign * self._eval_atom_int(piece)
                sign = 1 if op == "+" else -1
                cursor = op_idx + 1
            return total

        return self._eval_atom_int(s)

    def _eval_atom_int(self, atom: str) -> int:
        # Plain integer literal — the add-op split feeds these in too.
        if _NUM_RE.match(atom):
            return int(atom)
        m = _INDEXED_RE.match(atom)
        if m:
            name = m.group(1)
            idx = self.eval_int(m.group(2))
            arr = self._arrays.get(name)
            if arr and idx in arr and isinstance(arr[idx], int):
                return arr[idx]
            raise ValueError(f"unresolved array element: {atom}")
        if _IDENT_RE.match(atom):
            val = self._vars.get(atom)
            if isinstance(val, int):
                return val
            raise ValueError(f"unresolved variable: {atom}")
        raise ValueError(f"not an integer atom: {atom!r}")


def _top_level_addops(s: str) -> List[Tuple[int, str]]:
    """Find indices of `+`/`-` operators at bracket-depth 0. Skip the
    very first char so a leading `-` (unary minus) doesn't count.
    """
    out: List[Tuple[int, str]] = []
    depth = 0
    for i, c in enumerate(s):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and i > 0 and c in "+-":
            # Skip if the preceding char is another operator (handles
            # things like `x * -1`).
            prev = s[i - 1]
            if prev in "+-*/<>=,(":
                continue
            out.append((i, c))
    return out


# ---------------------------------------------------------- main walker


def _resolve_just(spec: dict, args: List[str], scope: Scope) -> str:
    """Resolve the just-string arg (if any) to a literal. Unresolved →
    empty string so width-hint extraction simply returns None."""
    idx = spec.get("just")
    if idx is None or idx >= len(args):
        return ""
    try:
        val = scope.eval_value(args[idx])
    except ValueError:
        return ""
    return str(val) if val is not None else ""


def _placeholder_for(label: str, test_name: str) -> str:
    """v1 fallback: bracketed placeholder used when no catalogue is
    available for a given test mnemonic.
    """
    if label == "TESTNAME":
        return test_name or "[TESTNAME]"
    if label == "UNITS":
        return "[u]"
    if label == "REFRANGE":
        return "[ref]"
    return f"[{test_name} {label}]" if test_name else f"[{label}]"


def _cell_for_test(label: str, test_name: str,
                   catalogue: Optional["Catalogue"]) -> Tuple[str, int]:
    """Pick (text, format-width) for a single test mnem + label combo,
    consulting the catalogue when present. format-width may be 0 ('use
    len(text)') if no format is registered for the test.
    """
    info: Optional[TestInfo] = None
    if catalogue is not None:
        info = catalogue.tests.get(test_name.upper())
    if info is None:
        return _placeholder_for(label, test_name), 0

    if label == "TESTNAME":
        text = info.display_name or info.mnem
    elif label == "UNITS":
        text = info.units or "[u]"
    elif label == "REFRANGE":
        text = "[ref]"
    elif label == "RESULT":
        text = "·" * info.width if info.width > 0 else f"[{info.mnem}]"
    else:
        text = f"[{info.mnem} {label}]"
    return text, info.width


def _emit_for_call(call_name: str, args: List[str], scope: Scope,
                   line: int, result: PreviewResult,
                   catalogue: Optional["Catalogue"] = None) -> None:
    spec = POSITION_SUBS.get(call_name)
    if spec is None:
        return
    try:
        x = scope.eval_int(args[spec["x"]])
        y = scope.eval_int(args[spec["y"]])
    except (ValueError, IndexError) as exc:
        result.warnings.append(PreviewWarning(
            line=line,
            message=f"{call_name}: could not resolve coordinates ({exc})"))
        return

    kind = spec["kind"]
    hint = _extract_width_hint(_resolve_just(spec, args, scope))

    if kind == "text" and "text" in spec:
        idx = spec["text"]
        if idx >= len(args):
            result.warnings.append(PreviewWarning(
                line=line, message=f"{call_name}: missing text arg"))
            return
        try:
            text_val = scope.eval_value(args[idx])
        except ValueError:
            # Use the source slice as a fallback placeholder.
            text_val = f"[{args[idx]}]"
        text_str = str(text_val)
        width = hint or len(text_str)
        result.commands.append(RenderCmd(
            x=x, y=y, kind="text", text=text_str,
            width=width, source_line=line))
        return

    if kind == "field" and "test" in spec:
        idx = spec["test"]
        label = spec.get("label", "FIELD")
        test_name = args[idx].strip() if idx < len(args) else ""
        try:
            resolved = scope.eval_value(test_name)
            if isinstance(resolved, str):
                test_name = resolved
        except ValueError:
            pass

        # tlist expansion: a string like 'l{T1}{T2}9{T3}' is a vertical
        # test-list. The runtime stacks one test per row, with digit
        # tokens between names acting as blank-row gaps. Render one
        # cell per test entry.
        if _looks_like_tlist(test_name):
            cy = y
            for kind_tag, value in parse_tlist(test_name):
                if kind_tag == "gap":
                    cy += int(value)  # type: ignore[arg-type]
                    continue
                tname = str(value)
                cell_text, fmt_w = _cell_for_test(label, tname, catalogue)
                width = hint or fmt_w or len(cell_text)
                result.commands.append(RenderCmd(
                    x=x, y=cy, kind="field", text=cell_text,
                    width=width, source_line=line))
                cy += 1
            return

        # Panel expansion: a bare mnem registered in CFpanel expands into
        # one vertical cell per member test, same as a tlist.
        if (catalogue is not None
                and test_name
                and test_name.upper() in catalogue.panels):
            members = catalogue.panels[test_name.upper()]
            if not members:
                result.warnings.append(PreviewWarning(
                    line=line,
                    message=f"{call_name}: panel {test_name!r} is empty"))
                return
            cy = y
            for member in members:
                cell_text, fmt_w = _cell_for_test(label, member, catalogue)
                width = hint or fmt_w or len(cell_text)
                result.commands.append(RenderCmd(
                    x=x, y=cy, kind="field", text=cell_text,
                    width=width, source_line=line))
                cy += 1
            return

        # Plain single-test argument — one cell.
        cell_text, fmt_w = _cell_for_test(label, test_name, catalogue)
        width = hint or fmt_w or len(cell_text)
        result.commands.append(RenderCmd(
            x=x, y=y, kind="field", text=cell_text,
            width=width, source_line=line))
        return

    if kind == "line":
        try:
            x2 = scope.eval_int(args[spec["x2"]])
            y2 = scope.eval_int(args[spec["y2"]])
        except (ValueError, IndexError):
            return
        # Horizontal line only — vertical/diagonal not really used in masks
        if y2 == y:
            result.commands.append(RenderCmd(
                x=min(x, x2), y=y, kind="line",
                text="─" * (abs(x2 - x) + 1),
                width=abs(x2 - x) + 1, source_line=line))
        return

    if kind == "box":
        try:
            w = scope.eval_int(args[spec["w"]])
            h = scope.eval_int(args[spec["h"]])
        except (ValueError, IndexError):
            return
        result.commands.append(RenderCmd(
            x=x, y=y, kind="box", text="",
            width=w, height=h, source_line=line))
        return


def _walk_stmt(stmt: Stmt, source: str, scope: Scope,
               result: PreviewResult,
               fixture: Optional[set] = None,
               catalogue: Optional[Catalogue] = None) -> None:
    if stmt.kind == "block":
        for c in stmt.children:
            _walk_stmt(c, source, scope, result, fixture, catalogue)
        return
    if stmt.kind == "if":
        result.branches_expanded += 1
        cond_val = evaluate_condition(stmt.cond_text, fixture)
        if cond_val is True:
            # Only walk then-branch
            for c in stmt.children:
                _walk_stmt(c, source, scope, result, fixture, catalogue)
        elif cond_val is False:
            # Only walk else-branch if present
            if stmt.else_branch is not None:
                _walk_stmt(stmt.else_branch, source, scope, result,
                           fixture, catalogue)
        else:
            # Fixture-unknown — walk both (superset).
            for c in stmt.children:
                _walk_stmt(c, source, scope, result, fixture, catalogue)
            if stmt.else_branch is not None:
                _walk_stmt(stmt.else_branch, source, scope, result,
                           fixture, catalogue)
        return
    if stmt.kind == "while":
        # Walk body once — loops with literal bounds could be unrolled
        # in a future pass.
        for c in stmt.children:
            _walk_stmt(c, source, scope, result, fixture, catalogue)
        return
    if stmt.kind == "exit":
        return
    if stmt.kind == "assign":
        # The Stmt.target only captures the bare LHS identifier; for
        # arr[i] = … we need to look at the assignment text directly.
        body = stmt.text.rstrip("; \t\n")
        eq_idx = _find_assignment_op(body)
        if eq_idx >= 0:
            lhs = body[:eq_idx].strip()
            rhs = body[eq_idx + 1:].strip()
            scope.assign_simple(lhs, rhs)
        return
    if stmt.kind == "expr":
        body = stmt.text.strip().rstrip(";")
        m = _CALL_RE.match(body)
        if not m:
            return
        call_name = m.group(1).lower()
        args = _split_args(m.group(2))
        line = _line_of(source, stmt.start)
        _emit_for_call(call_name, args, scope, line, result, catalogue)
        return


def _find_assignment_op(s: str) -> int:
    """Return index of the top-level `=` that isn't `==`/`!=`/`<=`/`>=`.
    -1 if none.
    """
    depth = 0
    in_str: Optional[str] = None
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\" and i + 1 < len(s):
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'":
            in_str = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == "=":
            prev = s[i - 1] if i > 0 else ""
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if prev in "=!<>" or nxt == "=":
                i += 1
                continue
            return i
        i += 1
    return -1


def _resolve_includes(source: str, includes: Dict[str, str],
                      result: "PreviewResult", *,
                      depth: int = 0, seen: Optional[set] = None) -> str:
    """Substitute every include_mask("X") call with the body of X from the
    includes map. Recurses for nested includes; tracks 'seen' to break
    cycles. Unresolved includes are replaced with an empty body and a
    warning is recorded on result.warnings.
    """
    if depth > _INCLUDE_DEPTH_LIMIT:
        result.warnings.append(PreviewWarning(
            line=0,
            message=f"include depth limit ({_INCLUDE_DEPTH_LIMIT}) exceeded"))
        return source
    seen = set() if seen is None else seen
    norm = {k.lower(): v for k, v in includes.items()}

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        key = name.lower()
        if key in seen:
            result.warnings.append(PreviewWarning(
                line=source.count("\n", 0, m.start()) + 1,
                message=f"include_mask({name!r}) is recursive — skipped"))
            return ""
        body = norm.get(key)
        if body is None:
            result.warnings.append(PreviewWarning(
                line=source.count("\n", 0, m.start()) + 1,
                message=f"include_mask({name!r}) not provided — body skipped"))
            return ""
        # Recurse for nested includes
        sub_seen = seen | {key}
        expanded = _resolve_includes(body, includes, result,
                                     depth=depth + 1, seen=sub_seen)
        return f"\n/* >>> include_mask({name!r}) */\n{expanded}\n/* <<< */\n"

    return _INCLUDE_CALL_RE.sub(_sub, source)


def render_mask(source: str, *, grid_width: int = 120,
                grid_height: int = 25,
                includes: Optional[Dict[str, str]] = None,
                ordered_tests: Optional[List[str]] = None,
                catalogue: Optional[Catalogue] = None) -> PreviewResult:
    """Top-level entry point. Parses the mask and returns a PreviewResult.

    If `includes` is supplied (name → body map), every include_mask("X")
    call in the source (and in any included body) is substituted before
    parsing. Unresolved or cyclic includes generate warnings.

    If `ordered_tests` is supplied (list of test mnemonics), if-conditions
    that reduce to membership tests against this set are evaluated and
    only the matching branch is walked. When None or when a condition
    can't be fully reduced (e.g. contains comparisons or unknown calls),
    the walker falls back to the original superset behaviour (both
    branches walked).

    If `catalogue` is supplied, test mnemonics passed to output_results
    etc. are resolved to display names / units / format widths, and
    panel mnemonics expand into one vertical cell per member test.
    Without a catalogue, the v1 placeholder behaviour is preserved.
    """
    result = PreviewResult(grid_width=grid_width, grid_height=grid_height)
    if catalogue is not None:
        result.tests_loaded = len(catalogue.tests)
        result.panels_loaded = len(catalogue.panels)
    expanded = _resolve_includes(source, includes or {}, result)

    code = rule_lint.strip_comments(expanded)
    statements = rule_lint.parse_statements(code)

    fixture: Optional[set] = None
    if ordered_tests is not None:
        fixture = {t.strip().upper() for t in ordered_tests if t and t.strip()}

    scope = Scope()
    for s in statements:
        _walk_stmt(s, expanded, scope, result, fixture, catalogue)

    return result
