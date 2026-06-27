# syntax=docker/dockerfile:1
#
# Multi-stage build for the Agentic Due Diligence Platform.
#   Stage 1 (frontend): build the Next.js static export → frontend/out
#   Stage 2 (runtime):  slim Python image that installs the package and serves
#                       both the FastAPI backend AND the built frontend on :8000.
#
# The final image carries no Node toolchain — only the static `out/` directory.
# The package is installed editable so api/main.py resolves frontend/out and
# knowledge_base/ relative to /app (it reads them via __file__).

# ── Stage 1: build the static frontend ────────────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /app/frontend

# Install deps from the lockfile first for better layer caching.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build a fully static export (output: 'export' → frontend/out).
COPY frontend/ ./
ENV NEXT_OUTPUT_EXPORT=1
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Persist the sentence-transformers model download across restarts (volume).
    HF_HOME=/app/.cache/huggingface

# Install the package + its web extra. Copy only what the install needs first so
# the (slow) dependency layer is cached independently of source edits.
COPY pyproject.toml README.md mcp_config.json ./
COPY src ./src
COPY api ./api
COPY knowledge_base ./knowledge_base
# Install the CPU-only torch wheel *first* so sentence-transformers reuses it
# instead of pulling the default ~5 GB CUDA build — there is no GPU on the free
# HF CPU host (or any of our targets), so the CUDA libraries are dead weight.
# This drops the final image from ~8.8 GB to ~2.5 GB with zero runtime cost.
RUN pip install --upgrade pip \
 && pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -e ".[web]"

# Bring in the pre-built static frontend from stage 1.
COPY --from=frontend /app/frontend/out ./frontend/out

# Runtime-writable dirs (SQLite store, exports, model cache).
RUN mkdir -p data output .cache/huggingface

EXPOSE 8000
CMD ["ddp-api", "--host", "0.0.0.0", "--port", "8000"]
