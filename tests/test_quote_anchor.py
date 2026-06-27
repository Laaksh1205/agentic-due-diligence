"""
Tests for quote-anchor verification — Task 2.2.3.

Covers: exact match (100), near-verbatim (85+), partial-match FLAG band (70–84),
no-match (<70), empty snippet, empty source, normalization, the verify_anchor
contract, and audit-log persistence of rejections.
"""

import pytest

from src.verification.quote_anchor import (
    FLAG_CONFIDENCE_FACTOR,
    AnchorVerdict,
    adjusted_confidence,
    anchor_score,
    classify_anchor,
    classify_score,
    normalize_text,
    record_anchor_rejection,
    verify_anchor,
)

SOURCE = (
    "Northgate Motors recalled 120,000 vehicles on 14 March 2025 over a faulty brake "
    "sensor that was linked to three reported crashes, the company confirmed in a "
    "statement to regulators."
)


# ── Normalisation ─────────────────────────────────────────────────────────────

def test_normalize_lowercases_strips_punctuation_collapses_ws():
    assert normalize_text("  Hello,   WORLD!!  (test)\n") == "hello world test"


def test_normalize_empty():
    assert normalize_text("") == ""
    assert normalize_text(None) == ""  # type: ignore[arg-type]


# ── Scoring / classification bands ────────────────────────────────────────────

def test_exact_substring_scores_100_and_passes():
    snippet = "recalled 120,000 vehicles on 14 March 2025 over a faulty brake sensor"
    score = anchor_score(snippet, SOURCE)
    assert score == 100.0
    res = classify_anchor(snippet, SOURCE)
    assert res.verdict is AnchorVerdict.PASS
    assert res.confidence_multiplier == 1.0


def test_near_verbatim_with_typo_passes():
    # one transposed/typo'd word in a long verbatim span -> still >= 85
    snippet = "recalled 120,000 vehicles on 14 March 2025 over a falty brake sensor"
    res = classify_anchor(snippet, SOURCE)
    assert res.score >= 85.0
    assert res.verdict is AnchorVerdict.PASS


def test_no_match_rejected():
    snippet = "the company won an environmental sustainability award in Berlin last spring"
    res = classify_anchor(snippet, SOURCE)
    assert res.score < 70.0
    assert res.verdict is AnchorVerdict.REJECT
    assert res.confidence_multiplier == 0.0
    assert res.accepted is False


def test_empty_snippet_rejected():
    res = classify_anchor("", SOURCE)
    assert res.score == 0.0
    assert res.verdict is AnchorVerdict.REJECT


def test_empty_source_rejected():
    res = classify_anchor("some snippet text", "")
    assert res.score == 0.0
    assert res.verdict is AnchorVerdict.REJECT


def test_classify_score_band_boundaries():
    # PASS at/above 85
    assert classify_score(100.0).verdict is AnchorVerdict.PASS
    assert classify_score(85.0).verdict is AnchorVerdict.PASS
    # FLAG in 70..84.999 with halved confidence
    flag_hi = classify_score(84.0)
    flag_lo = classify_score(70.0)
    assert flag_hi.verdict is AnchorVerdict.FLAG
    assert flag_lo.verdict is AnchorVerdict.FLAG
    assert flag_hi.confidence_multiplier == FLAG_CONFIDENCE_FACTOR == 0.5
    # REJECT below 70
    assert classify_score(69.9).verdict is AnchorVerdict.REJECT
    assert classify_score(0.0).verdict is AnchorVerdict.REJECT


def test_verdict_matches_score_band_property():
    # band-consistency across a spread of real snippets of varying fidelity
    for snippet in [
        "recalled 120,000 vehicles on 14 March 2025",          # verbatim
        "recalled 120000 vehicles in March 2025 over brakes",   # close-ish
        "unrelated text about quarterly dividends and buybacks",  # far
        "",
    ]:
        res = classify_anchor(snippet, SOURCE)
        if res.score >= 85.0:
            assert res.verdict is AnchorVerdict.PASS
        elif res.score >= 70.0:
            assert res.verdict is AnchorVerdict.FLAG
        else:
            assert res.verdict is AnchorVerdict.REJECT


# ── verify_anchor contract & confidence adjustment ────────────────────────────

def test_verify_anchor_returns_bool_float_tuple():
    ok, score = verify_anchor("recalled 120,000 vehicles on 14 March 2025", SOURCE)
    assert ok is True and isinstance(score, float) and score == 100.0
    ok2, score2 = verify_anchor("completely unrelated sentence here", SOURCE)
    assert ok2 is False and score2 < 70.0


def test_adjusted_confidence_applies_multiplier():
    passed = classify_score(95.0)
    flagged = classify_score(75.0)
    rejected = classify_score(40.0)
    assert adjusted_confidence(0.9, passed) == pytest.approx(0.9)
    assert adjusted_confidence(0.9, flagged) == pytest.approx(0.45)
    assert adjusted_confidence(0.9, rejected) == pytest.approx(0.0)


# ── Rejection logging (Task 2.2.2) ────────────────────────────────────────────

async def test_record_anchor_rejection_writes_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "audit.db"))
    from src.storage.database import get_audit_log, init_db
    await init_db()

    await record_anchor_rejection(
        signal_text="Company fined $10M by regulator.",
        snippet="a fine that does not appear in the source",
        source_url="https://example.com/article",
        score=42.0,
        run_id="run-123",
    )

    rows = await get_audit_log(run_id="run-123")
    assert any(r["event"] == "anchor_rejected" for r in rows)
    detail = next(r["detail"] for r in rows if r["event"] == "anchor_rejected")
    assert "example.com" in detail and "42" in detail


async def test_record_anchor_rejection_never_raises(monkeypatch):
    # Even if the DB layer blows up, logging must not propagate.
    async def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("src.storage.database.audit", _boom)
    # should complete without raising
    await record_anchor_rejection(
        signal_text="x", snippet="y", source_url=None, score=10.0,
    )
