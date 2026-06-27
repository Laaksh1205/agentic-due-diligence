"""
Risk Analysis Agent — Task 2.7.

LangGraph node that turns raw extracted signals into scored, deduplicated,
review-flagged signals:

  1. deduplicate near-duplicate signals (Task 2.5), folding corroboration in;
  2. boost confidence on corroborated signals (+0.1, capped at 1.0) — 2.7.2;
  3. for each signal, retrieve severity context via RAG (Task 2.6.5) and call the
     LLM with the severity-scoring prompt (Task 2.1.5) to re-grade severity,
     grounded in the rubric — 2.7.1;
  4. flag ``requires_human_review`` when severity is CRITICAL or confidence < 0.6;
  5. warn if the CRITICAL share exceeds 10% (severity inflation) — 2.7.3;
  6. detect contradictory signal pairs and flag both ``is_contradictory`` — 2.7.4.

All LLM calls are tracked toward ``llm_call_count`` / ``total_cost`` and bounded
by the ``max_llm_calls`` guardrail (2.7.5), reserving headroom for synthesis. The
node is wired into the supervisor graph (Task 3.5.1).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.analysis.deduplication import Embedder, deduplicate, get_default_embedder
from src.analysis.knowledge_base import retrieve_severity_context
from src.analysis.source_credibility import (
    apply_credibility_adjustment,
    apply_credibility_gate,
)
from src.config import settings
from src.llm.base import LLMProvider
from src.llm.gemini import GeminiProvider
from src.llm.prompts import (
    CONTRADICTION_SYSTEM_PROMPT,
    SEVERITY_SYSTEM_PROMPT,
    ContradictionAssessment,
    SeverityAssessment,
    build_contradiction_prompt,
    build_severity_prompt,
)
from src.models.signals import RiskSignal, Severity, SignalPolarity

logger = logging.getLogger(__name__)

_MAX_CONCURRENCY = 5
HUMAN_REVIEW_CONFIDENCE_FLOOR = 0.6
CRITICAL_INFLATION_THRESHOLD = 0.10  # >10% CRITICAL → warn
MAX_CONTRADICTION_PAIRS = 5          # cap LLM calls spent on contradiction checks
_CONTRADICTION_SIM_THRESHOLD = 0.5   # candidate-pair topical-similarity floor

# Normalised severity weight for "effective severity" (× temporal_weight).
_SEVERITY_WEIGHT = {
    Severity.CRITICAL: 1.0,
    Severity.HIGH: 0.8,
    Severity.MEDIUM: 0.6,
    Severity.LOW: 0.4,
    Severity.INFO: 0.2,
}


# ── Pure helpers (no LLM) ─────────────────────────────────────────────────────

def needs_human_review(signal: RiskSignal) -> bool:
    """CRITICAL severity or low confidence → human review (Task 2.7.1 step 6)."""
    return signal.severity is Severity.CRITICAL or signal.confidence_score < HUMAN_REVIEW_CONFIDENCE_FLOOR


def effective_severity_score(signal: RiskSignal) -> float:
    """Severity weight × temporal_weight × source_credibility (decayed, trust-weighted)."""
    return (
        _SEVERITY_WEIGHT.get(signal.severity, 0.2)
        * (signal.temporal_weight or 1.0)
        * (signal.source_credibility or 1.0)
    )


def critical_inflation(signals: list[RiskSignal]) -> bool:
    """True if CRITICAL signals exceed the inflation threshold (Task 2.7.3)."""
    if not signals:
        return False
    crit = sum(1 for s in signals if s.severity is Severity.CRITICAL)
    return crit / len(signals) > CRITICAL_INFLATION_THRESHOLD


def candidate_contradiction_pairs(
    signals: list[RiskSignal], *, embedder: Optional[Embedder] = None
) -> list[tuple[int, int]]:
    """Find signal pairs worth an LLM contradiction check (Task 2.7.4).

    Candidates share the same entity and are either opposite-polarity within the
    same category, or topically similar (cosine >= threshold). Bounded to the
    most-similar ``MAX_CONTRADICTION_PAIRS`` pairs to cap LLM spend.
    """
    import numpy as np

    n = len(signals)
    if n < 2:
        return []
    embedder = embedder or get_default_embedder()
    emb = np.asarray(embedder([s.text for s in signals]), dtype=float)
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    emb = emb / norms
    sims = emb @ emb.T

    scored: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = signals[i], signals[j]
            if a.entity_name != b.entity_name:
                continue
            opposite_polarity = (
                a.risk_category == b.risk_category
                and {a.signal_polarity, b.signal_polarity} == {SignalPolarity.POSITIVE, SignalPolarity.NEGATIVE}
            )
            similar = sims[i, j] >= _CONTRADICTION_SIM_THRESHOLD
            if opposite_polarity or similar:
                scored.append((float(sims[i, j]), i, j))
    scored.sort(reverse=True)
    return [(i, j) for _, i, j in scored[:MAX_CONTRADICTION_PAIRS]]


# ── LLM steps ─────────────────────────────────────────────────────────────────

async def score_one_signal(
    signal: RiskSignal, provider: LLMProvider, *, embedder: Optional[Embedder] = None
) -> RiskSignal:
    """Re-grade one signal's severity against retrieved rubric context."""
    context = await retrieve_severity_context(signal, k=3, embedder=embedder)
    prompt = build_severity_prompt(
        signal.text,
        signal.risk_category.value,
        signal.signal_polarity.value,
        "\n\n".join(context) if context else "(no rubric context retrieved)",
        source_snippet=signal.source_snippet,
    )
    assessment: SeverityAssessment = await provider.complete(
        prompt, SeverityAssessment, system=SEVERITY_SYSTEM_PROMPT, use_fast=False,
    )
    updated = signal.model_copy(update={"severity": assessment.severity})
    return updated.model_copy(update={"requires_human_review": needs_human_review(updated)})


# ── LangGraph node ────────────────────────────────────────────────────────────

async def risk_analysis_agent_node(
    state: dict, *, provider: Optional[LLMProvider] = None, embedder: Optional[Embedder] = None
) -> dict:
    """Deduplicate, score, and contradiction-check the raw signals in state."""
    raw: list[RiskSignal] = list(state.get("raw_signals") or [])
    if not raw:
        logger.info("[risk_analysis] no signals to analyse")
        return {"scored_signals": []}

    # 1–2. Dedup + credibility-aware confidence adjustment (no LLM).
    deduped = [apply_credibility_adjustment(s) for s in deduplicate(raw, embedder=embedder)]

    prior_calls = state.get("llm_call_count", 0)
    provider = provider or GeminiProvider()
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    errors: list[str] = []

    # 3–4. Severity scoring (1 LLM call per signal), bounded by max_llm_calls.
    # Reserve headroom for synthesis so per-signal scoring can't consume the
    # whole budget and leave the final report empty (the deliverable wins).
    budget = max(0, settings.max_llm_calls - prior_calls - settings.synthesis_call_reserve)
    to_score = deduped[:budget]
    unscored = deduped[budget:]
    if unscored:
        logger.warning(
            "[risk_analysis] llm-call budget scores only %d/%d signals",
            len(to_score), len(deduped),
        )

    async def _score(sig: RiskSignal):
        async with semaphore:
            try:
                return await score_one_signal(sig, provider, embedder=embedder), None
            except Exception as exc:  # isolate; keep the un-rescored signal
                logger.warning("[risk_analysis] severity scoring failed: %s", exc)
                return sig.model_copy(update={"requires_human_review": needs_human_review(sig)}), \
                    f"risk_analysis: severity: {exc}"

    scored_pairs = await asyncio.gather(*[_score(s) for s in to_score])
    scored = [s for s, _ in scored_pairs]
    errors.extend(e for _, e in scored_pairs if e)
    # Unscored signals still get a review flag based on their existing severity.
    scored.extend(s.model_copy(update={"requires_human_review": needs_human_review(s)}) for s in unscored)

    # 4b. Credibility gate (cap + flag): a HIGH/CRITICAL finding resting on a single
    # low-trust, uncorroborated source is capped to MEDIUM, marked is_unverified, and
    # routed to human review — never dropped (recall preserved).
    gated = [apply_credibility_gate(s) for s in scored]
    capped = sum(1 for before, after in zip(scored, gated) if before.severity is not after.severity)
    if capped:
        logger.info("[risk_analysis] credibility gate capped %d low-trust signal(s) to MEDIUM", capped)
    scored = gated

    # 5. Severity-inflation check (Task 2.7.3).
    if critical_inflation(scored):
        logger.warning(
            "[risk_analysis] Severity inflation detected (%d/%d CRITICAL) — consider rubric calibration.",
            sum(1 for s in scored if s.severity is Severity.CRITICAL), len(scored),
        )

    # 6. Contradiction detection (Task 2.7.4), within remaining budget
    # (still reserving the synthesis headroom).
    remaining = max(
        0,
        settings.max_llm_calls - (prior_calls + provider.call_count) - settings.synthesis_call_reserve,
    )
    if remaining > 0 and len(scored) >= 2:
        pairs = candidate_contradiction_pairs(scored, embedder=embedder)[:remaining]
        contradictory_idx: set[int] = set()
        for i, j in pairs:
            try:
                prompt = build_contradiction_prompt(
                    scored[i].text, scored[j].text,
                    source_a=scored[i].source_url, source_b=scored[j].source_url,
                )
                verdict: ContradictionAssessment = await provider.complete(
                    prompt, ContradictionAssessment,
                    system=CONTRADICTION_SYSTEM_PROMPT, use_fast=True,
                )
                if verdict.is_contradictory:
                    contradictory_idx.update((i, j))
            except Exception as exc:
                errors.append(f"risk_analysis: contradiction: {exc}")
        if contradictory_idx:
            logger.info("[risk_analysis] flagged %d contradictory signal(s)", len(contradictory_idx))
            scored = [
                s.model_copy(update={"is_contradictory": True}) if idx in contradictory_idx else s
                for idx, s in enumerate(scored)
            ]

    logger.info(
        "[risk_analysis] %d raw → %d scored (%d need review)",
        len(raw), len(scored), sum(1 for s in scored if s.requires_human_review),
    )

    return {
        "scored_signals": scored,
        "llm_call_count": prior_calls + provider.call_count,
        "total_cost": state.get("total_cost", 0.0) + provider.total_cost_usd,
        "errors": list(state.get("errors") or []) + errors,
    }


__all__ = [
    "apply_credibility_adjustment",
    "apply_credibility_gate",
    "needs_human_review",
    "effective_severity_score",
    "critical_inflation",
    "candidate_contradiction_pairs",
    "score_one_signal",
    "risk_analysis_agent_node",
]
