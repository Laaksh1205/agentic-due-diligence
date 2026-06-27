"""Web integration tests — Task 4.3 (backend side).

These complement the browser E2E suite (frontend/e2e) by driving the same user
journey through the real FastAPI app and exercising the error/edge states the
plan calls out in 4.3.3 (source down / pipeline failure, empty results,
report-not-ready, unknown run) — paths that are hard to trigger from the UI but
must degrade gracefully so the frontend can show a clean message.

The pipeline is replaced with deterministic fakes (no network / LLM).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from src.models.documents import DataSufficiency
from src.models.entities import ResolvedEntity
from src.models.report import DueDiligenceReport


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "integ.db"))


@pytest.fixture(autouse=True)
def fresh_manager(monkeypatch):
    from api.runs import RunManager
    import api.main as main_mod
    import api.runs as runs_mod

    mgr = RunManager()
    monkeypatch.setattr(main_mod, "manager", mgr)
    monkeypatch.setattr(runs_mod, "manager", mgr)
    return mgr


def _report() -> DueDiligenceReport:
    return DueDiligenceReport(
        target_entity=ResolvedEntity(canonical_name="ACME CORP"),
        evaluation_scope="full",
        data_sufficiency=DataSufficiency.ADEQUATE,
        executive_summary="Summary.",
    )


def _client():
    from api.main import app
    return TestClient(app)


def _wait(client, run_id, until, tries=60):
    for _ in range(tries):
        body = client.get(f"/api/runs/{run_id}").json()
        if until(body):
            return body
    raise AssertionError(f"condition not met; last={body}")


# ── 4.3.1 — Full journey through the API (assess → status → report → export) ──

def test_full_journey_assess_to_export(monkeypatch):
    import src.agents.supervisor as sup

    async def fake_pipeline(entity_name, *, scope="full", auto_mode=False,
                            hitl_timeout=None, run_id=None, progress_cb=None):
        from src.storage.database import save_report
        for node in ("entity_resolution", "research", "extraction", "risk_analysis",
                     "hitl_gate", "synthesis", "presentation"):
            if progress_cb:
                progress_cb(node)
        rep = _report()
        await save_report(run_id, "ACME CORP", rep.model_dump(mode="json"))
        return {"report": rep, "scored_signals": [], "errors": []}

    monkeypatch.setattr(sup, "run_pipeline", fake_pipeline)

    with _client() as client:
        run_id = client.post("/api/assess", json={"company_name": "Acme", "auto_mode": True}).json()["run_id"]
        body = _wait(client, run_id, lambda b: b["status"] == "complete")
        assert body["progress_pct"] == 100

        assert client.get(f"/api/runs/{run_id}/report").json()["target_entity"]["canonical_name"] == "ACME CORP"
        # JSON export downloads (4.2.11 / 4.1.8)
        r = client.get(f"/api/runs/{run_id}/export/json")
        assert r.status_code == 200 and r.json()["target_entity"]["canonical_name"] == "ACME CORP"


# ── 4.3.3 — Error states degrade gracefully ──────────────────────────────────

def test_pipeline_crash_surfaces_as_error_status(monkeypatch):
    """A source/pipeline failure becomes a clean error status, not a 500."""
    import src.agents.supervisor as sup

    async def boom(entity_name, *, scope="full", auto_mode=False,
                   hitl_timeout=None, run_id=None, progress_cb=None):
        raise RuntimeError("Companies House API unreachable (source down)")

    monkeypatch.setattr(sup, "run_pipeline", boom)

    with _client() as client:
        run_id = client.post("/api/assess", json={"company_name": "Acme", "auto_mode": True}).json()["run_id"]
        body = _wait(client, run_id, lambda b: b["status"] == "error")
        assert "source down" in (body["error"] or "")
        # Report endpoint is a clean 409, never a stack trace.
        assert client.get(f"/api/runs/{run_id}/report").status_code in (409, 404)


def test_empty_results_complete_without_report(monkeypatch):
    """Entity-not-found / empty results: no report, but the run still resolves."""
    import src.agents.supervisor as sup

    async def empty(entity_name, *, scope="full", auto_mode=False,
                    hitl_timeout=None, run_id=None, progress_cb=None):
        if progress_cb:
            progress_cb("entity_resolution")
        return {
            "report": None,
            "scored_signals": [],
            "errors": ["entity_resolution: no match found"],
        }

    monkeypatch.setattr(sup, "run_pipeline", empty)

    with _client() as client:
        run_id = client.post("/api/assess", json={"company_name": "Zzz", "auto_mode": True}).json()["run_id"]
        body = _wait(client, run_id, lambda b: b["status"] in ("error", "complete"))
        assert body["status"] == "error"
        assert "no match" in (body["error"] or "")
        assert client.get(f"/api/runs/{run_id}/signals").json()["total"] == 0


def test_report_not_ready_returns_409(monkeypatch):
    """Requesting the report mid-run yields 409, which the UI shows as 'loading'."""
    import asyncio
    import src.agents.supervisor as sup

    async def slow(entity_name, *, scope="full", auto_mode=False,
                   hitl_timeout=None, run_id=None, progress_cb=None):
        if progress_cb:
            progress_cb("research")
        await asyncio.sleep(0.5)
        return {"report": None, "scored_signals": [], "errors": []}

    monkeypatch.setattr(sup, "run_pipeline", slow)

    with _client() as client:
        run_id = client.post("/api/assess", json={"company_name": "Acme", "auto_mode": True}).json()["run_id"]
        # Immediately ask for the report — still running.
        assert client.get(f"/api/runs/{run_id}/report").status_code == 409


def test_unknown_run_returns_404():
    with _client() as client:
        missing = str(uuid.uuid4())
        assert client.get(f"/api/runs/{missing}").status_code == 404
        assert client.get(f"/api/runs/{missing}/report").status_code == 404
        assert client.get(f"/api/runs/{missing}/signals").status_code == 404


def test_review_on_non_reviewing_run_is_rejected(monkeypatch):
    """Posting a verdict when the run isn't awaiting review is a clean 409."""
    import src.agents.supervisor as sup

    async def fast(entity_name, *, scope="full", auto_mode=False,
                   hitl_timeout=None, run_id=None, progress_cb=None):
        return {"report": _report(), "scored_signals": [], "errors": []}

    monkeypatch.setattr(sup, "run_pipeline", fast)

    with _client() as client:
        run_id = client.post("/api/assess", json={"company_name": "Acme", "auto_mode": True}).json()["run_id"]
        _wait(client, run_id, lambda b: b["status"] == "complete")
        r = client.post(f"/api/runs/{run_id}/review", json={"signal_id": "x", "verdict": "confirm"})
        assert r.status_code == 409
