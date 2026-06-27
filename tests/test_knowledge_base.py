"""
Tests for the risk knowledge base — Tasks 2.6.4 / 2.6.5.

Unit tests use a deterministic keyword-bag embedder and a tmp index (no model
download, no network). One opt-in integration test builds the real index over
the shipped knowledge_base/*.md and retrieves with all-MiniLM-L6-v2.
"""

from pathlib import Path

import numpy as np
import pytest

from src.analysis import knowledge_base as kb
from src.models.signals import (
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
    SourceType,
)


def _sig(text, *, cat=RiskCategory.CYBERSECURITY, sub=""):
    return RiskSignal(
        text=text, source_url="https://x/a", source_type=SourceType.NEWS_ARTICLE,
        source_snippet="snippet anchoring the signal text for the record here",
        confidence_score=0.9, risk_category=cat, risk_subcategory=sub,
        severity=Severity.HIGH, signal_polarity=SignalPolarity.NEGATIVE,
        entity_name="Acme Corp",
    )


_VOCAB = ["breach", "data", "sanctions", "ofac", "revenue", "fine", "gdpr", "supplier"]


class _BagEmbedder:
    """Bag-of-keywords embedder: vector = normalized keyword counts over _VOCAB."""

    def __call__(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            v = np.array([low.count(w) for w in _VOCAB], dtype="float32")
            if v.sum() == 0:
                v[0] = 1e-3  # avoid all-zero rows
            out.append(v)
        return np.array(out, dtype="float32")


# ── Chunking ──────────────────────────────────────────────────────────────────

def test_chunk_markdown_keeps_heading_context():
    md = "# Title\n\n## CYBERSECURITY\n\nA confirmed data breach exposed records.\n"
    chunks = kb.chunk_markdown(md, source="severity_rubric")
    assert len(chunks) == 1
    assert "CYBERSECURITY" in chunks[0]
    assert "[severity_rubric]" in chunks[0]
    assert "data breach" in chunks[0]


def test_chunk_markdown_splits_long_text():
    body = " ".join(["word"] * 650)
    chunks = kb.chunk_markdown(f"## H\n\n{body}", target_words=200)
    assert len(chunks) >= 3


# ── Build + retrieve (fake embedder, tmp index) ───────────────────────────────

@pytest.fixture
def tmp_kb(tmp_path):
    (tmp_path / "rubric.md").write_text(
        "## CYBERSECURITY\n\nA confirmed data breach exposing records is severe.\n\n"
        "## FINANCIAL\n\nA large revenue decline signals financial distress.\n",
        encoding="utf-8",
    )
    (tmp_path / "reg.md").write_text(
        "## SANCTIONS\n\nOFAC sanctions list inclusion is critical.\n\n"
        "## GDPR\n\nA GDPR fine follows a personal data breach.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_build_index_writes_files(tmp_kb):
    index_path = tmp_kb / "k.index"
    chunks_path = tmp_kb / "k.json"
    n = kb.build_index(
        embedder=_BagEmbedder(),
        doc_paths=[tmp_kb / "rubric.md", tmp_kb / "reg.md"],
        index_path=index_path, chunks_path=chunks_path,
    )
    assert n == 4
    assert index_path.exists() and chunks_path.exists()


async def test_retrieve_returns_relevant_chunk(tmp_kb):
    index_path = tmp_kb / "k.index"
    chunks_path = tmp_kb / "k.json"
    kb.build_index(
        embedder=_BagEmbedder(),
        doc_paths=[tmp_kb / "rubric.md", tmp_kb / "reg.md"],
        index_path=index_path, chunks_path=chunks_path,
    )
    out = await kb.retrieve_severity_context(
        _sig("the company suffered a data breach"), k=1,
        embedder=_BagEmbedder(), index_path=index_path, chunks_path=chunks_path,
    )
    assert len(out) == 1
    assert "breach" in out[0].lower()


async def test_retrieve_sanctions_signal_hits_sanctions_chunk(tmp_kb):
    index_path = tmp_kb / "k.index"
    chunks_path = tmp_kb / "k.json"
    kb.build_index(
        embedder=_BagEmbedder(),
        doc_paths=[tmp_kb / "rubric.md", tmp_kb / "reg.md"],
        index_path=index_path, chunks_path=chunks_path,
    )
    out = await kb.retrieve_severity_context(
        _sig("entity added to the OFAC sanctions list", cat=RiskCategory.REGULATORY), k=1,
        embedder=_BagEmbedder(), index_path=index_path, chunks_path=chunks_path,
    )
    assert "ofac" in out[0].lower() or "sanctions" in out[0].lower()


async def test_retrieve_empty_query_returns_empty(tmp_kb):
    index_path = tmp_kb / "k.index"
    chunks_path = tmp_kb / "k.json"
    kb.build_index(embedder=_BagEmbedder(),
                   doc_paths=[tmp_kb / "rubric.md"],
                   index_path=index_path, chunks_path=chunks_path)

    class _Bare:
        risk_category = None
        risk_subcategory = ""
        text = ""

    out = await kb.retrieve_severity_context(
        _Bare(), embedder=_BagEmbedder(), index_path=index_path, chunks_path=chunks_path)
    assert out == []


# ── Shipped knowledge base sanity ─────────────────────────────────────────────

def test_shipped_kb_files_exist_and_chunk():
    chunks = kb.load_kb_chunks()
    assert len(chunks) >= 15  # three substantive docs
    joined = " ".join(chunks).lower()
    for term in ("critical", "cybersecurity", "ofac", "gdpr", "nist"):
        assert term in joined


# ── Real model over the real KB (opt-in) ──────────────────────────────────────

@pytest.mark.integration
def test_real_index_retrieves_cyber_context(tmp_path):
    import asyncio
    index_path = tmp_path / "real.index"
    chunks_path = tmp_path / "real.json"
    n = kb.build_index(index_path=index_path, chunks_path=chunks_path)  # real embedder, real KB
    assert n >= 15

    out = asyncio.run(kb.retrieve_severity_context(
        _sig("the company disclosed a data breach exposing 2 million customer records"),
        k=3, index_path=index_path, chunks_path=chunks_path,
    ))
    assert out
    assert any("breach" in c.lower() or "cyber" in c.lower() for c in out)
