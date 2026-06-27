"""
Tests for signal deduplication — Task 2.5.3.

Unit tests use a deterministic dict-backed embedder (no model download, no
network — immune to the Gemini/GCP incident). One opt-in integration test
exercises the real all-MiniLM-L6-v2 model.
"""

import numpy as np
import pytest

from src.analysis.deduplication import deduplicate
from src.models.signals import (
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
    SourceType,
)


def _sig(text, *, cat=RiskCategory.LEGAL, conf=0.9, url="https://x/a"):
    return RiskSignal(
        text=text,
        source_url=url,
        source_type=SourceType.NEWS_ARTICLE,
        source_snippet="snippet that anchors the signal text here for the record",
        confidence_score=conf,
        risk_category=cat,
        severity=Severity.HIGH,
        signal_polarity=SignalPolarity.NEGATIVE,
        entity_name="Acme Corp",
    )


class _DictEmbedder:
    """Maps each text to a caller-supplied vector (need not be normalised)."""

    def __init__(self, mapping):
        self.mapping = mapping

    def __call__(self, texts):
        return np.array([self.mapping[t] for t in texts], dtype=float)


def _onehot(i, dim=8):
    v = np.zeros(dim)
    v[i] = 1.0
    return v


# ── Core behaviour ────────────────────────────────────────────────────────────

def test_empty_returns_empty():
    assert deduplicate([], embedder=_DictEmbedder({})) == []


def test_single_signal_unchanged():
    s = _sig("only one")
    out = deduplicate([s], embedder=_DictEmbedder({"only one": _onehot(0)}))
    assert out == [s]
    assert out[0].is_corroborated is False
    assert out[0].corroborating_signals == []


def test_clear_duplicates_merge_into_one_primary():
    # 5 signals, identical topic & category, different confidences.
    texts = [f"SEC opened an investigation into Acme #{i}" for i in range(5)]
    confs = [0.6, 0.95, 0.7, 0.8, 0.5]
    sigs = [_sig(t, conf=c, url=f"https://x/{i}") for i, (t, c) in enumerate(zip(texts, confs))]
    emb = _DictEmbedder({t: _onehot(0) for t in texts})  # cosine 1.0 between every pair

    out = deduplicate(sigs, embedder=emb)
    assert len(out) == 1
    primary = out[0]
    assert primary.confidence_score == 0.95            # highest-confidence kept
    assert primary.is_corroborated is True
    assert len(primary.corroborating_signals) == 4     # other 4 linked


def test_cross_category_not_merged_even_if_similar():
    t1, t2 = "lawsuit about the data incident A", "lawsuit about the data incident B"
    s1 = _sig(t1, cat=RiskCategory.LEGAL)
    s2 = _sig(t2, cat=RiskCategory.REPUTATIONAL)
    emb = _DictEmbedder({t1: _onehot(0), t2: _onehot(0)})  # cosine 1.0
    out = deduplicate([s1, s2], embedder=emb)
    assert len(out) == 2
    assert all(not s.is_corroborated for s in out)


def test_distinct_topics_not_merged():
    t1, t2, t3 = "revenue decline", "data breach", "factory fire"
    sigs = [_sig(t1), _sig(t2), _sig(t3)]
    emb = _DictEmbedder({t1: _onehot(0), t2: _onehot(1), t3: _onehot(2)})
    out = deduplicate(sigs, embedder=emb)
    assert len(out) == 3


def test_threshold_boundary_merges_at_085_not_below():
    def vecs(dot):
        return np.array([1.0, 0.0]), np.array([dot, np.sqrt(1 - dot ** 2)])

    a, b = "alpha", "beta"
    v1, v2 = vecs(0.85)
    out = deduplicate([_sig(a, conf=0.9), _sig(b, conf=0.5)],
                      embedder=_DictEmbedder({a: v1, b: v2}))
    assert len(out) == 1

    v1, v2 = vecs(0.84)
    out = deduplicate([_sig(a, conf=0.9), _sig(b, conf=0.5)],
                      embedder=_DictEmbedder({a: v1, b: v2}))
    assert len(out) == 2


def test_inputs_not_mutated():
    t = "shared event"
    s1, s2 = _sig(t, conf=0.9), _sig(t, conf=0.4)
    deduplicate([s1, s2], embedder=_DictEmbedder({t: _onehot(0)}))
    assert s1.is_corroborated is False
    assert s1.corroborating_signals == []


def test_result_order_follows_first_occurrence():
    a, b, c = "topic a", "topic b dup", "topic b dup2"
    sigs = [_sig(a), _sig(b), _sig(c)]
    emb = _DictEmbedder({a: _onehot(0), b: _onehot(1), c: _onehot(1)})
    out = deduplicate(sigs, embedder=emb)
    assert len(out) == 2
    assert out[0].text == "topic a"      # cluster {a} occurs first
    assert out[1].text in (b, c)         # cluster {b,c} second


# ── Real model (opt-in; downloads ~80MB on first run) ─────────────────────────

@pytest.mark.integration
def test_real_embedder_clusters_paraphrases():
    # Three real paraphrases of one SEC event (pairwise cosine ~0.83–0.89, so they
    # cluster transitively at the 0.85 threshold) plus an unrelated ESG positive.
    sigs = [
        _sig("The SEC opened an investigation into the company's accounting.", conf=0.8),
        _sig("Company faces an SEC probe over its accounting practices.", conf=0.9),
        _sig("The SEC launched a probe into the company's accounting.", conf=0.7),
        _sig("The company won a national clean-energy innovation award.", conf=0.7,
             cat=RiskCategory.ESG),
    ]
    out = deduplicate(sigs)  # default real all-MiniLM-L6-v2
    assert len(out) == 2                                  # SEC cluster + award
    corroborated = [s for s in out if s.is_corroborated]
    assert len(corroborated) == 1
    assert corroborated[0].confidence_score == 0.9        # highest-conf SEC signal
    assert len(corroborated[0].corroborating_signals) == 2
