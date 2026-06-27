"""
Extraction Agent — Task 2.3.

LangGraph node that turns the research agent's raw documents into verified
``RiskSignal`` objects. Per document it:

  1. calls the LLM with the extraction prompt (Task 2.1) + document text,
  2. parses the structured ``ExtractionResult`` (Pydantic),
  3. truncates to ``max_signals_per_doc`` (guardrail, design doc 8e),
  4. quote-anchor verifies each signal against the source (Task 2.2) —
     REJECT drops it (logged), FLAG keeps it with confidence halved,
  5. computes ``temporal_weight`` from ``data_date``,
  6. maps surviving ``ExtractedSignal`` → ``RiskSignal`` (filling source_url /
     source_type from the document).

Documents are processed concurrently with a semaphore (max 5 in flight). The
``max_llm_calls`` guardrail is honoured before dispatch. A document whose LLM
call fails is isolated — it never crashes the batch.

This node function is wired into the supervisor graph (Task 3.5.1). Temporal
decay is computed by ``src/analysis/temporal.calculate_temporal_weight`` (Task 2.4).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from src.analysis.source_credibility import classify_credibility
from src.analysis.temporal import calculate_temporal_weight
from src.config import settings
from src.llm.base import LLMProvider
from src.llm.gemini import GeminiProvider
from src.llm.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    ExtractedSignal,
    ExtractionResult,
    build_extraction_prompt,
)
from src.models.documents import RawDocument
from src.models.entities import Entity
from src.models.signals import RiskSignal, Severity
from src.verification.quote_anchor import (
    AnchorVerdict,
    adjusted_confidence,
    classify_anchor,
    record_anchor_rejection,
)

logger = logging.getLogger(__name__)

_MAX_CONCURRENCY = 5
_SEVERITY_RANK = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


# ── Data-date parsing (temporal weight lives in src/analysis/temporal.py) ──────

def _parse_data_date(raw: Optional[str]) -> Optional[date]:
    """Parse 'YYYY' / 'YYYY-MM' / 'YYYY-MM-DD' into a date; missing parts -> 1."""
    if not raw:
        return None
    parts = raw.strip().split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


# ── Mapping & verification ────────────────────────────────────────────────────

@dataclass
class DocExtractionStats:
    extracted: int = 0   # signals the LLM returned (pre-truncation)
    kept: int = 0
    rejected: int = 0    # failed quote-anchor verification
    flagged: int = 0     # FLAG band — kept with halved confidence


def _to_risk_signal(
    es: ExtractedSignal, doc: RawDocument, entity_name: str, confidence: float
) -> RiskSignal:
    parsed_date = _parse_data_date(es.data_date)
    tier, weight = classify_credibility(doc.source_url, doc.source_type)
    return RiskSignal(
        text=es.text,
        source_url=doc.source_url,
        source_type=doc.source_type,
        source_snippet=es.source_snippet,
        data_date=parsed_date,
        confidence_score=max(0.0, min(1.0, confidence)),
        temporal_weight=calculate_temporal_weight(parsed_date),
        source_credibility=weight,
        credibility_tier=tier.value,
        risk_category=es.risk_category,
        risk_subcategory=es.risk_subcategory,
        severity=es.severity,
        signal_polarity=es.signal_polarity,
        entity_name=entity_name,
        related_entities=[
            Entity(name=e.name, entity_type=e.entity_type, role=e.role or "")
            for e in es.related_entities
        ],
    )


async def verify_and_build(
    doc: RawDocument,
    result: ExtractionResult,
    entity_name: str,
    *,
    run_id: Optional[str] = None,
) -> tuple[list[RiskSignal], DocExtractionStats]:
    """Truncate, quote-anchor verify, and map one document's extraction result."""
    signals = list(result.signals)

    # Guardrail 8e: cap signals per document, keeping the most material.
    if len(signals) > settings.max_signals_per_doc:
        signals.sort(
            key=lambda s: (_SEVERITY_RANK.get(s.severity, 0), s.confidence_score),
            reverse=True,
        )
        signals = signals[: settings.max_signals_per_doc]

    kept: list[RiskSignal] = []
    stats = DocExtractionStats(extracted=len(result.signals))
    source_text = doc.raw_text or ""

    for es in signals:
        anchor = classify_anchor(es.source_snippet, source_text)
        if anchor.verdict is AnchorVerdict.REJECT:
            stats.rejected += 1
            await record_anchor_rejection(
                signal_text=es.text,
                snippet=es.source_snippet,
                source_url=doc.source_url,
                score=anchor.score,
                run_id=run_id,
            )
            continue
        if anchor.verdict is AnchorVerdict.FLAG:
            stats.flagged += 1
        confidence = adjusted_confidence(es.confidence_score, anchor)
        kept.append(_to_risk_signal(es, doc, entity_name, confidence))

    stats.kept = len(kept)
    return kept, stats


# ── LangGraph node ────────────────────────────────────────────────────────────

async def extraction_agent_node(
    state: dict, *, provider: Optional[LLMProvider] = None
) -> dict:
    """Extract verified RiskSignals from ``state['documents']`` concurrently.

    Returns the AgentState delta: raw_signals, cumulative llm_call_count and
    total_cost, plus any per-document extraction errors appended to errors.
    """
    documents: list[RawDocument] = list(state.get("documents") or [])
    if not documents:
        logger.info("[extraction] no documents — 0 signals extracted")
        return {"raw_signals": []}

    entity = state.get("resolved_entity")
    entity_name = (
        entity.canonical_name if entity else (documents[0].entity_name or "")
    )
    run_id = state.get("run_id")
    prior_calls = state.get("llm_call_count", 0)

    # Guardrail 8e: never exceed max_llm_calls (1 call per document here).
    # Reserve synthesis headroom so upstream extraction can't starve the report.
    budget = max(0, settings.max_llm_calls - prior_calls - settings.synthesis_call_reserve)
    if budget == 0:
        logger.warning("[extraction] max_llm_calls (%d) reached — skipping", settings.max_llm_calls)
        return {"raw_signals": []}
    docs_to_process = documents[:budget]
    if len(docs_to_process) < len(documents):
        logger.warning(
            "[extraction] llm-call budget caps extraction at %d/%d documents",
            len(docs_to_process), len(documents),
        )

    provider = provider or GeminiProvider()
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _process(doc: RawDocument):
        async with semaphore:
            prompt = build_extraction_prompt(
                doc.raw_text or "",
                entity_name,
                source_type=getattr(doc.source_type, "value", str(doc.source_type)),
            )
            try:
                result = await provider.complete(
                    prompt, ExtractionResult,
                    system=EXTRACTION_SYSTEM_PROMPT, use_fast=True,
                )
            except Exception as exc:  # isolate one doc's failure from the batch
                logger.warning("[extraction] LLM failed for %s: %s", doc.source_url, exc)
                return None, f"extraction: {doc.source_url}: {exc}"
            kept, stats = await verify_and_build(doc, result, entity_name, run_id=run_id)
            return (kept, stats), None

    results = await asyncio.gather(*[_process(d) for d in docs_to_process])

    all_signals: list[RiskSignal] = []
    errors: list[str] = []
    agg = DocExtractionStats()
    by_category: dict[str, int] = {}

    for payload, err in results:
        if err is not None:
            errors.append(err)
            continue
        kept, stats = payload
        all_signals.extend(kept)
        agg.extracted += stats.extracted
        agg.kept += stats.kept
        agg.rejected += stats.rejected
        agg.flagged += stats.flagged
        for s in kept:
            by_category[s.risk_category.value] = by_category.get(s.risk_category.value, 0) + 1

    # ── Quality metrics (Task 2.3.4) ──────────────────────────────────────────
    verified = agg.kept + agg.rejected
    pass_rate = (agg.kept / verified) if verified else 0.0
    rejection_rate = (agg.rejected / verified) if verified else 0.0
    logger.info(
        "[extraction] %d/%d docs → %d signals "
        "(%d rejected, %d flagged; pass=%.0f%%, reject=%.0f%%)",
        len(docs_to_process) - len(errors), len(docs_to_process), len(all_signals),
        agg.rejected, agg.flagged, pass_rate * 100, rejection_rate * 100,
    )
    if by_category:
        logger.info("[extraction] by category: %s", by_category)

    return {
        "raw_signals": all_signals,
        "llm_call_count": prior_calls + provider.call_count,
        "total_cost": state.get("total_cost", 0.0) + provider.total_cost_usd,
        "errors": list(state.get("errors") or []) + errors,
    }


__all__ = [
    "DocExtractionStats",
    "verify_and_build",
    "extraction_agent_node",
]
