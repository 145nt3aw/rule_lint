"""HTTP endpoints for the Rule Lint web UI."""

from __future__ import annotations

import io
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

import rule_lint
import rule_lint_xlsx
from rule_lint import (
    EQ_TYPE_ALIASES, ISSUE_CODES, apply_fixes, apply_suppressions,
    build_suppress_map, lint, load_testlist,
)

from backend.preview import render_mask


router = APIRouter()


# ---------------------------------------------------------- response shapes


class IssueOut(BaseModel):
    severity: str
    line: int
    column: int
    code: str
    message: str
    include_chain: List[str] = []


class FileLintResult(BaseModel):
    filename: str
    lines: int
    errors: int
    warnings: int
    info: int
    issues: List[IssueOut]


class BatchLintResult(BaseModel):
    files: List[FileLintResult]
    total_errors: int
    total_warnings: int
    total_info: int


class CodeEntry(BaseModel):
    code: str
    severity: str
    description: str


class FixEntry(BaseModel):
    line: int
    description: str


class FixResult(BaseModel):
    filename: str
    fixed: bool                # True if at least one fix was applied
    fixes: List[FixEntry]
    original_size: int
    fixed_size: int
    fixed_text: str            # the patched .eq, ready for download
    # Re-linted issues against the fixed text so the UI can show "what
    # remains after auto-fix" without a second round-trip.
    remaining_errors: int
    remaining_warnings: int
    remaining_info: int


class GeneratedFile(BaseModel):
    filename: str
    content: str
    lines: int


class ImportIssueOut(BaseModel):
    row_number: int
    severity: str
    message: str


class ImportResult(BaseModel):
    rows_parsed: int
    files: List[GeneratedFile]
    issues: List[ImportIssueOut]
    total_errors: int
    total_warnings: int


class RenderCmdOut(BaseModel):
    x: int
    y: int
    kind: str
    text: str = ""
    width: int = 0
    height: int = 0
    colour: Optional[str] = None
    bold: bool = False
    source_line: int = 0


class PreviewWarningOut(BaseModel):
    line: int
    message: str


class PreviewResultOut(BaseModel):
    grid_width: int
    grid_height: int
    branches_expanded: int
    commands: List[RenderCmdOut]
    warnings: List[PreviewWarningOut]


class PreviewRequest(BaseModel):
    text: str
    grid_width: int = 120
    grid_height: int = 25
    # Optional name → body map for include_mask() resolution. Names match
    # case-insensitively against the include_mask string literal.
    includes: Optional[Dict[str, str]] = None
    # Optional fixture: test mnemonics that count as 'ordered'.
    # When supplied (even if empty), test_ordered(X) conditions are
    # resolved against the set and only the matching if-branch runs.
    # When None, both branches are walked (superset preview).
    ordered_tests: Optional[List[str]] = None


# ---------------------------------------------------------- helpers


_MAX_FILE_BYTES = 5 * 1024 * 1024     # 5 MiB per file — generous
_MAX_ZIP_FILES = 500                  # don't process a zip-bomb
_RULE_EXTENSIONS = (".eq", ".rule", ".mask")


def _decode(raw: bytes) -> str:
    """Decode an uploaded file, falling back to latin-1 for stubborn legacy."""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _normalise_eqtype(value: Optional[str]) -> Optional[str]:
    if not value or value.strip().lower() in ("", "none", "(none)"):
        return None
    return value.strip()


def _lint_text(filename: str, text: str, eqtype: Optional[str],
               strict: bool, testlist: Optional[set]) -> FileLintResult:
    suppress = build_suppress_map(text)
    issues = lint(text, eqtype=eqtype, strict=strict, testlist=testlist)
    issues = apply_suppressions(issues, suppress)

    out_issues = [IssueOut(**asdict(i)) for i in issues]
    return FileLintResult(
        filename=filename,
        lines=text.count("\n") + (0 if text.endswith("\n") else 1),
        errors=sum(1 for i in issues if i.severity == "error"),
        warnings=sum(1 for i in issues if i.severity == "warning"),
        info=sum(1 for i in issues if i.severity == "info"),
        issues=out_issues,
    )


async def _read_upload(upload: UploadFile, *, max_bytes: int) -> bytes:
    """Read an uploaded file with a size cap."""
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename}: exceeds {max_bytes // 1024} KiB cap")
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------- endpoints


@router.get("/codes", response_model=List[CodeEntry])
def list_codes() -> List[CodeEntry]:
    """Return the full ISSUE_CODES registry for the help dialog."""
    out: List[CodeEntry] = []
    for code in sorted(ISSUE_CODES):
        sev, desc = ISSUE_CODES[code]
        out.append(CodeEntry(code=code, severity=sev, description=desc))
    return out


@router.get("/eqtypes")
def list_eqtypes() -> Dict[str, List[str]]:
    """Return the recognised equation-type aliases for the dropdown."""
    return {"eqtypes": sorted(EQ_TYPE_ALIASES.keys())}


@router.get("/health")
def health() -> Dict[str, str]:
    return {
        "status": "ok",
        "version": rule_lint.__doc__.splitlines()[0] if rule_lint.__doc__ else "rule_lint",
        "codes": str(len(ISSUE_CODES)),
    }


@router.post("/lint", response_model=FileLintResult)
async def lint_single(
    file: UploadFile = File(...),
    eqtype: Optional[str] = Form(None),
    strict: bool = Form(False),
    testlist: Optional[UploadFile] = File(None),
) -> FileLintResult:
    """Lint a single .eq file."""
    raw = await _read_upload(file, max_bytes=_MAX_FILE_BYTES)
    text = _decode(raw)

    tl_set: Optional[set] = None
    if testlist is not None:
        tl_raw = await _read_upload(testlist, max_bytes=_MAX_FILE_BYTES)
        # load_testlist expects a path; write to a temp buffer-backed file.
        import tempfile
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as tf:
            tf.write(tl_raw)
            tl_path = tf.name
        try:
            tl_set, _types = load_testlist(tl_path)
        finally:
            try:
                Path(tl_path).unlink()
            except OSError:
                pass

    return _lint_text(
        filename=file.filename or "input.eq",
        text=text,
        eqtype=_normalise_eqtype(eqtype),
        strict=strict,
        testlist=tl_set,
    )


@router.post("/preview", response_model=PreviewResultOut)
def preview(req: PreviewRequest) -> PreviewResultOut:
    """Static-walk a .mask source and return positioned render commands.

    Lightweight, called on every keystroke (debounced by the frontend).
    All branches of every `if` are walked, so the preview is a superset.
    """
    if len(req.text) > _MAX_FILE_BYTES:
        raise HTTPException(413, "Mask source exceeds 5 MiB cap.")
    gw = max(20, min(req.grid_width, 200))
    gh = max(5, min(req.grid_height, 100))
    try:
        result = render_mask(req.text, grid_width=gw, grid_height=gh,
                             includes=req.includes or {},
                             ordered_tests=req.ordered_tests)
    except Exception as exc:   # noqa: BLE001
        # Fall back to a 400 with the parser-level error rather than 500.
        raise HTTPException(400, f"Mask parse failed: {exc}")
    return PreviewResultOut(
        grid_width=result.grid_width,
        grid_height=result.grid_height,
        branches_expanded=result.branches_expanded,
        commands=[
            RenderCmdOut(x=c.x, y=c.y, kind=c.kind, text=c.text,
                         width=c.width, height=c.height, colour=c.colour,
                         bold=c.bold, source_line=c.source_line)
            for c in result.commands
        ],
        warnings=[
            PreviewWarningOut(line=w.line, message=w.message)
            for w in result.warnings
        ],
    )


@router.get("/import-xlsx/template")
def get_xlsx_template() -> Response:
    """Return a CSV template with the workflow-importer column headers
    plus a few example rows. Browser-friendly download.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                    newline="") as tf:
        tmp_path = tf.name
    try:
        rule_lint_xlsx.write_csv_template(tmp_path)
        content = Path(tmp_path).read_bytes()
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition":
                'attachment; filename="workflow_template.csv"',
        },
    )


@router.post("/import-xlsx", response_model=ImportResult)
async def import_xlsx(
    file: UploadFile = File(...),
) -> ImportResult:
    """Parse a workflow spreadsheet (.xlsx or .csv) and return the generated
    .eq files plus any row-level issues.

    Files are returned inline as JSON; the UI offers per-file download via
    Blob. For "download everything as one zip", use /api/import-xlsx/zip.
    """
    name = (file.filename or "").lower()
    if not (name.endswith(".xlsx") or name.endswith(".csv")):
        raise HTTPException(400, "Expected an .xlsx or .csv upload.")

    raw = await _read_upload(file, max_bytes=_MAX_FILE_BYTES)

    # rule_lint_xlsx reads from a path; round-trip through a temp file.
    suffix = ".xlsx" if name.endswith(".xlsx") else ".csv"
    with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as tf:
        tf.write(raw)
        tmp_path = tf.name
    try:
        try:
            result = rule_lint_xlsx.import_spreadsheet(tmp_path)
        except (ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise HTTPException(400, f"Could not parse spreadsheet: {exc}")
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    return ImportResult(
        rows_parsed=len(result.rows),
        files=[
            GeneratedFile(filename=fname, content=content,
                          lines=content.count("\n"))
            for fname, content in sorted(result.files.items())
        ],
        issues=[
            ImportIssueOut(row_number=i.row_number, severity=i.severity,
                           message=i.message)
            for i in result.issues
        ],
        total_errors=len(result.errors),
        total_warnings=len(result.warnings),
    )


@router.post("/import-xlsx/zip")
async def import_xlsx_zip(file: UploadFile = File(...)) -> Response:
    """Same as /import-xlsx but returns the generated files as one .zip
    download instead of inline JSON. Convenience endpoint for the
    'Download all' button.
    """
    name = (file.filename or "").lower()
    if not (name.endswith(".xlsx") or name.endswith(".csv")):
        raise HTTPException(400, "Expected an .xlsx or .csv upload.")

    raw = await _read_upload(file, max_bytes=_MAX_FILE_BYTES)
    suffix = ".xlsx" if name.endswith(".xlsx") else ".csv"
    with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as tf:
        tf.write(raw)
        tmp_path = tf.name
    try:
        try:
            result = rule_lint_xlsx.import_spreadsheet(tmp_path)
        except (ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise HTTPException(400, f"Could not parse spreadsheet: {exc}")
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    if not result.files:
        raise HTTPException(
            400, "No .eq files generated — the spreadsheet produced no usable rows.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in sorted(result.files.items()):
            zf.writestr(fname, content)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition":
                'attachment; filename="generated_rules.zip"',
        },
    )


@router.post("/fix", response_model=FixResult)
async def fix_single(
    file: UploadFile = File(...),
) -> FixResult:
    """Run all safe auto-fixes against a single .eq file.

    Returns the patched text plus a list of fixes applied; the frontend
    triggers a Blob download. Re-lints the fixed text so the UI can show
    "issues remaining" in a single call.
    """
    raw = await _read_upload(file, max_bytes=_MAX_FILE_BYTES)
    text = _decode(raw)
    fixed_text, fixes = apply_fixes(text)
    remaining = lint(fixed_text)
    return FixResult(
        filename=file.filename or "input.eq",
        fixed=bool(fixes),
        fixes=[FixEntry(line=ln, description=desc) for ln, desc in fixes],
        original_size=len(text),
        fixed_size=len(fixed_text),
        fixed_text=fixed_text,
        remaining_errors=sum(1 for i in remaining if i.severity == "error"),
        remaining_warnings=sum(1 for i in remaining if i.severity == "warning"),
        remaining_info=sum(1 for i in remaining if i.severity == "info"),
    )


@router.post("/lint-batch", response_model=BatchLintResult)
async def lint_batch(
    archive: UploadFile = File(...),
    eqtype: Optional[str] = Form(None),
    strict: bool = Form(False),
    testlist: Optional[UploadFile] = File(None),
) -> BatchLintResult:
    """Lint every .eq / .rule / .mask file inside an uploaded .zip."""
    if not (archive.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Expected a .zip upload.")

    raw = await _read_upload(archive, max_bytes=_MAX_FILE_BYTES * 4)

    tl_set: Optional[set] = None
    if testlist is not None:
        tl_raw = await _read_upload(testlist, max_bytes=_MAX_FILE_BYTES)
        import tempfile
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as tf:
            tf.write(tl_raw)
            tl_path = tf.name
        try:
            tl_set, _types = load_testlist(tl_path)
        finally:
            try:
                Path(tl_path).unlink()
            except OSError:
                pass

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Upload is not a valid zip archive.")

    members = [m for m in zf.namelist()
               if not m.endswith("/")
               and PurePosixPath(m).suffix.lower() in _RULE_EXTENSIONS]
    if not members:
        raise HTTPException(400, "Zip contains no .eq / .rule / .mask files.")
    if len(members) > _MAX_ZIP_FILES:
        raise HTTPException(
            413, f"Zip contains {len(members)} rule files; cap is {_MAX_ZIP_FILES}.")

    results: List[FileLintResult] = []
    eq_param = _normalise_eqtype(eqtype)
    for name in sorted(members):
        with zf.open(name) as f:
            data = f.read()
        text = _decode(data)
        results.append(_lint_text(
            filename=name,
            text=text,
            eqtype=eq_param,
            strict=strict,
            testlist=tl_set,
        ))

    return BatchLintResult(
        files=results,
        total_errors=sum(r.errors for r in results),
        total_warnings=sum(r.warnings for r in results),
        total_info=sum(r.info for r in results),
    )
