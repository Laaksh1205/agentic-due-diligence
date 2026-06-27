"""Tests for the source-credibility layer (src/analysis/source_credibility.py).

All deterministic — no network, no LLM, no embeddings.
"""

import pytest

from src.analysis.source_credibility import (
    CredibilityTier,
    apply_credibility_adjustment,
    apply_credibility_gate,
    classify_credibility,
    credibility_confidence_multiplier,
    registrable_domain,
)
from src.models.signals import (
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
    SourceType,
)


def _sig(*, sev=Severity.CRITICAL, tier="LOW", cred=0.35, indep=1, conf=0.9):
    return RiskSignal(
        text="t", source_url="https://blog.example/x", source_type=SourceType.NEWS_ARTICLE,
        source_snippet="snippet anchoring the signal text for the record here please",
        confidence_score=conf, risk_category=RiskCategory.LEGAL, severity=sev,
        signal_polarity=SignalPolarity.NEGATIVE, entity_name="Acme",
        source_credibility=cred, credibility_tier=tier, independent_source_count=indep,
    )


# ── registrable_domain (eTLD+1) ───────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.reuters.com/article/x", "reuters.com"),
    ("http://sub.a.reuters.co.uk/y", "reuters.co.uk"),
    ("https://news.bbc.co.uk/", "bbc.co.uk"),
    ("https://company.substack.com/p/post", "substack.com"),
    ("https://sec.gov/x", "sec.gov"),
    ("https://x/0", "x"),  # malformed/no-TLD host (test fixtures use these)
])
def test_registrable_domain(url, expected):
    assert registrable_domain(url) == expected


# ── classify_credibility ──────────────────────────────────────────────────────

def test_primary_types_are_primary():
    for st in (SourceType.SEC_FILING, SourceType.COMPANY_REGISTRY,
               SourceType.COURT_RECORD, SourceType.SANCTIONS_LIST):
        tier, w = classify_credibility("https://sec.gov/x", st)
        assert tier is CredibilityTier.PRIMARY and w == 1.0


def test_company_website_is_established():
    tier, _ = classify_credibility("https://acme.com/about", SourceType.COMPANY_WEBSITE)
    assert tier is CredibilityTier.ESTABLISHED


def test_reputable_outlet_is_established():
    tier, _ = classify_credibility("https://www.reuters.com/a", SourceType.NEWS_ARTICLE)
    assert tier is CredibilityTier.ESTABLISHED


def test_pr_wire_and_blog_are_low():
    assert classify_credibility("https://www.prnewswire.com/x", SourceType.NEWS_ARTICLE)[0] is CredibilityTier.LOW
    assert classify_credibility("https://foo.substack.com/p", SourceType.NEWS_ARTICLE)[0] is CredibilityTier.LOW
    assert classify_credibility("https://reddit.com/r/x", SourceType.NEWS_ARTICLE)[0] is CredibilityTier.LOW


def test_unknown_news_domain_is_general_not_low():
    # An obscure-but-real outlet should not be over-penalized (protects recall).
    tier, _ = classify_credibility("https://some-local-paper.example/a", SourceType.NEWS_ARTICLE)
    assert tier is CredibilityTier.GENERAL


# ── confidence fusion ─────────────────────────────────────────────────────────

def test_confidence_multiplier_bounds():
    assert credibility_confidence_multiplier(1.0, 1) == pytest.approx(1.0)       # PRIMARY single
    assert credibility_confidence_multiplier(0.35, 1) == pytest.approx(0.61)     # LOW single
    assert credibility_confidence_multiplier(0.35, 3) == pytest.approx(0.79)     # LOW + 3 domains


def test_apply_adjustment_haircuts_low_trust_only():
    primary = _sig(tier="PRIMARY", cred=1.0, indep=1, conf=0.9)
    assert apply_credibility_adjustment(primary).confidence_score == pytest.approx(0.9)
    low = _sig(tier="LOW", cred=0.35, indep=1, conf=1.0)
    assert apply_credibility_adjustment(low).confidence_score == pytest.approx(0.61)


# ── severity gate (the headline guarantee) ────────────────────────────────────

def test_gate_caps_low_trust_uncorroborated_critical():
    g = apply_credibility_gate(_sig(sev=Severity.CRITICAL, tier="LOW", indep=1))
    assert g.severity is Severity.MEDIUM
    assert g.is_unverified is True
    assert g.requires_human_review is True


def test_gate_caps_general_uncorroborated_high():
    g = apply_credibility_gate(_sig(sev=Severity.HIGH, tier="GENERAL", cred=0.6, indep=1))
    assert g.severity is Severity.MEDIUM and g.is_unverified is True


def test_gate_keeps_single_primary_critical():
    g = apply_credibility_gate(_sig(sev=Severity.CRITICAL, tier="PRIMARY", cred=1.0, indep=1))
    assert g.severity is Severity.CRITICAL and g.is_unverified is False


def test_gate_keeps_corroborated_low_trust_critical():
    # Two independent domains clears the bar even for a low-trust tier.
    g = apply_credibility_gate(_sig(sev=Severity.CRITICAL, tier="LOW", cred=0.35, indep=2))
    assert g.severity is Severity.CRITICAL and g.is_unverified is False


def test_gate_ignores_non_high_severity():
    g = apply_credibility_gate(_sig(sev=Severity.MEDIUM, tier="LOW", indep=1))
    assert g.severity is Severity.MEDIUM and g.is_unverified is False
