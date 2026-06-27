"""
Quote-Anchor Verification — Task 2.2 (design doc Section 8b).

Every extracted risk signal must carry a `source_snippet` — a verbatim excerpt
from the source document. This module checks that the snippet really appears in
the source, catching paraphrased / hallucinated quote anchors before a signal
reaches the report.

Algorithm (design doc 8b):
  1. Normalize both snippet and source (lowercase, strip punctuation, collapse
     whitespace).
  2. Score with ``rapidfuzz.fuzz.partial_ratio`` (0–100) — partial_ratio finds
     the best-matching span of the (longer) source for the (shorter) snippet, so
     a verbatim snippet scores 100 regardless of surrounding text.
  3. Classify by threshold:
         score >= 85   -> PASS    (keep signal, confidence unchanged)
         70 <= score < 85 -> FLAG (keep signal, confidence *= 0.5)
         score < 70    -> REJECT  (drop signal; log it)

The Extraction Agent (Task 2.3) calls :func:`classify_anchor` per signal, applies
:func:`adjusted_confidence`, and persists rejections via
:func:`record_anchor_rejection`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# ── Thresholds (design doc 8b) ────────────────────────────────────────────────
PASS_THRESHOLD = 85.0
FLAG_THRESHOLD = 70.0
FLAG_CONFIDENCE_FACTOR = 0.5

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")


class AnchorVerdict(str, Enum):
    PASS = "PASS"
    FLAG = "FLAG"
    REJECT = "REJECT"


@dataclass(frozen=True)
class AnchorResult:
    verdict: AnchorVerdict
    score: float                 # 0–100 partial_ratio
    confidence_multiplier: float  # PASS=1.0, FLAG=0.5, REJECT=0.0

    @property
    def accepted(self) -> bool:
        """True if the signal should be kept (PASS or FLAG)."""
        return self.verdict is not AnchorVerdict.REJECT


# ── Normalisation & scoring ───────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    no_punct = _PUNCT_RE.sub(" ", text.lower())
    return _WS_RE.sub(" ", no_punct).strip()


def anchor_score(snippet: str, source_text: str) -> float:
    """Return the fuzzy partial-ratio (0–100) of *snippet* within *source_text*.

    Empty snippet or empty source scores 0.0 (cannot be verified -> REJECT).
    """
    ns = normalize_text(snippet)
    nsrc = normalize_text(source_text)
    if not ns or not nsrc:
        return 0.0
    return float(fuzz.partial_ratio(ns, nsrc))


# ── Classification ────────────────────────────────────────────────────────────

def classify_score(score: float) -> AnchorResult:
    """Map a raw 0–100 score to PASS / FLAG / REJECT with a confidence multiplier."""
    if score >= PASS_THRESHOLD:
        return AnchorResult(AnchorVerdict.PASS, score, 1.0)
    if score >= FLAG_THRESHOLD:
        return AnchorResult(AnchorVerdict.FLAG, score, FLAG_CONFIDENCE_FACTOR)
    return AnchorResult(AnchorVerdict.REJECT, score, 0.0)


def classify_anchor(snippet: str, source_text: str) -> AnchorResult:
    """Score *snippet* against *source_text* and classify the result."""
    return classify_score(anchor_score(snippet, source_text))


def verify_anchor(snippet: str, source_text: str) -> tuple[bool, float]:
    """Plan contract (Task 2.2.1): ``(accepted, score)``.

    ``accepted`` is True for PASS or FLAG, False for REJECT. For the full
    verdict and confidence multiplier use :func:`classify_anchor`.
    """
    result = classify_anchor(snippet, source_text)
    return result.accepted, result.score


def adjusted_confidence(base_confidence: float, result: AnchorResult) -> float:
    """Apply the verdict's multiplier to a signal's base confidence.

    PASS -> unchanged, FLAG -> halved, REJECT -> 0.0.
    """
    return base_confidence * result.confidence_multiplier


# ── Rejection logging (Task 2.2.2) ────────────────────────────────────────────

async def record_anchor_rejection(
    *,
    signal_text: str,
    snippet: str,
    source_url: Optional[str],
    score: float,
    run_id: Optional[str] = None,
) -> None:
    """Log a rejected (or flagged) quote anchor to the console and the audit_log.

    Always emits a WARNING (visible in --verbose) and best-effort persists a row
    to the SQLite ``audit_log`` table so rejections are auditable for prompt
    debugging. Never raises — logging failures must not break extraction.
    """
    logger.warning(
        "Quote-anchor REJECT (score=%.1f) url=%s snippet=%r",
        score, source_url or "?", snippet[:80],
    )
    detail = json.dumps({
        "score": round(score, 1),
        "source_url": source_url,
        "signal_text": signal_text[:300],
        "snippet": snippet[:300],
    }, ensure_ascii=False)
    try:
        from src.storage.database import audit
        await audit("anchor_rejected", detail, run_id=run_id)
    except Exception as exc:  # pragma: no cover - logging must never break callers
        logger.debug("Could not persist anchor rejection to audit_log: %s", exc)


__all__ = [
    "PASS_THRESHOLD",
    "FLAG_THRESHOLD",
    "FLAG_CONFIDENCE_FACTOR",
    "AnchorVerdict",
    "AnchorResult",
    "normalize_text",
    "anchor_score",
    "classify_score",
    "classify_anchor",
    "verify_anchor",
    "adjusted_confidence",
    "record_anchor_rejection",
]
