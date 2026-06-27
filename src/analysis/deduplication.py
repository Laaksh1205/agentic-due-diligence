"""
Signal deduplication — Task 2.5 (design doc Section 9, "multi-source dedup").

The same risk event (e.g. an SEC investigation) is often reported by several
sources with different wording, producing near-duplicate ``RiskSignal`` objects.
This module clusters semantically-similar signals and folds each cluster into a
single primary signal that links the rest as corroboration:

  1. embed each signal's text with ``all-MiniLM-L6-v2`` (local, free),
  2. compute pairwise cosine similarity,
  3. connect signals with similarity >= threshold (default 0.85) **only when they
     share the same risk_category** (a LEGAL and a REPUTATIONAL signal about one
     lawsuit are both valid and must not be merged — Task 2.5.2),
  4. per connected cluster: keep the highest-``confidence_score`` signal as the
     primary, set ``is_corroborated=True`` and list the others' ids in
     ``corroborating_signals``.

The embedder is injectable so callers/tests can supply a deterministic fake; the
default lazily loads the sentence-transformers model on first use.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Optional, Sequence

import numpy as np

from src.models.signals import RiskSignal

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.85
EMBED_MODEL = "all-MiniLM-L6-v2"

# An embedder maps a list of texts to a 2-D float array (n_texts, dim).
Embedder = Callable[[Sequence[str]], np.ndarray]

_model = None  # lazy singleton


def get_default_embedder() -> Embedder:
    """Return an embedder backed by ``all-MiniLM-L6-v2`` (loaded lazily once)."""
    def _embed(texts: Sequence[str]) -> np.ndarray:
        global _model
        if _model is None:
            from sentence_transformers import SentenceTransformer  # heavy import, deferred
            logger.info("Loading embedding model %s ...", EMBED_MODEL)
            _model = SentenceTransformer(EMBED_MODEL)
        return np.asarray(_model.encode(list(texts), normalize_embeddings=True), dtype=float)

    return _embed


# ── Union-find (connected components) ─────────────────────────────────────────

class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


# ── Public API ────────────────────────────────────────────────────────────────

def deduplicate(
    signals: Sequence[RiskSignal],
    *,
    threshold: float = SIMILARITY_THRESHOLD,
    embedder: Optional[Embedder] = None,
) -> list[RiskSignal]:
    """Cluster near-duplicate signals and return one primary per cluster.

    Inputs are never mutated — primaries are returned as copies with
    ``is_corroborated`` / ``corroborating_signals`` populated. Result order
    follows the first occurrence of each cluster in *signals*.
    """
    signals = list(signals)
    n = len(signals)
    if n <= 1:
        return signals

    embedder = embedder or get_default_embedder()
    emb = np.asarray(embedder([s.text for s in signals]), dtype=float)
    # Normalise so the dot product is cosine similarity (robust to un-normalised embedders).
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    emb = emb / norms
    sims = emb @ emb.T

    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            # Same-category constraint (Task 2.5.2) before similarity.
            if signals[i].risk_category != signals[j].risk_category:
                continue
            if sims[i, j] >= threshold:
                uf.union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[uf.find(i)].append(i)

    result: list[RiskSignal] = []
    for idxs in clusters.values():
        # Highest confidence is primary; ties resolved by original order (stable).
        primary_idx = max(idxs, key=lambda i: (signals[i].confidence_score, -i))
        others = [signals[i] for i in idxs if i != primary_idx]
        primary = signals[primary_idx]
        if others:
            primary = primary.model_copy(update={
                "is_corroborated": True,
                "corroborating_signals": [o.id for o in others],
            })
        result.append((min(idxs), primary))

    result.sort(key=lambda pair: pair[0])
    merged = sum(1 for _, s in result if s.is_corroborated)
    if merged:
        logger.info(
            "[dedup] %d signals -> %d clusters (%d corroborated)",
            n, len(result), merged,
        )
    return [s for _, s in result]


__all__ = [
    "SIMILARITY_THRESHOLD",
    "EMBED_MODEL",
    "Embedder",
    "get_default_embedder",
    "deduplicate",
]
