"""
Tests for the Extraction Agent — Task 2.3.

All LLM calls are mocked via FakeProvider, so these are deterministic and need
no network (immune to the Gemini outage). Covers: ExtractedSignal -> RiskSignal
mapping, quote-anchor rejection, FLAG confidence halving, max_signals_per_doc
truncation, max_llm_calls guardrail, concurrency call/cost tracking, per-doc
error isolation, and the empty-documents fast path.
"""

import pytest

from src.agents.extraction_agent import (
    DocExtractionStats,
    extraction_agent_node,
    verify_and_build,
)
from src.llm.base import LLMCall, LLMProvider
from src.llm.prompts import ExtractedSignal, ExtractionResult
from src.models.documents import RawDocument
from src.models.entities import ResolvedEntity
from src.models.signals import RiskCategory, Severity, SignalPolarity, SourceType

DOC_TEXT = (
    "Acme Corp was fined $5 million by the SEC in 2024 for accounting fraud. "
    "The company also won a national innovation award in 2023 for clean energy."
)


# ── Fakes / helpers ───────────────────────────────────────────────────────────

class FakeProvider(LLMProvider):
    """Returns queued ExtractionResults; records a fake call+cost each time."""

    def __init__(self, results, cost=0.0001, raises=False):
        super().__init__()
        self._results = list(results)
        self._cost = cost
        self._raises = raises

    async def complete(self, prompt, schema, *, system="", use_fast=True):
        if self._raises:
            raise RuntimeError("simulated LLM failure")
        self._record(LLMCall("fake", 100, 50, self._cost))
        return self._results.pop(0) if self._results else ExtractionResult(signals=[])


def _doc(text=DOC_TEXT, url="https://news.example.com/acme", st=SourceType.NEWS_ARTICLE):
    return RawDocument(source_url=url, source_type=st, raw_text=text, entity_name="Acme Corp")


def _es(snippet, *, text="A signal.", cat=RiskCategory.LEGAL, sev=Severity.HIGH,
        pol=SignalPolarity.NEGATIVE, conf=0.9, data_date="2024"):
    return ExtractedSignal(
        text=text, source_snippet=snippet, risk_category=cat, severity=sev,
        signal_polarity=pol, confidence_score=conf, data_date=data_date,
    )


def _state(documents, *, llm_call_count=0, total_cost=0.0, entity_name="Acme Corp"):
    return {
        "documents": documents,
        "resolved_entity": ResolvedEntity(canonical_name=entity_name),
        "run_id": "run-test",
        "llm_call_count": llm_call_count,
        "total_cost": total_cost,
        "errors": [],
    }


# ── verify_and_build (mapping + verification) ─────────────────────────────────

async def test_maps_verbatim_signal_to_risk_signal():
    snippet = "Acme Corp was fined $5 million by the SEC in 2024 for accounting fraud."
    kept, stats = await verify_and_build(
        _doc(), ExtractionResult(signals=[_es(snippet)]), "Acme Corp",
    )
    assert stats.kept == 1 and stats.rejected == 0
    sig = kept[0]
    assert sig.source_url == "https://news.example.com/acme"
    assert sig.source_type is SourceType.NEWS_ARTICLE
    assert sig.entity_name == "Acme Corp"
    assert sig.confidence_score == pytest.approx(0.9)  # PASS -> unchanged
    assert 0.3 <= sig.temporal_weight < 1.0            # 2024 event, in the past


async def test_rejects_non_verbatim_snippet():
    kept, stats = await verify_and_build(
        _doc(),
        ExtractionResult(signals=[_es("a fabricated quote that is not in the document at all")]),
        "Acme Corp",
    )
    assert kept == [] and stats.rejected == 1


async def test_flag_halves_confidence(monkeypatch):
    # Force the FLAG verdict deterministically.
    from src.verification.quote_anchor import AnchorResult, AnchorVerdict
    monkeypatch.setattr(
        "src.agents.extraction_agent.classify_anchor",
        lambda snippet, source: AnchorResult(AnchorVerdict.FLAG, 78.0, 0.5),
    )
    kept, stats = await verify_and_build(
        _doc(), ExtractionResult(signals=[_es("anything", conf=0.8)]), "Acme Corp",
    )
    assert stats.flagged == 1
    assert kept[0].confidence_score == pytest.approx(0.4)  # 0.8 * 0.5


async def test_truncates_to_max_signals_per_doc(monkeypatch):
    monkeypatch.setattr("src.config.settings.max_signals_per_doc", 7)
    # 10 verbatim signals (snippet present in DOC_TEXT) -> truncated to 7
    sig = _es("Acme Corp was fined $5 million by the SEC in 2024 for accounting fraud.")
    result = ExtractionResult(signals=[sig.model_copy() for _ in range(10)])
    kept, stats = await verify_and_build(_doc(), result, "Acme Corp")
    assert stats.extracted == 10
    assert len(kept) == 7


# ── node: concurrency, guardrails, isolation ──────────────────────────────────

async def test_node_extracts_and_tracks_calls_and_cost():
    snippet = "won a national innovation award in 2023 for clean energy"
    docs = [_doc(url=f"https://x/{i}") for i in range(3)]
    provider = FakeProvider([
        ExtractionResult(signals=[_es(snippet, pol=SignalPolarity.POSITIVE, sev=Severity.INFO,
                                      cat=RiskCategory.ESG)])
        for _ in range(3)
    ])
    out = await extraction_agent_node(_state(docs), provider=provider)
    assert len(out["raw_signals"]) == 3
    assert out["llm_call_count"] == 3          # 0 prior + 3 calls
    assert out["total_cost"] == pytest.approx(0.0003)
    assert out["errors"] == []


async def test_node_empty_documents_fast_path():
    provider = FakeProvider([])
    out = await extraction_agent_node(_state([]), provider=provider)
    assert out == {"raw_signals": []}
    assert provider.call_count == 0            # provider never used


async def test_node_respects_max_llm_calls_guardrail(monkeypatch):
    monkeypatch.setattr("src.config.settings.max_llm_calls", 5)
    provider = FakeProvider([])
    # already at the cap -> budget 0 -> no extraction, provider untouched
    out = await extraction_agent_node(_state([_doc()], llm_call_count=5), provider=provider)
    assert out == {"raw_signals": []}
    assert provider.call_count == 0


async def test_node_caps_documents_to_remaining_budget(monkeypatch):
    # Isolate the upstream call budget from the synthesis reserve.
    monkeypatch.setattr("src.config.settings.synthesis_call_reserve", 0)
    monkeypatch.setattr("src.config.settings.max_llm_calls", 2)
    snippet = "accounting fraud"
    docs = [_doc(url=f"https://x/{i}") for i in range(5)]
    provider = FakeProvider([ExtractionResult(signals=[_es(snippet)]) for _ in range(5)])
    # 1 prior call -> budget 1 -> only 1 document processed
    out = await extraction_agent_node(_state(docs, llm_call_count=1), provider=provider)
    assert provider.call_count == 1
    assert out["llm_call_count"] == 2


async def test_node_isolates_llm_failure():
    provider = FakeProvider([], raises=True)
    out = await extraction_agent_node(_state([_doc(), _doc(url="https://x/2")]), provider=provider)
    assert out["raw_signals"] == []
    assert len(out["errors"]) == 2             # both docs error, batch survives


def test_docstats_defaults():
    s = DocExtractionStats()
    assert (s.extracted, s.kept, s.rejected, s.flagged) == (0, 0, 0, 0)
