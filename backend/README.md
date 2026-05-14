# Rule Lint backend

FastAPI service that wraps the existing [`rule_lint.py`](../rule_lint.py)
linter as HTTP endpoints. Designed to run inside the Docker container
alongside the React frontend, but works fine standalone for local dev.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/health`                | Liveness probe — returns `{"status":"ok",...}` |
| GET  | `/api/codes`                 | Full ISSUE_CODES registry as JSON (for the help panel) |
| GET  | `/api/eqtypes`               | Recognised equation-type aliases for the UI dropdown |
| POST | `/api/lint`                  | Lint one `.eq` file (multipart upload) |
| POST | `/api/lint-batch`            | Lint every rule file inside an uploaded `.zip` |
| POST | `/api/fix`                   | Run safe auto-fixes against one `.eq`, return patched text |
| GET  | `/api/import-xlsx/template`  | Download the workflow CSV starter template |
| POST | `/api/import-xlsx`           | Parse a workflow `.xlsx`/`.csv`, return generated `.eq` content inline |
| POST | `/api/import-xlsx/zip`       | Same parse, returns a single `.zip` download |
| POST | `/api/preview`               | Static-walk a `.mask`, return positioned render commands for live preview |

`POST /api/lint` form fields:
- `file` (required) — the rule file
- `eqtype` (optional) — one of the values from `/api/eqtypes`
- `strict` (optional bool) — surface foot-gun notes
- `testlist` (optional file) — a test-catalogue CSV

`POST /api/lint-batch` form fields:
- `archive` (required) — `.zip` containing `.eq` / `.rule` / `.mask` files
- `eqtype`, `strict`, `testlist` — same as `/api/lint`

Response shape — see [`routes.py`](routes.py) (`FileLintResult`,
`BatchLintResult`) and the TypeScript mirror in
[`frontend/src/types.ts`](../frontend/src/types.ts).

## Local development

```bash
# From the repo root
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# Backend on :8000
uvicorn backend.app:app --reload --port 8000

# Frontend on :5173 (proxies /api → :8000)
cd frontend && npm install && npm run dev
```

The Vite dev server (port 5173) hot-reloads UI changes while uvicorn's
`--reload` picks up Python changes.

`POST /api/preview` body (JSON):
- `text` (required) — the `.mask` source
- `grid_width`, `grid_height` (optional) — default 120×25, clamped to [20–200]×[5–100]

Response shape — see [`routes.py`](routes.py) (`PreviewResultOut`,
`RenderCmdOut`) and the TypeScript mirror in
[`frontend/src/types.ts`](../frontend/src/types.ts). Each command is a
`{x, y, kind, text, …, source_line}` record. The renderer walks all
`if` branches (commands may overlap — last-writer-wins on the grid).

## Limits

- Single uploads capped at 5 MiB
- Batch zip capped at 20 MiB and 500 files
- Files with unrecognised extensions inside the zip are silently skipped;
  only `.eq` / `.rule` / `.mask` are linted

Raise the caps in `routes.py` (`_MAX_FILE_BYTES`, `_MAX_ZIP_FILES`) if the
local Docker deployment needs to handle bigger workloads.

## Notes

- `include_mask()` resolution is **not** supported by the web endpoints —
  the resolver walks the filesystem. The CLI and Tk GUI still support it.
  Web-side this would mean re-uploading every mask alongside the parent
  rule, or accepting a zip and resolving inside. v1 punts on it.
- The backend imports `rule_lint.py` directly from the repo root via
  `sys.path` manipulation in `app.py`. Keeping the linter core in one
  place avoids divergence between CLI / Tk GUI / web.
