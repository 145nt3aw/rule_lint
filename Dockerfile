# Multi-stage build: Node builds the React bundle, Python serves bundle + API.
#
# Build:    docker build -t rule-lint .
# Run:      docker run --rm -p 8000:8000 rule-lint
# Compose:  docker compose up

# ---------------------------------------------------------------------- 1/2
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend

# Install deps first for layer caching
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

# Then copy source + build
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------- 2/2
FROM python:3.11-slim AS runtime
WORKDIR /app

# Install backend deps (cached separately from app code)
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Linter core (root-level Python files)
COPY rule_lint.py rule_catalogue.py rule_lint_xlsx.py ./

# Backend package
COPY backend/ ./backend/

# Built React bundle from stage 1
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000

# Reload disabled in production; rely on docker compose for restart policy.
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
