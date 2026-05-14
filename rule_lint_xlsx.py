"""rule_lint_xlsx.py — workflow-spreadsheet importer.

Reads a structured XLSX (or CSV) workflow specification and emits draft .eq
rule-engine files, grouped by Action Category.

Stdlib only — XLSX is unzipped and parsed with zipfile + xml.etree.ElementTree
to keep the project zero-dep.

Spreadsheet template (header row required, case-insensitive, any order):

    ERN                Rule identifier, e.g. B.00521.7.01
    Department         BIOCHM, HAEM, MICRO, ... (drives Modify file split)
    Workflow           Free-text label, e.g. AFP, RP, LIPIDS
    Action Category    One of: "Req Add", "Req Delete", "Modify"
    Trigger Test       Primary test mnemonic, e.g. AFP, K, CREAT
    Trigger Op         ordered | resulted | numeric | < | <= | > | >= | = |
                       range | critical_high | critical_low | flag_h | flag_l
    Trigger Value      Numeric value or range, e.g. 5.0 or 5.0-10.0
    Extra Condition    Optional extra clauses, ';'-separated, same grammar as
                       the trigger columns (e.g. "value:UREA>10; age:>16")
    Action Target      Depends on category. For Req Add/Delete this is a test
                       mnemonic. For Modify the form is "<verb>:<args>" — see
                       MODIFY_VERBS below.
    Notes              Free text, becomes a /* comment */ in the output

Output files are written to the user-chosen directory:

    requests_add.eq            all "Req Add" rows
    requests_delete.eq         all "Req Delete" rows
    modify_<DEPARTMENT>.eq     "Modify" rows grouped by Department

Each emitted equation is annotated with the source ERN. The generator emits
real subroutine calls (add_request, listinsert_test, validate, ...) using the
catalogue from rule_catalogue.py. Where the input is ambiguous, a TODO comment
is left in place for a human reviewer.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# Column schema
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = ("ern", "department", "action category", "trigger test")
KNOWN_COLUMNS = (
    "ern", "department", "workflow", "action category",
    "trigger test", "trigger op", "trigger value",
    "extra condition", "action target", "notes",
)

CATEGORY_ALIASES = {
    "req add": "REQ_ADD",
    "request add": "REQ_ADD",
    "add": "REQ_ADD",
    "req delete": "REQ_DELETE",
    "request delete": "REQ_DELETE",
    "delete": "REQ_DELETE",
    "remove": "REQ_DELETE",
    "modify": "MODIFY",
}

TRIGGER_OPS = {
    "ordered", "not_ordered", "resulted", "not_resulted",
    "numeric", "<", "<=", ">", ">=", "=", "==",
    "range", "critical_high", "critical_low",
    "flag_h", "flag_l", "flag_x",
}

# Modify verb → (subroutine_name, expected_args)
# Form: "verb:test[=value]" or "verb:test->list" for add_to_list / unlist
MODIFY_VERBS = {
    "value": "test_setvalue",          # value:TEST=5.0
    "coded": "test_setvalue",          # coded:TEST=CODE
    "list": "listinsert_test",         # list:TEST->LISTNAME
    "unlist": "listremove_test",       # unlist:TEST->LISTNAME
    "validate": "validate",            # validate:TEST
    "note": "add_testnote",            # note:TEST=COMMENT_CODE
    "request_add": "add_request",      # request_add:TEST
    "request_delete": "remove_request",  # request_delete:TEST
    "calc": "test_setvalue",           # calc:TEST=EXPR
}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class WorkflowRow:
    """One row from the spreadsheet, post-normalisation."""
    row_number: int            # 1-based, for error messages (header is row 1)
    ern: str
    department: str
    workflow: str
    category: str              # REQ_ADD | REQ_DELETE | MODIFY
    trigger_test: str
    trigger_op: str
    trigger_value: str
    extra_condition: str
    action_target: str
    notes: str

    @property
    def label(self) -> str:
        return f"{self.ern} ({self.workflow})" if self.workflow else self.ern


@dataclass
class ImportIssue:
    row_number: int
    severity: str   # "error" | "warning"
    message: str


@dataclass
class ImportResult:
    rows: List[WorkflowRow] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)   # filename -> content
    issues: List[ImportIssue] = field(default_factory=list)
    skipped: int = 0

    @property
    def errors(self) -> List[ImportIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ImportIssue]:
        return [i for i in self.issues if i.severity == "warning"]


# ---------------------------------------------------------------------------
# XLSX reader (zipfile + xml.etree)
# ---------------------------------------------------------------------------

_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col_letter_to_index(letters: str) -> int:
    """A -> 0, B -> 1, ..., AA -> 26."""
    n = 0
    for c in letters:
        if not c.isalpha():
            break
        n = n * 26 + (ord(c.upper()) - ord("A") + 1)
    return n - 1


def _split_ref(ref: str) -> Tuple[int, int]:
    """'B7' -> (col_index=1, row_index=6)."""
    m = re.match(r"^([A-Z]+)(\d+)$", ref)
    if not m:
        return (0, 0)
    return (_col_letter_to_index(m.group(1)), int(m.group(2)) - 1)


def read_xlsx(path: str) -> List[List[str]]:
    """Read first worksheet of an XLSX file, return rows of strings.

    Empty cells become "". Trailing empty rows are stripped.
    """
    rows: List[List[str]] = []
    with zipfile.ZipFile(path) as zf:
        # Shared strings table (may be missing on minimal workbooks)
        shared: List[str] = []
        try:
            with zf.open("xl/sharedStrings.xml") as f:
                root = ET.parse(f).getroot()
                for si in root.findall("x:si", _NS):
                    # Concatenate all <t> children (handles rich text runs)
                    parts = [t.text or "" for t in si.iter(f"{{{_NS['x']}}}t")]
                    shared.append("".join(parts))
        except KeyError:
            pass

        # Find first sheet's xml. workbook.xml lists sheets in order.
        sheet_target = "xl/worksheets/sheet1.xml"
        try:
            with zf.open("xl/_rels/workbook.xml.rels") as f:
                rels_root = ET.parse(f).getroot()
                ns_rel = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
                with zf.open("xl/workbook.xml") as wbf:
                    wb_root = ET.parse(wbf).getroot()
                    first = wb_root.find("x:sheets/x:sheet", _NS)
                    if first is not None:
                        rid = first.get(f"{{http://schemas.openxmlformats.org/officeDocument/2006/relationships}}id")
                        for rel in rels_root.findall("r:Relationship", ns_rel):
                            if rel.get("Id") == rid:
                                target = rel.get("Target", "")
                                if target.startswith("/"):
                                    sheet_target = target[1:]
                                else:
                                    sheet_target = "xl/" + target
                                break
        except KeyError:
            pass

        with zf.open(sheet_target) as f:
            tree = ET.parse(f)
            sheet_root = tree.getroot()
            sheet_data = sheet_root.find("x:sheetData", _NS)
            if sheet_data is None:
                return rows

            for row_el in sheet_data.findall("x:row", _NS):
                row_cells: List[str] = []
                max_col = -1
                cells: Dict[int, str] = {}
                for c in row_el.findall("x:c", _NS):
                    ref = c.get("r", "")
                    col_idx, _ = _split_ref(ref)
                    if col_idx > max_col:
                        max_col = col_idx
                    cell_type = c.get("t", "n")
                    v = c.find("x:v", _NS)
                    if cell_type == "s":
                        try:
                            cells[col_idx] = shared[int(v.text)] if v is not None else ""
                        except (ValueError, IndexError):
                            cells[col_idx] = ""
                    elif cell_type == "inlineStr":
                        # <is><t>...</t></is>
                        is_el = c.find("x:is", _NS)
                        if is_el is not None:
                            cells[col_idx] = "".join(
                                t.text or "" for t in is_el.iter(f"{{{_NS['x']}}}t"))
                        else:
                            cells[col_idx] = ""
                    elif cell_type == "b":
                        cells[col_idx] = "true" if v is not None and v.text == "1" else "false"
                    else:
                        cells[col_idx] = v.text if v is not None and v.text is not None else ""

                if max_col < 0:
                    rows.append([])
                    continue
                row_cells = [cells.get(i, "") for i in range(max_col + 1)]
                rows.append(row_cells)

    # Strip trailing fully-empty rows
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    return rows


def read_csv(path: str) -> List[List[str]]:
    """Read a CSV with the same column shape, for users who prefer CSV."""
    rows: List[List[str]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for r in reader:
            rows.append([cell.strip() for cell in r])
    return rows


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------

def _normalise_header(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def parse_rows(raw: Sequence[Sequence[str]]) -> Tuple[List[WorkflowRow], List[ImportIssue]]:
    """Validate header, convert rows to WorkflowRow objects.

    Returns (rows, issues). Rows with a fatal error are skipped but reported.
    """
    issues: List[ImportIssue] = []
    if not raw:
        issues.append(ImportIssue(0, "error", "Spreadsheet is empty."))
        return [], issues

    header = [_normalise_header(h) for h in raw[0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        issues.append(ImportIssue(
            1, "error",
            f"Header row is missing required columns: {', '.join(missing)}"))
        return [], issues

    # Build column index map
    col_idx: Dict[str, int] = {}
    for i, h in enumerate(header):
        if h in KNOWN_COLUMNS and h not in col_idx:
            col_idx[h] = i
        elif h and h not in KNOWN_COLUMNS:
            issues.append(ImportIssue(
                1, "warning",
                f"Unknown column '{raw[0][i]}' will be ignored."))

    def cell(row: Sequence[str], name: str) -> str:
        idx = col_idx.get(name)
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()

    rows: List[WorkflowRow] = []
    for r_idx, row in enumerate(raw[1:], start=2):  # row 2 = first data row
        if not any(c.strip() for c in row):
            continue   # blank spacer row, silently skip

        ern = cell(row, "ern")
        dept = cell(row, "department")
        cat_raw = cell(row, "action category").lower()
        trig_test = cell(row, "trigger test")

        if not ern:
            issues.append(ImportIssue(r_idx, "error", "ERN is empty."))
            continue
        if not dept:
            issues.append(ImportIssue(r_idx, "error", f"{ern}: Department is empty."))
            continue
        if not cat_raw:
            issues.append(ImportIssue(r_idx, "error", f"{ern}: Action Category is empty."))
            continue
        cat = CATEGORY_ALIASES.get(cat_raw)
        if cat is None:
            issues.append(ImportIssue(
                r_idx, "error",
                f"{ern}: Action Category '{cat_raw}' is not recognised. "
                f"Use one of: Req Add, Req Delete, Modify."))
            continue
        if not trig_test:
            issues.append(ImportIssue(r_idx, "error", f"{ern}: Trigger Test is empty."))
            continue

        rows.append(WorkflowRow(
            row_number=r_idx,
            ern=ern,
            department=dept.upper(),
            workflow=cell(row, "workflow"),
            category=cat,
            trigger_test=trig_test.upper(),
            trigger_op=cell(row, "trigger op").lower(),
            trigger_value=cell(row, "trigger value"),
            extra_condition=cell(row, "extra condition"),
            action_target=cell(row, "action target"),
            notes=cell(row, "notes"),
        ))

    return rows, issues


# ---------------------------------------------------------------------------
# Trigger / action code synthesis
# ---------------------------------------------------------------------------

def _quote(s: str) -> str:
    """Quote a test mnemonic for inclusion in a rule-engine string literal."""
    return '"' + s.replace('"', r'\"') + '"'


def _emit_condition(test: str, op: str, value: str,
                    issues: List[ImportIssue], row_no: int) -> Optional[str]:
    """Produce a single boolean clause for one (test, op, value)."""
    test = test.upper()
    op = op.strip().lower()

    if op in ("ordered", ""):
        return f"test_ordered({_quote(test)})"
    if op == "not_ordered":
        return f"not test_ordered({_quote(test)})"
    if op == "resulted":
        return f"test_result({_quote(test)}) != \"\""
    if op == "not_resulted":
        return f"test_result({_quote(test)}) == \"\""
    if op == "numeric":
        # ee_value (test, dataset=0, what=0) returns numeric value; non-numeric → 0
        return (f"test_result({_quote(test)}) != \"\" and "
                f"ee_value({_quote(test)}, 0, 0) > 0")
    if op in ("<", "<=", ">", ">=", "=", "=="):
        py_op = "==" if op in ("=", "==") else op
        try:
            float(value)
        except ValueError:
            issues.append(ImportIssue(
                row_no, "error",
                f"Operator '{op}' requires a numeric Trigger Value, got '{value}'."))
            return None
        return f"ee_value({_quote(test)}, 0, 0) {py_op} {value}"
    if op == "range":
        m = re.match(r"^\s*([-\d.]+)\s*[-:]\s*([-\d.]+)\s*$", value)
        if not m:
            issues.append(ImportIssue(
                row_no, "error",
                f"Operator 'range' expects 'low-high' Trigger Value, got '{value}'."))
            return None
        lo, hi = m.group(1), m.group(2)
        return (f"ee_value({_quote(test)}, 0, 0) >= {lo} and "
                f"ee_value({_quote(test)}, 0, 0) <= {hi}")
    if op == "critical_high":
        return f"h1flagmatch({_quote(test)}, \"HH\")"
    if op == "critical_low":
        return f"h1flagmatch({_quote(test)}, \"LL\")"
    if op == "flag_h":
        return f"h1flagmatch({_quote(test)}, \"H\")"
    if op == "flag_l":
        return f"h1flagmatch({_quote(test)}, \"L\")"
    if op == "flag_x":
        return f"h1flagmatch({_quote(test)}, \"X\")"

    issues.append(ImportIssue(
        row_no, "error",
        f"Trigger Op '{op}' is not recognised. See template documentation."))
    return None


# Each entry: prefix => (op, value-string).  Used to parse Extra Condition.
_EXTRA_PREFIX = re.compile(r"^\s*([a-z_]+)\s*:\s*(.*)$", re.I)
_AGE_RE = re.compile(r"^\s*(>=|<=|>|<|=)\s*(\d+)\s*$")
_FACILITY_RE = re.compile(r"^\s*([A-Za-z]+)\s*$")


def _emit_extra(clause: str, issues: List[ImportIssue], row_no: int) -> Optional[str]:
    """Parse one extra-condition clause and return rule-engine code, or None.

    Grammar (all case-insensitive):
        ordered:TEST
        resulted:TEST
        value:TEST<X        also >  >=  <=  =  ==
        range:TEST=LO-HI
        critical:TEST       short for critical_high
        flag:TEST=H         flag letter
        age:>16             >, <, >=, <=, =
        sex:M               M | F
        inpatient:yes       yes | no
        facility:GP         GP | HOSP | ...
        coded:TEST=CODE     compares test_result to literal CODE
    """
    clause = clause.strip()
    if not clause:
        return None
    m = _EXTRA_PREFIX.match(clause)
    if not m:
        issues.append(ImportIssue(
            row_no, "error",
            f"Extra Condition '{clause}' must use prefix:value form."))
        return None
    prefix, rest = m.group(1).lower(), m.group(2).strip()

    if prefix in ("ordered", "not_ordered", "resulted", "not_resulted", "numeric"):
        return _emit_condition(rest, prefix, "", issues, row_no)

    if prefix == "value":
        # rest looks like TEST<5 or TEST>=10
        mm = re.match(r"^([A-Z0-9_]+)\s*(<=|>=|<|>|==|=)\s*(-?\d+(?:\.\d+)?)$",
                      rest, re.I)
        if not mm:
            issues.append(ImportIssue(
                row_no, "error", f"value:{rest} — expected TESTOP VALUE."))
            return None
        return _emit_condition(mm.group(1), mm.group(2), mm.group(3),
                               issues, row_no)

    if prefix == "range":
        mm = re.match(r"^([A-Z0-9_]+)\s*=\s*(.+)$", rest, re.I)
        if not mm:
            issues.append(ImportIssue(
                row_no, "error",
                f"range:{rest} — expected TEST=LO-HI."))
            return None
        return _emit_condition(mm.group(1), "range", mm.group(2),
                               issues, row_no)

    if prefix in ("critical", "critical_high"):
        return _emit_condition(rest, "critical_high", "", issues, row_no)
    if prefix == "critical_low":
        return _emit_condition(rest, "critical_low", "", issues, row_no)

    if prefix == "flag":
        mm = re.match(r"^([A-Z0-9_]+)\s*=\s*([A-Z]+)$", rest, re.I)
        if not mm:
            issues.append(ImportIssue(
                row_no, "error", f"flag:{rest} — expected TEST=FLAG."))
            return None
        flag_op = "flag_" + mm.group(2).lower()
        return _emit_condition(mm.group(1), flag_op, "", issues, row_no)

    if prefix == "age":
        mm = _AGE_RE.match(rest)
        if not mm:
            issues.append(ImportIssue(
                row_no, "error",
                f"age:{rest} — expected operator + number (e.g. >16)."))
            return None
        op, val = mm.group(1), mm.group(2)
        py_op = "==" if op == "=" else op
        # AGE_DAYS is the rule-engine system variable; convert years → days
        return f"AGE_DAYS {py_op} {int(val) * 365}"

    if prefix == "sex":
        v = rest.upper()
        if v not in ("M", "F", "U"):
            issues.append(ImportIssue(
                row_no, "warning",
                f"sex:{rest} — expected M, F or U; using literal anyway."))
        return f"SEX == {_quote(v)}"

    if prefix == "inpatient":
        truthy = rest.lower() in ("yes", "y", "true", "1")
        return ("INPATIENT == 1" if truthy else "INPATIENT == 0")

    if prefix == "facility":
        return f"FACILITY_TYPE == {_quote(rest.upper())}"

    if prefix == "coded":
        mm = re.match(r"^([A-Z0-9_]+)\s*=\s*([A-Z0-9_]+)$", rest, re.I)
        if not mm:
            issues.append(ImportIssue(
                row_no, "error", f"coded:{rest} — expected TEST=CODE."))
            return None
        return f"test_result({_quote(mm.group(1))}) == {_quote(mm.group(2))}"

    issues.append(ImportIssue(
        row_no, "error",
        f"Unknown extra-condition prefix '{prefix}'. "
        f"Known: ordered, resulted, value, range, critical, flag, age, sex, "
        f"inpatient, facility, coded."))
    return None


def _emit_action(row: WorkflowRow,
                 issues: List[ImportIssue]) -> Optional[str]:
    """Return the body of the if-block — one or more action statements."""
    if row.category == "REQ_ADD":
        target = row.action_target.strip().upper() or row.trigger_test
        return f"    add_request({_quote(target)});"
    if row.category == "REQ_DELETE":
        target = row.action_target.strip().upper() or row.trigger_test
        return f"    remove_request({_quote(target)});"

    # MODIFY: parse "verb:args"
    target = row.action_target.strip()
    if not target:
        issues.append(ImportIssue(
            row.row_number, "error",
            f"{row.ern}: Modify rows require an Action Target."))
        return None

    statements: List[str] = []
    for piece in re.split(r"\s*;\s*", target):
        if not piece:
            continue
        m = _EXTRA_PREFIX.match(piece)
        if not m:
            issues.append(ImportIssue(
                row.row_number, "error",
                f"{row.ern}: Modify target '{piece}' must use verb:args form."))
            continue
        verb, args = m.group(1).lower(), m.group(2).strip()
        if verb not in MODIFY_VERBS:
            issues.append(ImportIssue(
                row.row_number, "error",
                f"{row.ern}: unknown Modify verb '{verb}'. "
                f"Known: {', '.join(sorted(MODIFY_VERBS))}."))
            continue

        sub = MODIFY_VERBS[verb]
        if verb == "validate":
            statements.append(f"    {sub}({_quote(args.upper())});")
        elif verb in ("list", "unlist"):
            mm = re.match(r"^([A-Z0-9_]+)\s*(?:->|→)\s*([A-Z0-9_]+)$",
                          args, re.I)
            if not mm:
                issues.append(ImportIssue(
                    row.row_number, "error",
                    f"{row.ern}: {verb}:{args} — expected TEST->LIST."))
                continue
            statements.append(
                f"    {sub}({_quote(mm.group(1).upper())}, "
                f"{_quote(mm.group(2).upper())});")
        elif verb in ("request_add", "request_delete"):
            statements.append(f"    {sub}({_quote(args.upper())});")
        elif verb == "note":
            mm = re.match(r"^([A-Z0-9_]+)\s*=\s*([A-Z0-9_]+)$", args, re.I)
            if not mm:
                issues.append(ImportIssue(
                    row.row_number, "error",
                    f"{row.ern}: note:{args} — expected TEST=COMMENT_CODE."))
                continue
            statements.append(
                f"    add_testnote({_quote(mm.group(1).upper())}, "
                f"codedcomment({_quote(mm.group(2).upper())}));")
        elif verb in ("value", "coded", "calc"):
            mm = re.match(r"^([A-Z0-9_]+)\s*=\s*(.+)$", args, re.I)
            if not mm:
                issues.append(ImportIssue(
                    row.row_number, "error",
                    f"{row.ern}: {verb}:{args} — expected TEST=VALUE."))
                continue
            test = mm.group(1).upper()
            val = mm.group(2).strip()
            if verb == "coded":
                val = _quote(val.upper())
            elif verb == "value":
                # Leave numeric/string as written; quote if it looks textual
                if not re.match(r"^-?\d+(\.\d+)?$", val):
                    val = _quote(val)
            statements.append(f"    {sub}({_quote(test)}, {val});")
        else:
            # Should be unreachable
            statements.append(f"    /* TODO: unhandled verb {verb}:{args} */")

    if not statements:
        return None
    return "\n".join(statements)


# ---------------------------------------------------------------------------
# Equation block synthesis
# ---------------------------------------------------------------------------

def render_row(row: WorkflowRow, issues: List[ImportIssue]) -> Optional[str]:
    """Render one row as a self-contained if/endif block."""
    primary = _emit_condition(row.trigger_test, row.trigger_op or "ordered",
                              row.trigger_value, issues, row.row_number)
    if primary is None:
        return None
    clauses: List[str] = [primary]
    for piece in re.split(r"\s*;\s*", row.extra_condition):
        if not piece.strip():
            continue
        sub = _emit_extra(piece, issues, row.row_number)
        if sub is None:
            return None
        clauses.append(sub)

    action = _emit_action(row, issues)
    if action is None:
        return None

    header_lines = [f"/* ERN: {row.ern}"]
    if row.workflow:
        header_lines.append(f" * Workflow: {row.workflow}")
    if row.department:
        header_lines.append(f" * Department: {row.department}")
    if row.notes:
        header_lines.append(f" * Notes: {row.notes}")
    header_lines.append(" */")
    header = "\n".join(header_lines)

    condition = " and ".join(clauses)
    block = f"{header}\nif {condition} then\n{action}\nendif"
    return block


def group_files(rows: Sequence[WorkflowRow],
                issues: List[ImportIssue]) -> Dict[str, str]:
    """Render all rows, group by destination filename."""
    bucket: Dict[str, List[str]] = {}
    for row in rows:
        block = render_row(row, issues)
        if block is None:
            continue
        if row.category == "REQ_ADD":
            fname = "requests_add.eq"
        elif row.category == "REQ_DELETE":
            fname = "requests_delete.eq"
        else:
            # MODIFY: split by department
            safe = re.sub(r"[^A-Z0-9_]+", "_", row.department.upper()).strip("_") \
                or "UNSORTED"
            fname = f"modify_{safe}.eq"
        bucket.setdefault(fname, []).append(block)

    files: Dict[str, str] = {}
    for fname, blocks in sorted(bucket.items()):
        body = "\n\n".join(blocks)
        header = (
            f"/* {fname}\n"
            f" *\n"
            f" * Generated by rule_lint_xlsx — review before deployment.\n"
            f" * Each block carries an ERN reference back to the source row.\n"
            f" */\n\n"
        )
        files[fname] = header + body + "\n"
    return files


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def import_spreadsheet(path: str) -> ImportResult:
    """Parse path (.xlsx or .csv), generate .eq files, return ImportResult."""
    ext = Path(path).suffix.lower()
    if ext == ".xlsx":
        raw = read_xlsx(path)
    elif ext == ".csv":
        raw = read_csv(path)
    else:
        raise ValueError(f"Unsupported spreadsheet extension: {ext}")

    rows, issues = parse_rows(raw)
    files = group_files(rows, issues)
    skipped = sum(1 for i in issues if i.severity == "error")
    return ImportResult(rows=rows, files=files, issues=issues, skipped=skipped)


def write_files(result: ImportResult, output_dir: str) -> List[str]:
    """Write each generated file under output_dir. Returns paths written."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for fname, content in result.files.items():
        target = out_dir / fname
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(str(target))
    return written


# ---------------------------------------------------------------------------
# CSV template export
# ---------------------------------------------------------------------------

TEMPLATE_HEADER = list(KNOWN_COLUMNS)
TEMPLATE_EXAMPLES = [
    ["B.00521.7.01", "BIOCHM", "AFP", "Req Add", "AFP", "ordered", "", "",
     "AFPROCHE", "Reflex AFP to Roche analyser"],
    ["B.00081.6.02", "BIOCHM", "RP", "Modify", "K", ">=", "6.2", "",
     "list:RP->TELEPHONE; list:RP->BREV",
     "Hyperkalaemia escalation"],
    ["B.00260.6.05", "BIOCHM", "LIPIDS", "Modify", "TRIG", ">", "4.5",
     "value:TCHOL<=5.0",
     "note:TRIG=TRIGSE", "Severe hypertriglyceridaemia note"],
    ["B.00440.6.01", "BIOCHM", "TDM", "Req Delete", "CICLO", "resulted", "", "",
     "CICLO_REFLEX", "Drop reflex once primary result lands"],
]


def write_csv_template(path: str) -> None:
    """Write a starter CSV template to path."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Capitalise headers for readability in Excel
        writer.writerow([c.title() for c in TEMPLATE_HEADER])
        for row in TEMPLATE_EXAMPLES:
            writer.writerow(row)
