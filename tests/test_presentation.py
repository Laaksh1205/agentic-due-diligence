"""
Tests for the Phase-2 presentation layer — Task 2.9.

Renders to an in-memory rich Console (no TTY) and asserts content; JSON export
writes to a tmp dir. No network/LLM.
"""

import json
from io import StringIO

from rich.console import Console

from src.models.documents import DataSufficiency
from src.models.entities import ResolvedEntity
from src.models.report import DueDiligenceReport, ReportMetadata
from src.models.signals import (
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
    SourceType,
)
from src.presentation.cli_report import render_report, render_from_state, sufficiency_badge
from src.presentation.json_export import export_report, export_signals
from src.presentation.progress import STAGE_LABELS, PipelineProgress


def _console():
    return Console(file=StringIO(), width=120, force_terminal=False, color_system=None)


def _entity(name="Helix Energy"):
    return ResolvedEntity(canonical_name=name, aliases=[name, "HLX"], jurisdiction="us-de", is_public=True)


def _sig(text, *, cat=RiskCategory.FINANCIAL, sev=Severity.CRITICAL,
         pol=SignalPolarity.NEGATIVE, url="https://news.example.com/a", review=False):
    return RiskSignal(
        text=text, source_url=url, source_type=SourceType.NEWS_ARTICLE,
        source_snippet="snippet anchoring the signal text for the record here ok",
        confidence_score=0.9, risk_category=cat, severity=sev, signal_polarity=pol,
        entity_name="Helix Energy", requires_human_review=review,
    )


# ── cli_report ────────────────────────────────────────────────────────────────

def test_render_report_contains_all_sections():
    c = _console()
    signals = [
        _sig("Auditors raised going-concern doubt", sev=Severity.CRITICAL, review=True),
        _sig("ISO 27001 certified", cat=RiskCategory.CYBERSECURITY, sev=Severity.INFO,
             pol=SignalPolarity.POSITIVE),
    ]
    render_report(
        console=c, entity=_entity(), data_sufficiency=DataSufficiency.RICH,
        signals=signals, sources_consulted=["web_search", "registry_lookup"],
        sources_failed=["companies_house"],
        metadata={"elapsed_seconds": 12.3, "llm_call_count": 5, "total_cost": 0.0123,
                  "signals_extracted": 9, "signals_rejected": 1, "signals_final": 2},
    )
    out = c.file.getvalue()
    assert "Helix Energy" in out
    assert "RICH" in out                          # sufficiency badge
    assert "FINANCIAL" in out                     # category grouping
    assert "CRITICAL" in out                      # severity colour-coded label
    assert "going-concern" in out
    assert "Strengths" in out                     # positive section
    assert "ISO 27001" in out
    assert "registry_lookup" in out and "companies_house" in out  # sources
    assert "PENDING_REVIEW" in out                # review flag rendered
    assert "Run metadata" in out and "$0.0123" in out


def test_render_report_no_entity_shows_error():
    c = _console()
    render_report(console=c, entity=None, data_sufficiency=None, signals=[])
    assert "ERROR" in c.file.getvalue()


def test_render_report_no_risks_message():
    c = _console()
    render_report(console=c, entity=_entity(), data_sufficiency=DataSufficiency.SPARSE, signals=[])
    out = c.file.getvalue()
    assert "No risk signals detected" in out
    assert "SPARSE" in out


def test_sufficiency_badge_styles():
    assert "RICH" in sufficiency_badge(DataSufficiency.RICH).plain
    assert sufficiency_badge(None).plain == "UNKNOWN"


def test_render_from_state_uses_scored_signals():
    c = _console()
    state = {
        "resolved_entity": _entity("Acme Corp"),
        "data_sufficiency": DataSufficiency.ADEQUATE,
        "scored_signals": [_sig("revenue fell 40%")],
        "raw_signals": [_sig("x"), _sig("y")],
        "sources_consulted": ["web_search"], "sources_failed": [],
        "llm_call_count": 3, "total_cost": 0.05, "start_time": None,
    }
    render_from_state(state, console=c)
    out = c.file.getvalue()
    assert "Acme Corp" in out and "ADEQUATE" in out
    assert "revenue fell 40%" in out


# ── json_export ───────────────────────────────────────────────────────────────

def test_export_report_writes_valid_json(tmp_path):
    report = DueDiligenceReport(
        target_entity=_entity(), evaluation_scope="full",
        data_sufficiency=DataSufficiency.RICH, metadata=ReportMetadata(),
    )
    path = export_report(report, output_dir=str(tmp_path), timestamp="20260625_120000")
    assert path.exists()
    assert path.name == "helix_energy_20260625_120000.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["target_entity"]["canonical_name"] == "Helix Energy"
    assert data["data_sufficiency"] == "RICH"


def test_export_signals_writes_valid_json(tmp_path):
    path = export_signals([_sig("a"), _sig("b")], entity_name="Acme Corp",
                          output_dir=str(tmp_path), timestamp="20260625_120000")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["signal_count"] == 2
    assert data["entity_name"] == "Acme Corp"
    assert len(data["signals"]) == 2


def test_export_slugifies_entity_name(tmp_path):
    report = DueDiligenceReport(
        target_entity=_entity("Açme, Inc. & Co!"), evaluation_scope="full",
        data_sufficiency=DataSufficiency.SPARSE, metadata=ReportMetadata(),
    )
    path = export_report(report, output_dir=str(tmp_path), timestamp="t")
    assert path.name == "a_me_inc_co_t.json"


# ── progress ──────────────────────────────────────────────────────────────────

def test_progress_disabled_on_non_terminal():
    p = PipelineProgress(console=_console())
    assert p.enabled is False                     # StringIO is not a terminal
    with p:
        p.stage("research")                       # must not raise
        p.stage("custom message")


def test_stage_labels_cover_pipeline():
    for key in ("research", "extraction", "risk_analysis", "synthesis"):
        assert key in STAGE_LABELS
