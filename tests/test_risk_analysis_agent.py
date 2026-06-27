"""
Tests for the Risk Analysis Agent — Task 2.7.

All LLM calls and embeddings are faked, so these are deterministic and need no
network/model (immune to the Gemini/GCP incident). The knowledge-base RAG call
is monkeypatched to return canned context.
"""

import numpy as np
import pytest

import src.agents.risk_analysis_agent as ra
from src.agents.risk_analysis_agent import (
    apply_corroboration_boost,
    candidate_contradiction_pairs,
    critical_inflation,
    effective_severity_score,
    needs_human_review,
    risk_analysis_agent_node,
)
from src.llm.base import LLMCall, LLMProvider
from src.llm.prompts import ContradictionAssessment, SeverityAssessment
from src.models.signals import (
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
)
from src.models.signals import SourceType


def _sig(text, *, cat=RiskCategory.FINANCIAL, sev=Severity.MEDIUM, conf=0.9,
         pol=SignalPolarity.NEGATIVE, corro=False, url="https://x/a", entity="Acme Corp",
         tw=1.0):
    return RiskSignal(
        text=text, source_url=url, source_type=SourceType.NEWS_ARTICLE,
        source_snippet="snippet anchoring the signal text for the record here please",
        confidence_score=conf, risk_category=cat, severity=sev, signal_polarity=pol,
        entity_name=entity, is_corroborated=corro, temporal_weight=tw,
    )


class _FakeProvider(LLMProvider):
    """Routes by requested schema: SeverityAssessment vs ContradictionAssessment."""

    def __init__(self, *, severity=Severity.HIGH, contradiction=False, cost=0.0002):
        super().__init__()
        self._severity = severity
        self._contradiction = contradiction
        self._cost = cost

    async def complete(self, prompt, schema, *, system="", use_fast=True):
        self._record(LLMCall("fake", 80, 40, self._cost))
        if schema is ContradictionAssessment:
            return ContradictionAssessment(is_contradictory=self._contradiction, reason="x")
        return SeverityAssessment(severity=self._severity, reasoning="r", rubric_reference="ref")


class _BagEmbedder:
    _VOCAB = ["revenue", "grew", "fell", "breach", "award", "lawsuit", "fine", "supplier"]

    def __call__(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            v = np.array([low.count(w) for w in self._VOCAB], dtype="float32")
            if v.sum() == 0:
                v[0] = 1e-3
            out.append(v)
        return np.array(out, dtype="float32")


@pytest.fixture(autouse=True)
def _stub_rag(monkeypatch):
    async def _fake_ctx(signal, k=3, **kw):
        return ["CRITICAL: active fraud investigation.", "HIGH: >30% revenue decline."]
    monkeypatch.setattr(ra, "retrieve_severity_context", _fake_ctx)


def _state(signals, *, llm_call_count=0, total_cost=0.0):
    return {"raw_signals": signals, "llm_call_count": llm_call_count,
            "total_cost": total_cost, "errors": [], "run_id": "r"}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_corroboration_boost_caps_at_one():
    assert apply_corroboration_boost(_sig("x", conf=0.95, corro=True)).confidence_score == 1.0
    assert apply_corroboration_boost(_sig("x", conf=0.5, corro=True)).confidence_score == pytest.approx(0.6)
    assert apply_corroboration_boost(_sig("x", conf=0.5, corro=False)).confidence_score == 0.5


def test_needs_human_review_rules():
    assert needs_human_review(_sig("x", sev=Severity.CRITICAL, conf=0.9)) is True
    assert needs_human_review(_sig("x", sev=Severity.LOW, conf=0.5)) is True   # low confidence
    assert needs_human_review(_sig("x", sev=Severity.HIGH, conf=0.9)) is False


def test_effective_severity_applies_temporal_weight():
    s = _sig("x", sev=Severity.CRITICAL, tw=0.5)
    assert effective_severity_score(s) == pytest.approx(0.5)   # 1.0 * 0.5
    s2 = _sig("x", sev=Severity.MEDIUM, tw=1.0)
    assert effective_severity_score(s2) == pytest.approx(0.6)


def test_critical_inflation_threshold():
    sigs = [_sig("a", sev=Severity.CRITICAL)] + [_sig("b", sev=Severity.LOW) for _ in range(8)]
    assert critical_inflation(sigs) is True       # 1/9 ≈ 11% > 10%
    sigs = [_sig("a", sev=Severity.CRITICAL)] + [_sig("b", sev=Severity.LOW) for _ in range(19)]
    assert critical_inflation(sigs) is False       # 1/20 = 5%


def test_candidate_pairs_opposite_polarity_same_category():
    a = _sig("revenue grew strongly", pol=SignalPolarity.POSITIVE)
    b = _sig("revenue fell sharply", pol=SignalPolarity.NEGATIVE)
    pairs = candidate_contradiction_pairs([a, b], embedder=_BagEmbedder())
    assert (0, 1) in pairs


def test_candidate_pairs_skips_different_entities():
    a = _sig("revenue fell", entity="Acme Corp")
    b = _sig("revenue fell", entity="Other Inc")
    assert candidate_contradiction_pairs([a, b], embedder=_BagEmbedder()) == []


# ── Node behaviour ────────────────────────────────────────────────────────────

async def test_node_empty_signals():
    out = await risk_analysis_agent_node(_state([]), provider=_FakeProvider())
    assert out == {"scored_signals": []}


async def test_node_scores_severity_from_llm_and_tracks_cost():
    sigs = [_sig("revenue fell 40%"), _sig("data breach affected users", cat=RiskCategory.CYBERSECURITY)]
    prov = _FakeProvider(severity=Severity.HIGH)
    out = await risk_analysis_agent_node(_state(sigs), provider=prov, embedder=_BagEmbedder())
    assert all(s.severity is Severity.HIGH for s in out["scored_signals"])
    assert out["llm_call_count"] >= 2
    assert out["total_cost"] > 0


async def test_node_flags_critical_for_human_review():
    prov = _FakeProvider(severity=Severity.CRITICAL)
    out = await risk_analysis_agent_node(_state([_sig("active fraud probe")]),
                                         provider=prov, embedder=_BagEmbedder())
    assert out["scored_signals"][0].requires_human_review is True


async def test_node_deduplicates_before_scoring():
    dupes = [_sig("revenue fell 40 percent", url=f"https://x/{i}", conf=0.5 + i * 0.1) for i in range(3)]
    # identical text -> bag embedder makes them cosine 1.0 -> one cluster
    out = await risk_analysis_agent_node(_state(dupes), provider=_FakeProvider(), embedder=_BagEmbedder())
    assert len(out["scored_signals"]) == 1
    assert out["scored_signals"][0].is_corroborated is True


async def test_node_flags_contradiction():
    a = _sig("revenue grew 20 percent", pol=SignalPolarity.POSITIVE)
    b = _sig("revenue fell 15 percent", pol=SignalPolarity.NEGATIVE)
    prov = _FakeProvider(severity=Severity.MEDIUM, contradiction=True)
    out = await risk_analysis_agent_node(_state([a, b]), provider=prov, embedder=_BagEmbedder())
    assert sum(1 for s in out["scored_signals"] if s.is_contradictory) == 2


async def test_node_respects_llm_budget(monkeypatch):
    # Isolate the upstream call budget from the synthesis reserve.
    monkeypatch.setattr("src.config.settings.synthesis_call_reserve", 0)
    monkeypatch.setattr("src.config.settings.max_llm_calls", 1)
    # distinct topics so dedup keeps all four
    topics = ["revenue fell", "data breach", "award won", "supplier fine"]
    sigs = [_sig(t, url=f"https://x/{i}") for i, t in enumerate(topics)]
    prov = _FakeProvider()
    out = await risk_analysis_agent_node(_state(sigs, llm_call_count=0), provider=prov, embedder=_BagEmbedder())
    # only 1 severity call allowed; no budget left for contradictions
    assert prov.call_count == 1
    assert out["llm_call_count"] == 1
    assert len(out["scored_signals"]) == 4   # all signals still returned (3 un-rescored)


async def test_node_reserves_synthesis_budget(monkeypatch):
    # Severity scoring must leave synthesis_call_reserve headroom out of the
    # global max_llm_calls so the final report is never starved.
    monkeypatch.setattr("src.config.settings.max_llm_calls", 20)
    monkeypatch.setattr("src.config.settings.synthesis_call_reserve", 8)
    monkeypatch.setattr(ra, "deduplicate", lambda sigs, embedder=None: list(sigs))
    sigs = [_sig(f"distinct risk number {i}", url=f"https://x/{i}") for i in range(15)]
    prov = _FakeProvider()
    out = await risk_analysis_agent_node(
        _state(sigs, llm_call_count=0), provider=prov, embedder=_BagEmbedder()
    )
    # budget = 20 - 0 - 8 (reserve) = 12 severity calls; 0 left for contradictions.
    assert prov.call_count == 12
    assert out["llm_call_count"] == 12
    assert len(out["scored_signals"]) == 15  # all returned (3 un-rescored)


async def test_node_isolates_scoring_failure():
    class _Boom(LLMProvider):
        async def complete(self, prompt, schema, *, system="", use_fast=True):
            raise RuntimeError("llm down")
    out = await risk_analysis_agent_node(_state([_sig("x")]), provider=_Boom(), embedder=_BagEmbedder())
    assert len(out["scored_signals"]) == 1          # signal preserved
    assert any("severity" in e for e in out["errors"])
