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
# (subroutine, (x_arg_idx, y_arg_idx, kind, text_idx or None,
#               width_idx or None)) — indices into the comma-split args.
#
# text_idx None means "use a placeholder labelled with the kind".
# For output_results/output_testname/output_units/output_refrange the
# last arg is the test mnemonic; we use [TESTNAME] / [TESTNAME RESULT]
# style placeholders.
POSITION_SUBS: Dict[str, dict] = {
    # output_text(x, y, font, font_size, justify, text)
    "output_text": {"x": 0, "y": 1, "text": 5, "kind": "text"},
    # output_results(x, y, font, size, hfont, hsize, just, mode, flags, test)
    "output_results": {"x": 0, "y": 1, "test": 9, "kind": "field",
                       "label": "RESULT"},
    # output_testname(x, y, font, size, hfont, hsize, just, mode, accred, test)
    "output_testname": {"x": 0, "y": 1, "test": 9, "kind": "field",
                        "label": "TESTNAME"},
    # output_units(x, y, font, size, just, mode, flags, test)
    "output_units": {"x": 0, "y": 1, "test": 7, "kind": "field",
                     "label": "UNITS"},
    # output_refrange(x, y, font, size, just, mode, flags, test)
    "output_refrange": {"x": 0, "y": 1, "test": 7, "kind": "field",
                        "label": "REFRANGE"},
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


# ---------------------------------------------------------- helpers


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


def _emit_for_call(call_name: str, args: List[str], scope: Scope,
                   line: int, result: PreviewResult) -> None:
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
        result.commands.append(RenderCmd(
            x=x, y=y, kind="text", text=text_str,
            width=len(text_str), source_line=line))
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
        # `output_testname` displays the test mnemonic itself; other
        # field kinds display the corresponding value.
        if label == "TESTNAME":
            text_str = test_name or "[TESTNAME]"
        else:
            text_str = f"[{test_name} {label}]" if test_name else f"[{label}]"
        result.commands.append(RenderCmd(
            x=x, y=y, kind="field", text=text_str,
            width=len(text_str), source_line=line))
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
               result: PreviewResult) -> None:
    if stmt.kind == "block":
        for c in stmt.children:
            _walk_stmt(c, source, scope, result)
        return
    if stmt.kind == "if":
        result.branches_expanded += 1
        for c in stmt.children:
            _walk_stmt(c, source, scope, result)
        if stmt.else_branch is not None:
            _walk_stmt(stmt.else_branch, source, scope, result)
        return
    if stmt.kind == "while":
        # Walk body once — loops with literal bounds could be unrolled
        # in a future pass.
        for c in stmt.children:
            _walk_stmt(c, source, scope, result)
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
        _emit_for_call(call_name, args, scope, line, result)
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
                includes: Optional[Dict[str, str]] = None) -> PreviewResult:
    """Top-level entry point. Parses the mask and returns a PreviewResult.

    If `includes` is supplied (name → body map), every include_mask("X")
    call in the source (and in any included body) is substituted before
    parsing. Unresolved or cyclic includes generate warnings.
    """
    result = PreviewResult(grid_width=grid_width, grid_height=grid_height)
    expanded = _resolve_includes(source, includes or {}, result)

    code = rule_lint.strip_comments(expanded)
    statements = rule_lint.parse_statements(code)

    scope = Scope()
    for s in statements:
        _walk_stmt(s, expanded, scope, result)

    return result
