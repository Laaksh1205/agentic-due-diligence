"""
Tests for the extraction / severity prompts — Task 2.1.

These are deterministic, no-LLM tests. They guard the *engineering contract* of
the prompt artifacts: that every few-shot snippet is a verbatim span of its
document (the quote-anchor invariant), that the examples cover all 7 risk
categories plus positive signals plus "do-NOT-extract" cases, and that the
prompt builders carry the key instructions. The LLM-in-the-loop precision gate
(Tasks 2.1.3 / 2.1.4, "≥80% on 30 signals") is a separate manual evaluation.
"""

import re

from src.llm.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    FEW_SHOT_EXAMPLES,
    MAX_SIGNALS_PER_DOC,
    SEVERITY_SYSTEM_PROMPT,
    ExtractionResult,
    SeverityAssessment,
    build_extraction_prompt,
    build_severity_prompt,
)
from src.models.signals import RiskCategory, Severity, SignalPolarity


def _norm(s: str) -> str:
    """Whitespace-insensitive normalisation (snippets may re-wrap)."""
    return " ".join(s.split())


def _wc(s: str) -> int:
    return len(s.split())


# ── Few-shot integrity ────────────────────────────────────────────────────────

def test_five_examples_covering_required_source_types():
    assert len(FEW_SHOT_EXAMPLES) == 5
    types = {ex.source_type for ex in FEW_SHOT_EXAMPLES}
    # design doc 2.1.2: news, filing, registry, regulatory, website
    assert {"NEWS_ARTICLE", "SEC_FILING", "COMPANY_REGISTRY", "COURT_RECORD", "COMPANY_WEBSITE"} == types


def test_every_snippet_is_verbatim_substring_of_its_document():
    for ex in FEW_SHOT_EXAMPLES:
        doc = _norm(ex.document)
        for sig in ex.extraction.signals:
            assert _norm(sig.source_snippet) in doc, (
                f"snippet not verbatim in {ex.source_type}: {sig.source_snippet!r}"
            )


def test_snippet_word_counts_in_band():
    # Design target is 20–50 words; allow a small tolerance band (18–52).
    for ex in FEW_SHOT_EXAMPLES:
        for sig in ex.extraction.signals:
            n = _wc(sig.source_snippet)
            assert 18 <= n <= 52, f"{ex.source_type} snippet has {n} words: {sig.source_snippet!r}"


def test_all_seven_categories_represented():
    cats = {s.risk_category for ex in FEW_SHOT_EXAMPLES for s in ex.extraction.signals}
    assert cats == set(RiskCategory), f"missing categories: {set(RiskCategory) - cats}"


def test_positive_and_negative_polarity_both_present():
    pols = {s.signal_polarity for ex in FEW_SHOT_EXAMPLES for s in ex.extraction.signals}
    assert SignalPolarity.POSITIVE in pols
    assert SignalPolarity.NEGATIVE in pols


def test_no_example_exceeds_signal_cap():
    for ex in FEW_SHOT_EXAMPLES:
        assert len(ex.extraction.signals) <= MAX_SIGNALS_PER_DOC


def test_confidence_scores_in_range():
    for ex in FEW_SHOT_EXAMPLES:
        for s in ex.extraction.signals:
            assert 0.0 <= s.confidence_score <= 1.0


def test_data_dates_are_iso_or_none():
    iso = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
    for ex in FEW_SHOT_EXAMPLES:
        for s in ex.extraction.signals:
            assert s.data_date is None or iso.match(s.data_date), s.data_date


# ── Prompt builders ───────────────────────────────────────────────────────────

def test_extraction_system_prompt_has_key_directives():
    p = EXTRACTION_SYSTEM_PROMPT
    assert "verbatim" in p.lower()
    assert "POSITIVE" in p
    assert "DO NOT EXTRACT" in p
    assert str(MAX_SIGNALS_PER_DOC) in p


def test_build_extraction_prompt_includes_doc_and_entity():
    prompt = build_extraction_prompt("Some doc body about a fine.", "Acme Corp", source_type="NEWS_ARTICLE")
    assert "Acme Corp" in prompt
    assert "Some doc body about a fine." in prompt
    assert "NEWS_ARTICLE" in prompt
    # with examples on by default, all five worked examples are embedded
    assert prompt.count("CORRECT EXTRACTION:") == 5


def test_build_extraction_prompt_can_omit_examples():
    prompt = build_extraction_prompt("body", "X", include_examples=False)
    assert "CORRECT EXTRACTION:" not in prompt
    assert "body" in prompt


def test_severity_prompt_grounds_in_rubric():
    assert "rubric" in SEVERITY_SYSTEM_PROMPT.lower()
    prompt = build_severity_prompt(
        signal_text="Revenue fell 38%.",
        risk_category="FINANCIAL",
        signal_polarity="NEGATIVE",
        rubric_context="HIGH: >30% revenue decline.",
        source_snippet="total revenue declined 38%",
    )
    assert "HIGH: >30% revenue decline." in prompt
    assert "Revenue fell 38%." in prompt
    assert "FINANCIAL" in prompt


# ── Schema sanity ─────────────────────────────────────────────────────────────

def test_extraction_result_validates_empty():
    assert ExtractionResult().signals == []


def test_severity_assessment_requires_fields():
    a = SeverityAssessment(severity=Severity.HIGH, reasoning="x", rubric_reference="y")
    assert a.severity is Severity.HIGH
