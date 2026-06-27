"""Tests for synthesis_agent — Task 3.2.6."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from src.agents.synthesis_agent import (
    compute_category_scores,
    synthesis_agent_node,
    verify_citations,
)
from src.config import settings
from src.llm.base import LLMProvider
from src.llm.prompts import CategorySectionOutput, OverallSynthesisOutput, RecommendedActionOutput
from src.models.documents import DataSufficiency
from src.models.entities import EntityType, ResolvedEntity
from src.models.report import ActionPriority, DueDiligenceReport
from src.models.signals import RiskCategory, RiskSignal, Severity, SignalPolarity, SourceType


# ── Shared factories ──────────────────────────────────────────────────────────

def _make_signal(
    *,
    text: str = "Test risk signal.",
    risk_category: RiskCategory = RiskCategory.FINANCIAL,
    severity: Severity = Severity.HIGH,
    polarity: SignalPolarity = SignalPolarity.NEGATIVE,
    confidence: float = 0.9,
    temporal_weight: float = 1.0,
    source_url: str = "https://example.com",
    source_type: SourceType = SourceType.NEWS_ARTICLE,
) -> RiskSignal:
    return RiskSignal(
        text=text,
        source_url=source_url,
        source_type=source_type,
        source_snippet="Sample verbatim quote from the source document used in testing.",
        confidence_score=confidence,
        temporal_weight=temporal_weight,
        risk_category=risk_category,
        severity=severity,
        signal_polarity=polarity,
        entity_name="Acme Corp",
    )


def _make_entity() -> ResolvedEntity:
    return ResolvedEntity(
        canonical_name="Acme Corp",
        entity_type=EntityType.COMPANY,
        aliases=["Acme"],
        jurisdiction="us-de",
    )


def _base_state(
    signals: list[RiskSignal] | None = None,
    data_sufficiency: DataSufficiency = DataSufficiency.ADEQUATE,
) -> dict:
    sigs = signals or []
    return {
        "resolved_entity": _make_entity(),
        "scored_signals": sigs,
        "data_sufficiency": data_sufficiency,
        "evaluation_scope": "full",
        "llm_call_count": 0,
        "total_cost": 0.0,
        "sources_failed": [],
        "errors": [],
        "signals_extracted": len(sigs),
        "signals_rejected": 0,
    }


class MockProvider(LLMProvider):
    """Returns canned responses keyed by schema class."""

    def __init__(self, responses: dict | None = None) -> None:
        super().__init__()
        self._responses = responses or {}

    async def complete(self, prompt: str, schema, *, system: str = "", use_fast: bool = True):
        self.call_count += 1
        if schema in self._responses:
            return self._responses[schema]
        if schema is CategorySectionOutput:
            return CategorySectionOutput(section_text="Risk identified. [Signal-1]")
        if schema is OverallSynthesisOutput:
            return OverallSynthesisOutput(
                executive_summary="Acme Corp has financial risks. [Signal-1]",
                strengths_section="No material strengths identified from available data.",
                recommended_actions=[],
            )
        return schema()


# ── Pure function tests ───────────────────────────────────────────────────────

class TestComputeCategoryScores:
    def test_empty_returns_zero(self):
        scores, overall = compute_category_scores({})
        assert scores == {}
        assert overall == 0.0

    def test_critical_signal_scores_ten(self):
        sig = _make_signal(severity=Severity.CRITICAL, confidence=1.0, temporal_weight=1.0)
        scores, overall = compute_category_scores({RiskCategory.FINANCIAL: [sig]})
        assert scores[RiskCategory.FINANCIAL] == 10.0
        assert overall == 10.0

    def test_info_signal_score(self):
        sig = _make_signal(severity=Severity.INFO, confidence=1.0, temporal_weight=1.0)
        scores, _ = compute_category_scores({RiskCategory.ESG: [sig]})
        # 0.2 * 1.0 * 1.0 * 10 = 2.0
        assert scores[RiskCategory.ESG] == pytest.approx(2.0)

    def test_multiple_categories_weighted_average(self):
        crit = _make_signal(severity=Severity.CRITICAL, confidence=1.0, temporal_weight=1.0)
        low = _make_signal(severity=Severity.LOW, confidence=1.0, temporal_weight=1.0)
        scores, overall = compute_category_scores({
            RiskCategory.FINANCIAL: [crit],
            RiskCategory.LEGAL: [low],
        })
        assert scores[RiskCategory.FINANCIAL] == 10.0
        assert scores[RiskCategory.LEGAL] == pytest.approx(4.0)
        # Weighted average: (10.0×1 + 4.0×1) / 2 = 7.0
        assert overall == pytest.approx(7.0)

    def test_temporal_weight_applied(self):
        sig = _make_signal(severity=Severity.HIGH, confidence=1.0, temporal_weight=0.5)
        scores, _ = compute_category_scores({RiskCategory.OPERATIONAL: [sig]})
        # 0.8 * 1.0 * 0.5 * 10 = 4.0
        assert scores[RiskCategory.OPERATIONAL] == pytest.approx(4.0)

    def test_score_capped_at_ten(self):
        sigs = [_make_signal(severity=Severity.CRITICAL, confidence=1.0) for _ in range(5)]
        scores, _ = compute_category_scores({RiskCategory.FINANCIAL: sigs})
        assert scores[RiskCategory.FINANCIAL] <= 10.0

    def test_confidence_affects_score(self):
        low_conf = _make_signal(severity=Severity.HIGH, confidence=0.5, temporal_weight=1.0)
        hi_conf = _make_signal(severity=Severity.HIGH, confidence=1.0, temporal_weight=1.0)
        scores_low, _ = compute_category_scores({RiskCategory.FINANCIAL: [low_conf]})
        scores_hi, _ = compute_category_scores({RiskCategory.FINANCIAL: [hi_conf]})
        assert scores_low[RiskCategory.FINANCIAL] < scores_hi[RiskCategory.FINANCIAL]


class TestVerifyCitations:
    def test_no_citations_returns_empty(self):
        assert verify_citations("No citations here.", {1, 2, 3}) == []

    def test_all_valid_citations(self):
        text = "Risk found [Signal-1]. Another issue [Signal-2]."
        assert verify_citations(text, {1, 2}) == []

    def test_orphan_citation_detected(self):
        text = "Risk [Signal-1] and also [Signal-99]."
        orphans = verify_citations(text, {1, 2})
        assert 99 in orphans
        assert 1 not in orphans

    def test_multiple_orphans(self):
        text = "See [Signal-1] [Signal-5] [Signal-100]."
        orphans = verify_citations(text, {1})
        assert 5 in orphans
        assert 100 in orphans
        assert 1 not in orphans

    def test_empty_text_returns_empty(self):
        assert verify_citations("", {1, 2, 3}) == []

    def test_empty_valid_set_all_orphan(self):
        text = "Found [Signal-1] and [Signal-2]."
        orphans = verify_citations(text, set())
        assert sorted(orphans) == [1, 2]


# ── Node tests: zero signals ──────────────────────────────────────────────────

class TestSynthesisNodeNoSignals:
    @pytest.mark.asyncio
    async def test_returns_report_instance(self):
        result = await synthesis_agent_node(_base_state(), provider=MockProvider())
        assert isinstance(result["report"], DueDiligenceReport)

    @pytest.mark.asyncio
    async def test_no_llm_calls_made(self):
        provider = MockProvider()
        await synthesis_agent_node(_base_state(), provider=provider)
        assert provider.call_count == 0

    @pytest.mark.asyncio
    async def test_summary_says_no_signals(self):
        result = await synthesis_agent_node(_base_state(), provider=MockProvider())
        assert "no material risk signals" in result["report"].executive_summary.lower()

    @pytest.mark.asyncio
    async def test_caveat_prepended_for_sparse(self):
        state = _base_state(data_sufficiency=DataSufficiency.SPARSE)
        result = await synthesis_agent_node(state, provider=MockProvider())
        assert "IMPORTANT LIMITATION" in result["report"].executive_summary

    @pytest.mark.asyncio
    async def test_caveat_prepended_for_limited(self):
        state = _base_state(data_sufficiency=DataSufficiency.LIMITED)
        result = await synthesis_agent_node(state, provider=MockProvider())
        assert "IMPORTANT LIMITATION" in result["report"].executive_summary

    @pytest.mark.asyncio
    async def test_no_caveat_for_adequate(self):
        result = await synthesis_agent_node(_base_state(), provider=MockProvider())
        assert "IMPORTANT LIMITATION" not in result["report"].executive_summary

    @pytest.mark.asyncio
    async def test_no_caveat_for_rich(self):
        state = _base_state(data_sufficiency=DataSufficiency.RICH)
        result = await synthesis_agent_node(state, provider=MockProvider())
        assert "IMPORTANT LIMITATION" not in result["report"].executive_summary

    @pytest.mark.asyncio
    async def test_empty_signal_lists_in_report(self):
        result = await synthesis_agent_node(_base_state(), provider=MockProvider())
        report = result["report"]
        assert report.risk_signals == []
        assert report.positive_signals == []
        assert report.overall_risk_score == 0.0


# ── Node tests: with signals ──────────────────────────────────────────────────

class TestSynthesisNodeWithSignals:
    @pytest.mark.asyncio
    async def test_report_contains_risk_signals(self):
        sigs = [_make_signal(), _make_signal(text="Second risk.")]
        result = await synthesis_agent_node(_base_state(signals=sigs), provider=MockProvider())
        assert len(result["report"].risk_signals) == 2

    @pytest.mark.asyncio
    async def test_positive_signals_separated(self):
        neg = _make_signal(polarity=SignalPolarity.NEGATIVE)
        pos = _make_signal(polarity=SignalPolarity.POSITIVE, severity=Severity.INFO)
        result = await synthesis_agent_node(
            _base_state(signals=[neg, pos]), provider=MockProvider()
        )
        report = result["report"]
        assert len(report.risk_signals) == 1
        assert len(report.positive_signals) == 1

    @pytest.mark.asyncio
    async def test_category_scores_populated(self):
        sig = _make_signal(severity=Severity.CRITICAL, confidence=1.0, temporal_weight=1.0)
        result = await synthesis_agent_node(_base_state(signals=[sig]), provider=MockProvider())
        report = result["report"]
        assert RiskCategory.FINANCIAL in report.category_scores
        assert report.overall_risk_score > 0

    @pytest.mark.asyncio
    async def test_caveat_prepended_with_signals_limited(self):
        sigs = [_make_signal()]
        state = _base_state(signals=sigs, data_sufficiency=DataSufficiency.LIMITED)
        result = await synthesis_agent_node(state, provider=MockProvider())
        assert result["report"].executive_summary.startswith("IMPORTANT LIMITATION")

    @pytest.mark.asyncio
    async def test_caveat_prepended_with_signals_sparse(self):
        sigs = [_make_signal()]
        state = _base_state(signals=sigs, data_sufficiency=DataSufficiency.SPARSE)
        result = await synthesis_agent_node(state, provider=MockProvider())
        assert result["report"].executive_summary.startswith("IMPORTANT LIMITATION")

    @pytest.mark.asyncio
    async def test_no_caveat_for_adequate_with_signals(self):
        sigs = [_make_signal()]
        result = await synthesis_agent_node(_base_state(signals=sigs), provider=MockProvider())
        assert "IMPORTANT LIMITATION" not in result["report"].executive_summary

    @pytest.mark.asyncio
    async def test_llm_call_count_tracked(self):
        # 1 FINANCIAL signal → 1 category call + 1 overall call = 2 total
        sigs = [_make_signal()]
        provider = MockProvider()
        result = await synthesis_agent_node(_base_state(signals=sigs), provider=provider)
        assert result["llm_call_count"] == 2

    @pytest.mark.asyncio
    async def test_two_categories_three_calls(self):
        # 2 categories → 2 category calls + 1 overall = 3
        sigs = [
            _make_signal(risk_category=RiskCategory.FINANCIAL),
            _make_signal(risk_category=RiskCategory.LEGAL),
        ]
        provider = MockProvider()
        result = await synthesis_agent_node(_base_state(signals=sigs), provider=provider)
        assert result["llm_call_count"] == 3

    @pytest.mark.asyncio
    async def test_recommended_actions_priority_immediate(self):
        crit_sig = _make_signal(severity=Severity.CRITICAL)
        action_out = RecommendedActionOutput(
            description="Investigate immediately.",
            priority="IMMEDIATE",
            signal_refs=["[Signal-1]"],
        )
        overall_out = OverallSynthesisOutput(
            executive_summary="Critical issue. [Signal-1]",
            strengths_section="No material strengths identified from available data.",
            recommended_actions=[action_out],
        )
        provider = MockProvider(responses={OverallSynthesisOutput: overall_out})
        result = await synthesis_agent_node(
            _base_state(signals=[crit_sig]), provider=provider
        )
        report = result["report"]
        assert len(report.recommended_actions) == 1
        assert report.recommended_actions[0].priority == ActionPriority.IMMEDIATE
        assert crit_sig.id in report.recommended_actions[0].related_signals

    @pytest.mark.asyncio
    async def test_recommended_actions_signal_refs_mapped(self):
        sig_a = _make_signal(severity=Severity.HIGH, risk_category=RiskCategory.FINANCIAL)
        sig_b = _make_signal(severity=Severity.HIGH, risk_category=RiskCategory.LEGAL)
        action_out = RecommendedActionOutput(
            description="Engage legal counsel.",
            priority="SHORT_TERM",
            signal_refs=["[Signal-1]", "[Signal-2]"],
        )
        overall_out = OverallSynthesisOutput(
            executive_summary="Issues found. [Signal-1] [Signal-2]",
            strengths_section="No material strengths identified from available data.",
            recommended_actions=[action_out],
        )
        provider = MockProvider(responses={OverallSynthesisOutput: overall_out})
        result = await synthesis_agent_node(
            _base_state(signals=[sig_a, sig_b]), provider=provider
        )
        action = result["report"].recommended_actions[0]
        assert action.priority == ActionPriority.SHORT_TERM
        assert sig_a.id in action.related_signals
        assert sig_b.id in action.related_signals

    @pytest.mark.asyncio
    async def test_sources_consulted_deduped_by_url(self):
        sig1 = _make_signal(source_url="https://sec.gov/f1", source_type=SourceType.SEC_FILING)
        sig2 = _make_signal(source_url="https://news.com/a", source_type=SourceType.NEWS_ARTICLE)
        sig3 = _make_signal(source_url="https://sec.gov/f1", source_type=SourceType.SEC_FILING)
        result = await synthesis_agent_node(
            _base_state(signals=[sig1, sig2, sig3]), provider=MockProvider()
        )
        assert len(result["report"].sources_consulted) == 2

    @pytest.mark.asyncio
    async def test_sources_failed_from_state(self):
        sigs = [_make_signal()]
        state = _base_state(signals=sigs)
        state["sources_failed"] = ["registry_lookup", "companies_house"]
        result = await synthesis_agent_node(state, provider=MockProvider())
        assert len(result["report"].sources_failed) == 2

    @pytest.mark.asyncio
    async def test_llm_failure_isolated(self):
        """LLM errors are captured in errors list; report still returned."""
        class FailProvider(LLMProvider):
            async def complete(self, prompt, schema, *, system="", use_fast=True):
                self.call_count += 1
                raise RuntimeError("API timeout")

        sigs = [_make_signal()]
        result = await synthesis_agent_node(_base_state(signals=sigs), provider=FailProvider())
        assert len(result["errors"]) > 0
        assert "report" in result

    @pytest.mark.asyncio
    async def test_budget_exhausted_skips_llm_calls(self):
        sigs = [_make_signal()]
        state = _base_state(signals=sigs)
        state["llm_call_count"] = settings.max_llm_calls  # exhaust budget
        provider = MockProvider()
        result = await synthesis_agent_node(state, provider=provider)
        assert provider.call_count == 0
        assert "report" in result

    @pytest.mark.asyncio
    async def test_orphan_citation_logs_warning(self):
        sigs = [_make_signal()]
        # LLM returns a section with Signal-99 which doesn't exist
        cat_out = CategorySectionOutput(
            section_text="Risk identified. [Signal-1] See also [Signal-99]."
        )
        overall_out = OverallSynthesisOutput(
            executive_summary="Issue found. [Signal-1]",
            strengths_section="No material strengths identified from available data.",
            recommended_actions=[],
        )
        provider = MockProvider(responses={
            CategorySectionOutput: cat_out,
            OverallSynthesisOutput: overall_out,
        })
        with patch("src.agents.synthesis_agent.logger") as mock_logger:
            result = await synthesis_agent_node(_base_state(signals=sigs), provider=provider)
            warning_msgs = " ".join(str(c) for c in mock_logger.warning.call_args_list)
            assert "orphan" in warning_msgs.lower()
        assert "report" in result  # report still built

    @pytest.mark.asyncio
    async def test_metadata_llm_call_count_accurate(self):
        sigs = [_make_signal()]
        result = await synthesis_agent_node(_base_state(signals=sigs), provider=MockProvider())
        report = result["report"]
        assert report.metadata.llm_call_count == result["llm_call_count"]

    @pytest.mark.asyncio
    async def test_detailed_sections_keys_are_risk_categories(self):
        sigs = [
            _make_signal(risk_category=RiskCategory.FINANCIAL),
            _make_signal(risk_category=RiskCategory.CYBERSECURITY),
        ]
        result = await synthesis_agent_node(_base_state(signals=sigs), provider=MockProvider())
        for k in result["report"].detailed_sections:
            assert isinstance(k, RiskCategory)
