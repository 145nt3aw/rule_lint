"""FastAPI backend for Rule Lint web UI.

Wraps the existing rule_lint.py linter as HTTP endpoints. In production the
built React bundle from /frontend/dist is mounted at "/" so this serves both
the API and the UI from a single port.

Local dev: run with `uvicorn backend.app:app --reload --port 8000` and have
Vite proxy /api → :8000 (see frontend/vite.config.ts).
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Make rule_lint.py at the repo root importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.routes import router  # noqa: E402


app = FastAPI(
    title="Rule Lint",
    description="Web UI for the Evolution rule-engine linter.",
    version="1.1.0",
)

# Local Docker deployments are single-origin so CORS is mostly a no-op, but
# allow * for dev convenience (Vite on :5173 hitting backend on :8000).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


# Serve the built React bundle if it exists (production / Docker case).
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    # html=True makes FastAPI serve index.html for unknown sub-paths, which
    # lets the React router handle client-side routes.
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True),
              name="frontend")
