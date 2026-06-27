"""
Task 1.1.5 — Unit tests for all Pydantic data models.
Run: pytest tests/test_models.py -v
"""

import uuid
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.models.documents import DataSufficiency, RawDocument
from src.models.entities import Entity, EntityType, ResolvedEntity
from src.models.report import (
    Action,
    ActionPriority,
    DueDiligenceReport,
    ReportMetadata,
    Source,
)
from src.models.signals import (
    HumanVerdict,
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
    SourceType,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def make_signal(**overrides) -> RiskSignal:
    defaults = dict(
        text="Company reported a 40% revenue decline in Q3 2025.",
        source_url="https://example.com/news/article-1",
        source_type=SourceType.NEWS_ARTICLE,
        source_snippet="Company reported a 40% revenue decline in Q3 2025 compared to the same period last year.",
        confidence_score=0.85,
        risk_category=RiskCategory.FINANCIAL,
        severity=Severity.HIGH,
        signal_polarity=SignalPolarity.NEGATIVE,
        entity_name="Acme Corp",
    )
    defaults.update(overrides)
    return RiskSignal(**defaults)


def make_resolved_entity(**overrides) -> ResolvedEntity:
    defaults = dict(
        canonical_name="TESLA, INC.",
        aliases=["Tesla", "TSLA", "Tesla Motors"],
        jurisdiction="us-de",
        is_public=True,
        sec_cik="0001318605",
    )
    defaults.update(overrides)
    return ResolvedEntity(**defaults)


# ── Entity ───────────────────────────────────────────────────────────────────

class TestEntity:
    def test_valid_person(self):
        e = Entity(name="John Smith", entity_type=EntityType.PERSON, role="CEO")
        assert e.name == "John Smith"
        assert e.canonical_id is None

    def test_valid_company_with_id(self):
        e = Entity(name="Acme LLC", entity_type=EntityType.COMPANY, role="Subsidiary", canonical_id="REG-123")
        assert e.canonical_id == "REG-123"

    def test_invalid_entity_type(self):
        with pytest.raises(ValidationError):
            Entity(name="X", entity_type="ALIEN", role="X")

    def test_all_entity_types_valid(self):
        for et in EntityType:
            e = Entity(name="X", entity_type=et, role="Role")
            assert e.entity_type == et


# ── ResolvedEntity ────────────────────────────────────────────────────────────

class TestResolvedEntity:
    def test_public_company(self):
        e = make_resolved_entity()
        assert e.is_public is True
        assert "TSLA" in e.aliases
        assert e.sec_cik == "0001318605"

    def test_private_company_defaults(self):
        e = ResolvedEntity(canonical_name="Stripe, Inc.")
        assert e.is_public is False
        assert e.aliases == []
        assert e.sec_cik is None
        assert e.jurisdiction is None

    def test_uk_company(self):
        e = ResolvedEntity(
            canonical_name="REVOLUT LTD",
            jurisdiction="gb",
            companies_house_number="08804411",
            is_public=False,
        )
        assert e.companies_house_number == "08804411"
        assert e.sec_cik is None


# ── RiskSignal ────────────────────────────────────────────────────────────────

class TestRiskSignal:
    def test_valid_signal(self):
        s = make_signal()
        assert isinstance(s.id, uuid.UUID)
        assert s.temporal_weight == 1.0
        assert s.is_corroborated is False
        assert s.requires_human_review is False
        assert s.human_verdict is None

    def test_auto_uuid(self):
        s1, s2 = make_signal(), make_signal()
        assert s1.id != s2.id

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            make_signal(confidence_score=1.5)
        with pytest.raises(ValidationError):
            make_signal(confidence_score=-0.1)

    def test_temporal_weight_bounds(self):
        with pytest.raises(ValidationError):
            make_signal(temporal_weight=1.1)
        with pytest.raises(ValidationError):
            make_signal(temporal_weight=-0.1)

    def test_invalid_enum_fields(self):
        with pytest.raises(ValidationError):
            make_signal(severity="EXTREME")
        with pytest.raises(ValidationError):
            make_signal(risk_category="LEGAL_RISK")
        with pytest.raises(ValidationError):
            make_signal(signal_polarity="BAD")
        with pytest.raises(ValidationError):
            make_signal(source_type="TWITTER")

    def test_all_severity_levels(self):
        for sev in Severity:
            s = make_signal(severity=sev)
            assert s.severity == sev

    def test_all_risk_categories(self):
        for cat in RiskCategory:
            s = make_signal(risk_category=cat)
            assert s.risk_category == cat

    def test_positive_signal(self):
        s = make_signal(
            signal_polarity=SignalPolarity.POSITIVE,
            text="Company achieved ISO 27001 certification in 2024.",
            severity=Severity.INFO,
        )
        assert s.signal_polarity == SignalPolarity.POSITIVE

    def test_human_verdict_valid(self):
        s = make_signal(human_verdict=HumanVerdict.CONFIRMED, requires_human_review=True)
        assert s.human_verdict == HumanVerdict.CONFIRMED

    def test_corroborating_signals(self):
        uid = uuid.uuid4()
        s = make_signal(is_corroborated=True, corroborating_signals=[uid])
        assert uid in s.corroborating_signals

    def test_related_entities(self):
        e = Entity(name="FTC", entity_type=EntityType.REGULATOR, role="Plaintiff")
        s = make_signal(related_entities=[e])
        assert s.related_entities[0].name == "FTC"

    def test_data_date_optional(self):
        s = make_signal(data_date=date(2024, 6, 15))
        assert s.data_date == date(2024, 6, 15)
        s2 = make_signal()
        assert s2.data_date is None

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            RiskSignal(text="X")  # missing many required fields

    def test_temporal_weight_formula(self):
        """Verify the design-doc formula: max(0.3, 1.0 - (years * 0.15))"""
        cases = [
            (0, 1.0),
            (1, 0.85),
            (3, 0.55),
            (5, 0.30),
            (10, 0.30),  # floor
        ]
        for years, expected in cases:
            result = max(0.3, 1.0 - (years * 0.15))
            assert abs(result - expected) < 1e-9, f"years={years}: got {result}, expected {expected}"


# ── DataSufficiency ───────────────────────────────────────────────────────────

class TestDataSufficiency:
    def test_all_tiers(self):
        for ds in DataSufficiency:
            assert isinstance(ds.value, str)

    def test_ordering_by_value(self):
        tiers = [DataSufficiency.RICH, DataSufficiency.ADEQUATE, DataSufficiency.LIMITED, DataSufficiency.SPARSE]
        assert len(tiers) == 4

    def test_invalid_tier(self):
        with pytest.raises(ValidationError):
            DueDiligenceReport(
                target_entity=make_resolved_entity(),
                evaluation_scope="full",
                data_sufficiency="EXCELLENT",  # invalid value
            )


# ── RawDocument ───────────────────────────────────────────────────────────────

class TestRawDocument:
    def test_valid(self):
        doc = RawDocument(
            source_url="https://example.com/filing.html",
            source_type=SourceType.SEC_FILING,
            raw_text="Annual report text...",
        )
        assert isinstance(doc.fetch_timestamp, datetime)
        assert doc.metadata == {}
        assert doc.entity_name is None

    def test_with_metadata(self):
        doc = RawDocument(
            source_url="https://example.com",
            source_type=SourceType.COMPANY_WEBSITE,
            raw_text="About us page.",
            metadata={"title": "About", "word_count": 500},
            entity_name="Acme Corp",
        )
        assert doc.metadata["title"] == "About"
        assert doc.entity_name == "Acme Corp"

    def test_invalid_source_type(self):
        with pytest.raises(ValidationError):
            RawDocument(
                source_url="https://x.com",
                source_type="BLOG_POST",
                raw_text="x",
            )


# ── Report models ─────────────────────────────────────────────────────────────

class TestAction:
    def test_valid(self):
        uid = uuid.uuid4()
        a = Action(
            description="Request audited financials for FY2025",
            priority=ActionPriority.IMMEDIATE,
            related_signals=[uid],
        )
        assert a.priority == ActionPriority.IMMEDIATE
        assert uid in a.related_signals

    def test_invalid_priority(self):
        with pytest.raises(ValidationError):
            Action(description="X", priority="URGENT")


class TestReportMetadata:
    def test_defaults(self):
        m = ReportMetadata()
        assert isinstance(m.run_id, uuid.UUID)
        assert m.estimated_cost_usd == 0.0
        assert m.llm_call_count == 0

    def test_with_values(self):
        m = ReportMetadata(
            model_versions={"fast": "gemini-2.5-flash", "smart": "gemini-2.5-pro"},
            estimated_cost_usd=0.12,
            latency_seconds=47.3,
            llm_call_count=18,
        )
        assert m.model_versions["fast"] == "gemini-2.5-flash"
        assert m.estimated_cost_usd == 0.12


class TestDueDiligenceReport:
    def test_minimal_report(self):
        entity = make_resolved_entity()
        report = DueDiligenceReport(
            target_entity=entity,
            evaluation_scope="full",
            data_sufficiency=DataSufficiency.ADEQUATE,
        )
        assert report.overall_risk_score == 0.0
        assert report.risk_signals == []
        assert report.recommended_actions == []
        assert isinstance(report.metadata.run_id, uuid.UUID)

    def test_report_with_signals(self):
        entity = make_resolved_entity()
        s = make_signal()
        report = DueDiligenceReport(
            target_entity=entity,
            evaluation_scope="compliance",
            data_sufficiency=DataSufficiency.RICH,
            risk_signals=[s],
            category_scores={RiskCategory.FINANCIAL: 7.5},
            overall_risk_score=6.2,
            executive_summary="Acme Corp presents elevated financial risk.",
        )
        assert len(report.risk_signals) == 1
        assert report.category_scores[RiskCategory.FINANCIAL] == 7.5

    def test_sources(self):
        entity = make_resolved_entity()
        ok_src = Source(url="https://registry-lookup.com", source_type=SourceType.COMPANY_REGISTRY, name="Registry Lookup")
        fail_src = Source(url="https://down.api.com", source_type=SourceType.SEC_FILING, name="SEC EDGAR", error="Timeout")
        report = DueDiligenceReport(
            target_entity=entity,
            evaluation_scope="full",
            data_sufficiency=DataSufficiency.LIMITED,
            sources_consulted=[ok_src],
            sources_failed=[fail_src],
        )
        assert report.sources_consulted[0].name == "Registry Lookup"
        assert report.sources_failed[0].error == "Timeout"

    def test_invalid_data_sufficiency(self):
        with pytest.raises(ValidationError):
            DueDiligenceReport(
                target_entity=make_resolved_entity(),
                evaluation_scope="full",
                data_sufficiency="EXCELLENT",
            )
