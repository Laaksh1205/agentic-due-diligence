"""
Risk knowledge base — FAISS index + RAG retrieval (Tasks 2.6.4, 2.6.5).

Builds a FAISS index over the markdown reference docs in ``knowledge_base/``
(severity rubric, NIST CSF summary, US + international regulatory references), chunked to ~200 words
and embedded with ``all-MiniLM-L6-v2``. ``retrieve_severity_context`` queries the
index with a signal and returns the top-k most relevant chunks so the Risk
Analysis Agent (Task 2.7) can ground its severity judgments.

The embedder is injectable (shared with ``deduplication``) so tests can run with
a deterministic fake and no model download.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from src.analysis.deduplication import Embedder, get_default_embedder

logger = logging.getLogger(__name__)

# knowledge_base/ sits at the project root (two levels up from this file: src/analysis/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
KB_DIR = _PROJECT_ROOT / "knowledge_base"
KB_FILES = (
    "severity_rubric.md",
    "nist_csf_summary.md",
    "regulatory_reference.md",
    "regulatory_reference_intl.md",
)
INDEX_PATH = KB_DIR / "severity.index"
CHUNKS_PATH = KB_DIR / "severity_chunks.json"

CHUNK_TARGET_WORDS = 200

# In-memory cache keyed by index path so repeated retrievals (and tests using
# different paths) don't reload or collide.
_cache: dict[str, tuple] = {}


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_markdown(text: str, *, source: str = "", target_words: int = CHUNK_TARGET_WORDS) -> list[str]:
    """Split markdown into ~target_words chunks, keeping the nearest heading as a
    prefix so each chunk carries its context (e.g. 'CYBERSECURITY').

    Words from each heading section accumulate and are emitted in fixed
    target_words windows, so even a single very long paragraph is split.
    """
    heading = ""
    chunks: list[str] = []
    words: list[str] = []

    def flush() -> None:
        nonlocal words
        prefix = f"[{source}] " if source else ""
        ctx = f"{heading}: " if heading else ""
        for i in range(0, len(words), target_words):
            body = " ".join(words[i : i + target_words]).strip()
            if body:
                chunks.append(f"{prefix}{ctx}{body}")
        words = []

    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            heading = m.group(2).strip()
            continue
        if not line or re.fullmatch(r"-{3,}", line):  # blank line or horizontal rule
            continue
        words.extend(line.split())
    flush()
    return chunks


def load_kb_chunks(doc_paths: Optional[Sequence[Path]] = None) -> list[str]:
    """Read and chunk all knowledge-base documents."""
    paths = [Path(p) for p in (doc_paths or [KB_DIR / f for f in KB_FILES])]
    chunks: list[str] = []
    for path in paths:
        if not path.exists():
            logger.warning("knowledge-base doc missing: %s", path)
            continue
        text = path.read_text(encoding="utf-8")
        chunks.extend(chunk_markdown(text, source=path.stem))
    return chunks


# ── Index build / load ────────────────────────────────────────────────────────

def build_index(
    *,
    embedder: Optional[Embedder] = None,
    doc_paths: Optional[Sequence[Path]] = None,
    index_path: Path = INDEX_PATH,
    chunks_path: Path = CHUNKS_PATH,
) -> int:
    """Build the FAISS index over the knowledge base and write it to disk.

    Returns the number of chunks indexed. Uses inner-product over L2-normalised
    embeddings, i.e. cosine similarity.
    """
    import faiss  # deferred — heavy native dependency

    embedder = embedder or get_default_embedder()
    chunks = load_kb_chunks(doc_paths)
    if not chunks:
        raise RuntimeError(f"No knowledge-base chunks found (looked in {KB_DIR}).")

    emb = np.asarray(embedder(chunks), dtype="float32")
    faiss.normalize_L2(emb)
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_path))
    chunks_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    _cache.pop(str(index_path), None)
    logger.info("Built knowledge-base index: %d chunks -> %s", len(chunks), index_path)
    return len(chunks)


def _load_index(index_path: Path, chunks_path: Path):
    key = str(index_path)
    if key in _cache:
        return _cache[key]
    import faiss
    if not index_path.exists() or not chunks_path.exists():
        logger.info("Knowledge-base index missing — building it now.")
        build_index(index_path=index_path, chunks_path=chunks_path)
    index = faiss.read_index(str(index_path))
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    _cache[key] = (index, chunks)
    return index, chunks


# ── Retrieval (Task 2.6.5) ────────────────────────────────────────────────────

def _signal_query(signal) -> str:
    """Build a retrieval query from a RiskSignal (category + subcategory + text)."""
    cat = getattr(getattr(signal, "risk_category", None), "value", "") or ""
    sub = getattr(signal, "risk_subcategory", "") or ""
    text = getattr(signal, "text", "") or ""
    return " ".join(p for p in (cat, sub, text) if p).strip()


async def retrieve_severity_context(
    signal,
    k: int = 3,
    *,
    embedder: Optional[Embedder] = None,
    index_path: Path = INDEX_PATH,
    chunks_path: Path = CHUNKS_PATH,
) -> list[str]:
    """Return the top-k knowledge-base chunks most relevant to *signal*.

    *signal* is a ``RiskSignal`` (or anything with risk_category / risk_subcategory
    / text). The index is built lazily on first use if absent.
    """
    import asyncio
    import faiss

    query = _signal_query(signal)
    if not query:
        return []

    def _search() -> list[str]:
        index, chunks = _load_index(index_path, chunks_path)
        emb_fn = embedder or get_default_embedder()
        q = np.asarray(emb_fn([query]), dtype="float32")
        faiss.normalize_L2(q)
        _scores, idxs = index.search(q, min(k, len(chunks)))
        return [chunks[i] for i in idxs[0] if i != -1]

    return await asyncio.to_thread(_search)


__all__ = [
    "KB_DIR",
    "KB_FILES",
    "INDEX_PATH",
    "CHUNKS_PATH",
    "chunk_markdown",
    "load_kb_chunks",
    "build_index",
    "retrieve_severity_context",
]


if __name__ == "__main__":  # `python -m src.analysis.knowledge_base` builds the index
    logging.basicConfig(level=logging.INFO)
    n = build_index()
    print(f"Indexed {n} knowledge-base chunks at {INDEX_PATH}")
