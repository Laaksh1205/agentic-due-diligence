"""Tests for the FastAPI backend — Task 4.1.

The pipeline itself is replaced with a fast fake that drives ``progress_cb`` and
the browser HITL provider exactly as the real ``run_pipeline`` does, so the API
contract (status polling, report/signals, review gate, exports, WebSocket) is
exercised end-to-end without any network or LLM calls.
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from src.models.documents import DataSufficiency
from src.models.entities import ResolvedEntity
from src.models.report import DueDiligenceReport
from src.models.signals import (
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
    SourceType,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "api_test.db"))


@pytest.fixture(autouse=True)
def fresh_manager(monkeypatch):
    """Isolate the in-memory run registry per test."""
    from api.runs import RunManager
    import api.main as main_mod
    import api.runs as runs_mod

    mgr = RunManager()
    monkeypatch.setattr(main_mod, "manager", mgr)
    monkeypatch.setattr(runs_mod, "manager", mgr)
    return mgr


def _signal(text="Lawsuit filed", critical=False, polarity=SignalPolarity.NEGATIVE) -> RiskSignal:
    return RiskSignal(
        text=text,
        source_url="https://example.com/a",
        source_type=SourceType.NEWS_ARTICLE,
        source_snippet="verbatim snippet from the source document",
        confidence_score=0.9,
        risk_category=RiskCategory.LEGAL,
        severity=Severity.CRITICAL if critical else Severity.MEDIUM,
        signal_polarity=polarity,
        entity_name="Acme Corp",
        requires_human_review=critical,
    )


def _report(signals, positives=None) -> DueDiligenceReport:
    return DueDiligenceReport(
        target_entity=ResolvedEntity(canonical_name="ACME CORP", aliases=["Acme"]),
        evaluation_scope="full",
        data_sufficiency=DataSufficiency.ADEQUATE,
        risk_signals=signals,
        positive_signals=positives or [],
        executive_summary="Summary.",
    )


def _make_fake_pipeline(*, report, review_signals=None):
    """Build a fake run_pipeline that mimics the real one's callbacks."""

    async def fake_run_pipeline(
        entity_name, *, scope="full", auto_mode=False,
        hitl_timeout=None, run_id=None, progress_cb=None, **kwargs,
    ):
        from src.agents import hitl
        from src.storage.database import save_report

        for node in ("entity_resolution", "research", "data_sufficiency_check",
                     "extraction", "risk_analysis"):
            if progress_cb:
                progress_cb(node)

        scored = list(report.risk_signals)
        if not auto_mode and review_signals:
            provider = hitl._web_verdict_providers.get(run_id)
            assert provider is not None, "web verdict provider should be registered"
            payload = hitl.build_review_payload(review_signals)
            verdicts = await provider(payload)
            scored = hitl.apply_verdicts(scored, verdicts)

        for node in ("hitl_gate", "synthesis", "presentation"):
            if progress_cb:
                progress_cb(node)

        await save_report(run_id, report.target_entity.canonical_name,
                          report.model_dump(mode="json"))
        return {"report": report, "scored_signals": scored, "errors": []}

    return fake_run_pipeline


@pytest.fixture
def client(monkeypatch):
    import src.agents.supervisor as sup
    rep = _report([_signal("Lawsuit"), _signal("Fine", polarity=SignalPolarity.NEGATIVE)],
                  positives=[_signal("Award", polarity=SignalPolarity.POSITIVE)])
    monkeypatch.setattr(sup, "run_pipeline", _make_fake_pipeline(report=rep))
    from api.main import app
    with TestClient(app) as c:
        yield c


def _wait_complete(client, run_id, tries=50):
    for _ in range(tries):
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in ("complete", "error"):
            return body
    raise AssertionError("run did not complete")


# ── Basic endpoints ──────────────────────────────────────────────────────────

def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_assess_returns_run_id(client):
    r = client.post("/api/assess", json={"company_name": "Acme Corp", "auto_mode": True})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert uuid.UUID(run_id)


def test_assess_rejects_bad_scope(client):
    r = client.post("/api/assess", json={"company_name": "Acme", "scope": "nonsense"})
    assert r.status_code == 422


def test_status_report_and_signals_flow(client):
    run_id = client.post(
        "/api/assess", json={"company_name": "Acme Corp", "auto_mode": True}
    ).json()["run_id"]

    status = _wait_complete(client, run_id)
    assert status["status"] == "complete"
    assert status["progress_pct"] == 100

    report = client.get(f"/api/runs/{run_id}/report").json()
    assert report["target_entity"]["canonical_name"] == "ACME CORP"

    sig = client.get(f"/api/runs/{run_id}/signals").json()
    assert sig["total"] == 3  # 2 risk + 1 positive
    # Filter by category
    legal = client.get(f"/api/runs/{run_id}/signals?category=LEGAL").json()
    assert legal["total"] == 3 and all(s["risk_category"] == "LEGAL" for s in legal["signals"])
    # Pagination
    page = client.get(f"/api/runs/{run_id}/signals?limit=1&offset=0").json()
    assert len(page["signals"]) == 1 and page["total"] == 3


def test_report_404_for_unknown_run(client):
    assert client.get(f"/api/runs/{uuid.uuid4()}/report").status_code == 404


def test_json_export(client):
    run_id = client.post(
        "/api/assess", json={"company_name": "Acme Corp", "auto_mode": True}
    ).json()["run_id"]
    _wait_complete(client, run_id)
    r = client.get(f"/api/runs/{run_id}/export/json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["target_entity"]["canonical_name"] == "ACME CORP"


# ── HITL review gate (4.1.6) ─────────────────────────────────────────────────

def test_review_gate_resumes_pipeline(monkeypatch):
    import src.agents.supervisor as sup
    critical = _signal("Sanctions hit", critical=True)
    rep = _report([critical])
    monkeypatch.setattr(
        sup, "run_pipeline",
        _make_fake_pipeline(report=rep, review_signals=[critical]),
    )

    from api.main import app
    with TestClient(app) as client:
        run_id = client.post(
            "/api/assess", json={"company_name": "Acme Corp", "auto_mode": False}
        ).json()["run_id"]

        # Wait until the gate pauses for review.
        for _ in range(50):
            body = client.get(f"/api/runs/{run_id}").json()
            if body["status"] == "reviewing":
                break
        assert body["status"] == "reviewing"
        assert len(body["review_signals"]) == 1
        signal_id = body["review_signals"][0]["id"]

        # Submit a verdict → pipeline resumes and completes.
        r = client.post(
            f"/api/runs/{run_id}/review",
            json={"signal_id": signal_id, "verdict": "confirm"},
        )
        assert r.status_code == 200

        final = _wait_complete(client, run_id)
        assert final["status"] == "complete"


def test_review_rejects_unknown_signal(monkeypatch):
    import src.agents.supervisor as sup
    critical = _signal("Sanctions hit", critical=True)
    monkeypatch.setattr(
        sup, "run_pipeline",
        _make_fake_pipeline(report=_report([critical]), review_signals=[critical]),
    )
    from api.main import app
    with TestClient(app) as client:
        run_id = client.post(
            "/api/assess", json={"company_name": "Acme Corp", "auto_mode": False}
        ).json()["run_id"]
        for _ in range(50):
            if client.get(f"/api/runs/{run_id}").json()["status"] == "reviewing":
                break
        r = client.post(
            f"/api/runs/{run_id}/review",
            json={"signal_id": str(uuid.uuid4()), "verdict": "confirm"},
        )
        assert r.status_code == 404


# ── WebSocket (4.1.9) ─────────────────────────────────────────────────────────

def test_websocket_streams_to_completion(client):
    run_id = client.post(
        "/api/assess", json={"company_name": "Acme Corp", "auto_mode": True}
    ).json()["run_id"]

    statuses = []
    with client.websocket_connect(f"/ws/{run_id}") as ws:
        for _ in range(30):
            msg = ws.receive_json()
            statuses.append(msg["status"])
            if msg["status"] in ("complete", "error"):
                break
    assert statuses[-1] == "complete"
