"""FastAPI application — Task 4.1.

Endpoints (all under /api):
  POST   /api/assess                       launch an assessment (4.1.2)
  GET    /api/runs/{run_id}                live status for polling (4.1.3)
  GET    /api/runs/{run_id}/report         full DueDiligenceReport JSON (4.1.4)
  GET    /api/runs/{run_id}/signals        paginated/filterable signals (4.1.5)
  POST   /api/runs/{run_id}/review         submit a HITL verdict (4.1.6)
  GET    /api/runs/{run_id}/export/pdf     download PDF report (4.1.7)
  GET    /api/runs/{run_id}/export/json    download JSON report (4.1.8)
  WS     /ws/{run_id}                      live progress stream (4.1.9)

Run with: uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# The pipeline's presentation node renders a Rich report (incl. glyphs like ⚠) to
# stdout. Windows consoles default to cp1252, which can't encode those — the CLI
# fixes this in src/main.py, so do the same for the API server process here.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - best effort; never block startup
            pass

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.runs import manager
from src.storage.database import get_report as db_get_report
from src.storage.database import init_db, list_reports

logger = logging.getLogger(__name__)

# Built Next.js export lands here (Phase 4.2). Mounted only if present.
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "out"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("API ready — database initialised")
    yield


app = FastAPI(
    title="Due Diligence Intelligence Platform API",
    version="0.1.0",
    lifespan=lifespan,
)

# 4.1.1 — CORS for the Next.js dev server (and any origin in this MVP).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / response models ─────────────────────────────────────────────────

class AssessRequest(BaseModel):
    company_name: str = Field(..., min_length=1)
    scope: str = Field("full", pattern="^(full|financial|compliance)$")
    auto_mode: bool = False
    hitl_timeout: Optional[int] = Field(None, ge=1)
    # Set when the user picked a specific candidate in the entity picker (§8c).
    registry_id: str = ""
    jurisdiction: str = ""


class AssessResponse(BaseModel):
    run_id: str


class ResolveRequest(BaseModel):
    company_name: str = Field(..., min_length=1)
    jurisdiction: str = ""


class Candidate(BaseModel):
    registry_id: str = ""
    name: str
    jurisdiction: str = ""
    status: str = ""
    company_type: str = ""
    address: str = ""
    is_public: bool = False


class ResolveResponse(BaseModel):
    candidates: list[Candidate]


class ReviewRequest(BaseModel):
    signal_id: str
    verdict: str = Field(..., pattern="^(confirm|dismiss|investigate)$")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_run(run_id: str):
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


def _report_obj(report_json: dict):
    """Rebuild a DueDiligenceReport from stored JSON for export rendering."""
    from src.models.report import DueDiligenceReport

    return DueDiligenceReport.model_validate(report_json)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/resolve", response_model=ResolveResponse)
async def resolve_entity(req: ResolveRequest) -> ResolveResponse:
    """Return up to 5 registry candidates for the entity picker (design §8c).

    No pipeline run — just a registry search. Returns an empty list when
    Registry Lookup is unavailable or finds nothing, so the UI can fall back to
    submitting the raw name (autonomous best-match resolution).
    """
    from src.resolution.entity_resolver import EntityResolver

    out: list[Candidate] = []
    try:
        cands = await EntityResolver().search_candidates(req.company_name, req.jurisdiction)
        for c in cands:
            try:
                out.append(Candidate(**c))
            except Exception as exc:  # skip a malformed candidate, don't 500
                logger.warning("skipping candidate for '%s': %s", req.company_name, exc)
    except Exception as exc:  # never let resolution errors break the UI
        logger.warning("resolve failed for '%s': %s", req.company_name, exc)
    return ResolveResponse(candidates=out)


@app.post("/api/assess", response_model=AssessResponse, status_code=202)
async def assess(req: AssessRequest) -> AssessResponse:
    """Kick off the pipeline in a background task and return its run_id (4.1.2)."""
    run = manager.start(
        req.company_name,
        scope=req.scope,
        auto_mode=req.auto_mode,
        hitl_timeout=req.hitl_timeout,
        registry_id=req.registry_id,
        jurisdiction=req.jurisdiction,
    )
    return AssessResponse(run_id=run.run_id)


@app.get("/api/runs")
async def runs_history() -> dict:
    """Run history — persisted reports plus any in-flight in-memory runs (4.2.12)."""
    persisted = await list_reports()
    history = [
        {
            "run_id": r["id"],
            "entity_name": r["entity_name"],
            "created_at": r["created_at"],
            "status": "complete",
        }
        for r in persisted
    ]
    known_ids = {h["run_id"] for h in history}
    for run in manager._runs.values():  # surface live runs not yet persisted
        if run.run_id not in known_ids:
            history.append(
                {
                    "run_id": run.run_id,
                    "entity_name": run.company_name,
                    "created_at": None,
                    "status": run.status,
                }
            )
    return {"runs": history}


@app.get("/api/runs/{run_id}")
async def run_status(run_id: str) -> dict:
    """Current pipeline status for polling (4.1.3)."""
    return _require_run(run_id).status_payload()


@app.get("/api/runs/{run_id}/report")
async def run_report(run_id: str) -> JSONResponse:
    """Full DueDiligenceReport JSON — only once complete (4.1.4)."""
    run = manager.get(run_id)
    if run is not None and run.report is not None:
        return JSONResponse(run.report)
    # Fall back to the persisted copy (e.g. after a server restart).
    stored = await db_get_report(run_id)
    if stored is not None:
        return JSONResponse(stored)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    raise HTTPException(status_code=409, detail=f"report not ready (status={run.status})")


@app.get("/api/runs/{run_id}/signals")
async def run_signals(
    run_id: str,
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Paginated, filterable risk signals (4.1.5)."""
    run = _require_run(run_id)
    signals = run.signals
    if category:
        signals = [s for s in signals if s.get("risk_category") == category.upper()]
    if severity:
        signals = [s for s in signals if s.get("severity") == severity.upper()]
    total = len(signals)
    page = signals[offset : offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "signals": page}


@app.post("/api/runs/{run_id}/review")
async def run_review(run_id: str, req: ReviewRequest) -> dict:
    """Apply one HITL verdict and resume the gate when all are in (4.1.6)."""
    run = _require_run(run_id)
    try:
        manager.submit_verdict(run, req.signal_id, req.verdict)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "status": run.status}


@app.get("/api/runs/{run_id}/export/pdf")
async def export_pdf_endpoint(run_id: str) -> FileResponse:
    """Render and download the report as a PDF (4.1.7)."""
    run = manager.get(run_id)
    report_json = run.report if (run and run.report) else await db_get_report(run_id)
    if report_json is None:
        raise HTTPException(status_code=409, detail="report not ready")

    from src.presentation.pdf_export import export_pdf

    try:
        path = export_pdf(_report_obj(report_json))
    except RuntimeError as exc:  # no PDF backend installed
        raise HTTPException(status_code=503, detail=str(exc))
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/api/runs/{run_id}/export/json")
async def export_json_endpoint(run_id: str) -> FileResponse:
    """Write the report to disk and download it as JSON (4.1.8)."""
    run = manager.get(run_id)
    report_json = run.report if (run and run.report) else await db_get_report(run_id)
    if report_json is None:
        raise HTTPException(status_code=409, detail="report not ready")

    from src.presentation.json_export import export_report

    path = export_report(_report_obj(report_json))
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.websocket("/ws/{run_id}")
async def run_ws(websocket: WebSocket, run_id: str) -> None:
    """Push live status updates instead of polling (4.1.9)."""
    await websocket.accept()
    run = manager.get(run_id)
    if run is None:
        await websocket.send_json({"error": f"run {run_id} not found"})
        await websocket.close()
        return

    queue = manager.subscribe(run)
    try:
        # Send the current snapshot immediately so late subscribers catch up.
        await websocket.send_json(run.status_payload())
        if run.status in ("complete", "error"):
            return
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
            if payload.get("status") in ("complete", "error"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(run, queue)


# ── Static frontend (mounted last so it never shadows /api routes) ────────────

# The dynamic run page is statically exported once as a placeholder shell
# (frontend/out/runs/live/index.html); the client reads the real run id from the
# URL. StaticFiles has no SPA fallback, so serve that shell for any /runs/<id>.
_RUN_SHELL = _FRONTEND_DIR / "runs" / "live" / "index.html"


@app.get("/runs/{run_id}", include_in_schema=False)
async def _run_page_shell(run_id: str):
    if _RUN_SHELL.is_file():
        return FileResponse(str(_RUN_SHELL), media_type="text/html")
    return JSONResponse({"detail": "frontend not built"}, status_code=404)


if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
    logger.info("Mounted static frontend from %s", _FRONTEND_DIR)
