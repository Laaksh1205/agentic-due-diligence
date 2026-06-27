"""
Source credibility — deterministic defense against low-trust / planted data.

Quote-anchor verification (src/verification/quote_anchor.py) proves a signal's
snippet really appears in the fetched page; it does NOT prove the *source* is
truthful. A fabricated article indexed by web search / NewsAPI would still pass
quote verification. This module adds the missing axis: how much do we trust the
*source*, graded into tiers and combined with independent cross-source
corroboration.

It is intentionally deterministic — a tier lookup from the pipeline's existing
``SourceType`` classification plus a small curated domain allow/deny list. No
network calls, no LLM, so it adds zero latency/cost.

Consumed by the risk-analysis agent to:
  1. haircut ``confidence_score`` for low-trust sources (flows into category
     scores via synthesis, which already multiplies by confidence), and
  2. cap + flag a HIGH/CRITICAL finding that rests on a single low-credibility,
     uncorroborated source (it is never deleted — recall is preserved — but its
     severity is capped to MEDIUM, marked ``is_unverified`` and routed to HITL).
"""

from __future__ import annotations

import re
from enum import Enum

from src.models.signals import RiskSignal, Severity, SourceType


class CredibilityTier(str, Enum):
    PRIMARY = "PRIMARY"          # authoritative primary records — structurally hard to fake
    ESTABLISHED = "ESTABLISHED"  # reputable outlets / the company's own site
    GENERAL = "GENERAL"          # a real-looking publication not on the allowlist
    LOW = "LOW"                  # PR wires, blogs, forums, UGC, content farms


TIER_WEIGHT: dict[CredibilityTier, float] = {
    CredibilityTier.PRIMARY: 1.0,
    CredibilityTier.ESTABLISHED: 0.85,
    CredibilityTier.GENERAL: 0.6,
    CredibilityTier.LOW: 0.35,
}

# ── Severity-gate tuning ──────────────────────────────────────────────────────
_HIGH_SEVERITIES = {Severity.CRITICAL, Severity.HIGH}
_CAPPABLE_TIERS = {CredibilityTier.GENERAL, CredibilityTier.LOW}
_SEVERITY_CAP = Severity.MEDIUM
MIN_INDEPENDENT_FOR_HIGH = 2  # independent domains needed to keep HIGH/CRITICAL on a low-trust source

# ── Domain knowledge (small, curated, no PSL dependency) ──────────────────────

# Public suffixes with two labels — so registrable_domain keeps the right eTLD+1.
_TWO_LEVEL_TLDS = {
    "co.uk", "gov.uk", "org.uk", "ac.uk", "com.au", "co.in", "co.jp", "com.br",
    "co.za", "com.sg", "com.hk", "co.nz",
}

# Reputable news / data outlets → ESTABLISHED.
_ESTABLISHED_DOMAINS = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "nytimes.com",
    "washingtonpost.com", "apnews.com", "theguardian.com", "bbc.com", "bbc.co.uk",
    "cnbc.com", "forbes.com", "economist.com", "politico.com", "axios.com",
    "npr.org", "latimes.com", "marketwatch.com", "barrons.com", "fortune.com",
    "businessinsider.com", "theverge.com", "techcrunch.com", "arstechnica.com",
    "wired.com", "nature.com", "sciencemag.org", "propublica.org", "law360.com",
    "courthousenews.com", "thetimes.co.uk", "telegraph.co.uk", "nikkei.com",
    "scmp.com", "aljazeera.com", "cnn.com", "nbcnews.com", "cbsnews.com",
    "abcnews.go.com", "usatoday.com", "theinformation.com", "ap.org",
}

# PR wires (company-controlled), blogs, social / UGC, content farms → LOW.
# registrable_domain() collapses sub-domains, so "x.substack.com" maps here too.
_LOW_DOMAINS = {
    "prnewswire.com", "businesswire.com", "globenewswire.com", "prweb.com",
    "newswire.com", "einnews.com", "openpr.com", "accesswire.com", "issuewire.com",
    "medium.com", "substack.com", "blogspot.com", "wordpress.com", "wix.com",
    "weebly.com", "blogger.com", "tumblr.com", "reddit.com", "quora.com",
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "tiktok.com", "youtube.com",
}

_PRIMARY_TYPES = {
    SourceType.SEC_FILING,
    SourceType.COMPANY_REGISTRY,
    SourceType.COURT_RECORD,
    SourceType.SANCTIONS_LIST,
}


# ── Domain helpers ────────────────────────────────────────────────────────────

def registrable_domain(url: str) -> str:
    """Best-effort eTLD+1, e.g. 'https://www.a.reuters.co.uk/x' -> 'reuters.co.uk'.

    Avoids the tldextract dependency by special-casing a small set of two-level
    public suffixes — enough for source-independence counting and tier lookup.
    """
    m = re.match(r"https?://([^/]+)", (url or "").strip().lower())
    host = (m.group(1) if m else (url or "").strip().lower()).split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ── Classification ────────────────────────────────────────────────────────────

def classify_credibility(source_url: str, source_type: SourceType) -> tuple[CredibilityTier, float]:
    """Map a source to a (tier, weight). Deterministic — no I/O.

    Keys primarily off ``source_type`` (the pipeline already routes sec.gov →
    SEC_FILING, .gov → COURT_RECORD, registries → COMPANY_REGISTRY, etc. in
    research_agent._classify_source_type), refining only NEWS_ARTICLE by domain.
    """
    if source_type in _PRIMARY_TYPES:
        tier = CredibilityTier.PRIMARY
    elif source_type in (SourceType.COMPANY_WEBSITE, SourceType.INTERNAL_DOC):
        tier = CredibilityTier.ESTABLISHED
    else:  # NEWS_ARTICLE and any unknown → refine by domain
        dom = registrable_domain(source_url)
        if dom in _ESTABLISHED_DOMAINS:
            tier = CredibilityTier.ESTABLISHED
        elif dom in _LOW_DOMAINS:
            tier = CredibilityTier.LOW
        else:
            tier = CredibilityTier.GENERAL
    return tier, TIER_WEIGHT[tier]


def _tier_of(signal: RiskSignal) -> CredibilityTier:
    try:
        return CredibilityTier(signal.credibility_tier)
    except ValueError:
        # Unclassified (e.g. legacy signal) → treat as GENERAL (conservative).
        return CredibilityTier.GENERAL


# ── Confidence fusion (pure) ──────────────────────────────────────────────────

def effective_trust(source_credibility: float, independent_source_count: int) -> float:
    """Source trust lifted by independent corroboration, capped at 1.0."""
    return min(1.0, source_credibility + 0.15 * max(0, independent_source_count - 1))


def credibility_confidence_multiplier(source_credibility: float, independent_source_count: int) -> float:
    """0.4–1.0 multiplier: PRIMARY/corroborated ≈ 1.0; a lone LOW source ≈ 0.61."""
    return 0.4 + 0.6 * effective_trust(source_credibility, independent_source_count)


def apply_credibility_adjustment(signal: RiskSignal) -> RiskSignal:
    """Haircut ``confidence_score`` by source credibility + corroboration.

    Replaces the old flat +0.1 corroboration boost: trust is now graded and only
    ever lowers confidence (never inflates the extractor's own estimate). A
    PRIMARY or well-corroborated signal is left effectively unchanged.
    """
    mult = credibility_confidence_multiplier(signal.source_credibility, signal.independent_source_count)
    new_conf = max(0.0, min(1.0, signal.confidence_score * mult))
    return signal.model_copy(update={"confidence_score": new_conf})


# ── Severity gate (pure) ──────────────────────────────────────────────────────

def apply_credibility_gate(signal: RiskSignal) -> RiskSignal:
    """Cap + flag a HIGH/CRITICAL finding resting on a single low-trust source.

    A HIGH/CRITICAL signal whose source is GENERAL/LOW tier AND is not
    independently corroborated (fewer than ``MIN_INDEPENDENT_FOR_HIGH`` distinct
    domains) is capped to MEDIUM, marked ``is_unverified``, and routed to human
    review. It is never dropped. A single PRIMARY/ESTABLISHED source, or ≥2
    independent domains, is left untouched.
    """
    if (
        signal.severity in _HIGH_SEVERITIES
        and _tier_of(signal) in _CAPPABLE_TIERS
        and signal.independent_source_count < MIN_INDEPENDENT_FOR_HIGH
    ):
        return signal.model_copy(update={
            "severity": _SEVERITY_CAP,
            "is_unverified": True,
            "requires_human_review": True,
        })
    return signal


__all__ = [
    "CredibilityTier",
    "TIER_WEIGHT",
    "MIN_INDEPENDENT_FOR_HIGH",
    "registrable_domain",
    "classify_credibility",
    "effective_trust",
    "credibility_confidence_multiplier",
    "apply_credibility_adjustment",
    "apply_credibility_gate",
]
