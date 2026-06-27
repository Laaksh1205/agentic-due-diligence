"""
Synthesis Agent — Task 3.2.

LangGraph node: scored RiskSignals → DueDiligenceReport.

Steps:
  1. Separate negative/positive signals; assign global [Signal-N] numbers (3.2.2).
  2. Compute per-category and overall risk scores on a 0–10 scale (3.2.4).
  3. For each populated risk category call the LLM for a detailed section (3.2.2).
  4. One final LLM call for executive summary, strengths, and recommended actions (3.2.2).
  5. Verify all [Signal-N] citations — log orphan refs (3.2.3).
  6. Prepend data-sufficiency caveat when data is LIMITED or SPARSE (3.2.5).
  7. Compile and return DueDiligenceReport.

Zero-signal state short-circuits without any LLM calls.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from src.config import settings
from src.llm.base import LLMProvider
from src.llm.gemini import GeminiProvider
from src.llm.prompts import (
    SYNTHESIS_CATEGORY_SYSTEM_PROMPT,
    SYNTHESIS_OVERALL_SYSTEM_PROMPT,
    CategorySectionOutput,
    OverallSynthesisOutput,
    build_category_synthesis_prompt,
    build_overall_synthesis_prompt,
)
from src.models.documents import DataSufficiency
from src.models.report import Action, ActionPriority, DueDiligenceReport, ReportMetadata, Source
from src.models.signals import RiskCategory, RiskSignal, Severity, SignalPolarity, SourceType

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r'\[Signal-(\d+)\]')

_SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.CRITICAL: 1.0,
    Severity.HIGH: 0.8,
    Severity.MEDIUM: 0.6,
    Severity.LOW: 0.4,
    Severity.INFO: 0.2,
}

_PRIORITY_MAP: dict[str, ActionPriority] = {
    "IMMEDIATE": ActionPriority.IMMEDIATE,
    "SHORT_TERM": ActionPriority.SHORT_TERM,
    "MONITOR": ActionPriority.MONITOR,
}

_SOURCE_TYPE_MAP: dict[str, SourceType] = {
    "web_search": SourceType.NEWS_ARTICLE,
    "website": SourceType.COMPANY_WEBSITE,
    "registry_lookup": SourceType.COMPANY_REGISTRY,
    "companies_house": SourceType.COMPANY_REGISTRY,
    "sec_edgar": SourceType.SEC_FILING,
}

_DATA_SUFFICIENCY_CAVEAT = (
    "IMPORTANT LIMITATION: This assessment is based on limited publicly available data. "
    "Findings may not be comprehensive. Manual investigation is recommended for a complete evaluation."
)


# ── Pure helpers ──────────────────────────────────────────────────────────────

def compute_category_scores(
    signals_by_category: dict[RiskCategory, list[RiskSignal]],
) -> tuple[dict[RiskCategory, float], float]:
    """Per-category and overall risk scores on a 0–10 scale.

    Category score = mean(severity_weight × confidence × temporal_weight) × 10.
    Overall = weighted average of category scores by signal count.
    """
    cat_scores: dict[RiskCategory, float] = {}
    weighted_sum = 0.0
    total_signals = 0

    for cat, sigs in signals_by_category.items():
        if not sigs:
            continue
        raw = sum(
            _SEVERITY_WEIGHT.get(s.severity, 0.2)
            * s.confidence_score
            * (s.temporal_weight or 1.0)
            for s in sigs
        ) / len(sigs)
        score = round(min(10.0, raw * 10.0), 2)
        cat_scores[cat] = score
        weighted_sum += score * len(sigs)
        total_signals += len(sigs)

    overall = round(weighted_sum / total_signals, 2) if total_signals else 0.0
    return cat_scores, overall


def verify_citations(text: str, valid_indices: set[int]) -> list[int]:
    """Return list of orphan [Signal-N] indices (N not in valid_indices)."""
    return [
        int(m.group(1))
        for m in _CITATION_RE.finditer(text)
        if int(m.group(1)) not in valid_indices
    ]


def _format_signals(
    signals: list[RiskSignal],
    signal_to_num: dict[uuid.UUID, int],
) -> str:
    lines: list[str] = []
    for sig in signals:
        num = signal_to_num[sig.id]
        cred = sig.credibility_tier or "GENERAL"
        flag = (
            " | UNVERIFIED (single low-credibility source — caveat this claim, keep it out of confident summary statements)"
            if sig.is_unverified else ""
        )
        lines.append(
            f"[Signal-{num}] {sig.severity.value} | {sig.risk_category.value} | "
            f"{sig.signal_polarity.value} | conf={sig.confidence_score:.2f} | "
            f"tw={sig.temporal_weight:.2f} | cred={cred} | srcs={sig.independent_source_count}{flag}\n"
            f"  Statement: {sig.text}\n"
            f"  Evidence: \"{sig.source_snippet[:180]}\"\n"
            f"  Source: {sig.source_url}"
        )
    return "\n\n".join(lines)


def _log_orphans(text: str, valid_indices: set[int], context: str) -> None:
    orphans = verify_citations(text, valid_indices)
    if orphans:
        logger.warning(
            "[synthesis] %d orphan citation(s) in %s: %s", len(orphans), context, orphans
        )


def _map_actions(
    action_outputs: list,
    signal_to_num: dict[uuid.UUID, int],
) -> list[Action]:
    """Map OverallSynthesisOutput.recommended_actions → Action objects."""
    num_to_id: dict[int, uuid.UUID] = {v: k for k, v in signal_to_num.items()}
    result: list[Action] = []
    for act in action_outputs:
        all_refs = " ".join(act.signal_refs or [])
        nums = [int(m.group(1)) for m in _CITATION_RE.finditer(all_refs)]
        related = [num_to_id[n] for n in nums if n in num_to_id]
        priority = _PRIORITY_MAP.get((act.priority or "").upper(), ActionPriority.MONITOR)
        result.append(Action(
            description=act.description,
            priority=priority,
            related_signals=related,
        ))
    return result


def _compile_report(
    *,
    entity,
    evaluation_scope: str,
    data_sufficiency: DataSufficiency,
    negative: list[RiskSignal],
    positive: list[RiskSignal],
    category_scores: dict[RiskCategory, float],
    overall_score: float,
    executive_summary: str,
    strengths_section: str,
    detailed_sections: dict[RiskCategory, str],
    recommended_actions: list[Action],
    state: dict,
    llm_calls: int,
    cost_usd: float,
) -> DueDiligenceReport:
    seen_urls: set[str] = set()
    sources_consulted: list[Source] = []
    for sig in negative + positive:
        if sig.source_url not in seen_urls:
            seen_urls.add(sig.source_url)
            sources_consulted.append(Source(url=sig.source_url, source_type=sig.source_type))

    sources_failed: list[Source] = [
        Source(
            url="",
            source_type=_SOURCE_TYPE_MAP.get(name, SourceType.NEWS_ARTICLE),
            name=name,
        )
        for name in (state.get("sources_failed") or [])
    ]

    return DueDiligenceReport(
        target_entity=entity,
        evaluation_scope=evaluation_scope,
        data_sufficiency=data_sufficiency,
        risk_signals=negative,
        positive_signals=positive,
        category_scores=category_scores,
        overall_risk_score=overall_score,
        executive_summary=executive_summary,
        strengths_section=strengths_section,
        detailed_sections=detailed_sections,
        recommended_actions=recommended_actions,
        sources_consulted=sources_consulted,
        sources_failed=sources_failed,
        metadata=ReportMetadata(
            llm_call_count=llm_calls,
            estimated_cost_usd=cost_usd,
            signals_extracted=state.get("signals_extracted", len(negative) + len(positive)),
            signals_rejected=state.get("signals_rejected", 0),
        ),
    )


# ── LangGraph node ────────────────────────────────────────────────────────────

async def synthesis_agent_node(
    state: dict, *, provider: Optional[LLMProvider] = None
) -> dict:
    """Generate a DueDiligenceReport from scored signals in state.

    Returns state delta: report, llm_call_count, total_cost, errors.
    """
    scored: list[RiskSignal] = list(state.get("scored_signals") or [])
    entity = state["resolved_entity"]
    data_sufficiency: DataSufficiency = state.get("data_sufficiency", DataSufficiency.SPARSE)
    # AgentState uses "scope"; synthesis state uses "evaluation_scope" — accept both.
    evaluation_scope: str = state.get("scope") or state.get("evaluation_scope") or "full"
    prior_calls: int = state.get("llm_call_count", 0)
    prior_cost: float = state.get("total_cost", 0.0)
    errors: list[str] = list(state.get("errors") or [])

    # Zero-signal fast path — no LLM calls needed; provider is not created.
    if not scored:
        summary = "No material risk signals were identified from the available data sources."
        if data_sufficiency in (DataSufficiency.LIMITED, DataSufficiency.SPARSE):
            summary = f"{_DATA_SUFFICIENCY_CAVEAT}\n\n{summary}"
        report = _compile_report(
            entity=entity,
            evaluation_scope=evaluation_scope,
            data_sufficiency=data_sufficiency,
            negative=[],
            positive=[],
            category_scores={},
            overall_score=0.0,
            executive_summary=summary,
            strengths_section="No material strengths identified from available data.",
            detailed_sections={},
            recommended_actions=[],
            state=state,
            llm_calls=prior_calls,
            cost_usd=prior_cost,
        )
        return {
            "report": report,
            "llm_call_count": prior_calls,
            "total_cost": prior_cost,
            "errors": errors,
        }

    # Provider only needed when there are signals to synthesise.
    provider = provider or GeminiProvider()

    # Separate polarities; assign stable global numbers (negative first, then positive).
    negative = [s for s in scored if s.signal_polarity != SignalPolarity.POSITIVE]
    positive = [s for s in scored if s.signal_polarity == SignalPolarity.POSITIVE]
    all_ordered = negative + positive
    signal_to_num: dict[uuid.UUID, int] = {sig.id: i + 1 for i, sig in enumerate(all_ordered)}
    valid_indices: set[int] = set(range(1, len(all_ordered) + 1))

    # Group negative signals by category.
    by_cat: dict[RiskCategory, list[RiskSignal]] = {}
    for sig in negative:
        by_cat.setdefault(sig.risk_category, []).append(sig)

    category_scores, overall_score = compute_category_scores(by_cat)
    budget = max(0, settings.max_llm_calls - prior_calls)

    # ── Per-category sections (3.2.2 step 2) ─────────────────────────────────
    detailed_sections: dict[RiskCategory, str] = {}
    sorted_cats = sorted(by_cat, key=lambda c: category_scores.get(c, 0), reverse=True)

    for cat in sorted_cats:
        if provider.call_count >= budget:
            logger.warning("[synthesis] LLM budget reached — skipping remaining category sections")
            break
        prompt = build_category_synthesis_prompt(
            cat.value,
            _format_signals(by_cat[cat], signal_to_num),
            entity.canonical_name,
        )
        try:
            out: CategorySectionOutput = await provider.complete(
                prompt, CategorySectionOutput,
                system=SYNTHESIS_CATEGORY_SYSTEM_PROMPT, use_fast=True,
            )
            _log_orphans(out.section_text, valid_indices, f"{cat.value} section")
            detailed_sections[cat] = out.section_text
        except Exception as exc:
            logger.warning("[synthesis] category section failed (%s): %s", cat.value, exc)
            errors.append(f"synthesis:{cat.value}:{exc}")

    # ── Overall synthesis call (3.2.2 steps 3–4) ─────────────────────────────
    executive_summary = ""
    strengths_section = ""
    recommended_actions: list[Action] = []

    if provider.call_count < budget:
        overall_prompt = build_overall_synthesis_prompt(
            entity_name=entity.canonical_name,
            all_signals_text=_format_signals(negative, signal_to_num),
            positive_signals_text=_format_signals(positive, signal_to_num),
            category_sections=detailed_sections,
            data_sufficiency=data_sufficiency.value,
            evaluation_scope=evaluation_scope,
        )
        try:
            out2: OverallSynthesisOutput = await provider.complete(
                overall_prompt, OverallSynthesisOutput,
                system=SYNTHESIS_OVERALL_SYSTEM_PROMPT, use_fast=False,
            )
            executive_summary = out2.executive_summary
            strengths_section = out2.strengths_section
            _log_orphans(executive_summary, valid_indices, "executive_summary")
            _log_orphans(strengths_section, valid_indices, "strengths_section")
            recommended_actions = _map_actions(out2.recommended_actions, signal_to_num)
        except Exception as exc:
            logger.warning("[synthesis] overall synthesis failed: %s", exc)
            errors.append(f"synthesis:overall:{exc}")
    else:
        logger.warning("[synthesis] LLM budget exhausted — skipping overall synthesis call")

    # ── Data-sufficiency caveat (3.2.5) ──────────────────────────────────────
    if data_sufficiency in (DataSufficiency.LIMITED, DataSufficiency.SPARSE):
        executive_summary = (
            f"{_DATA_SUFFICIENCY_CAVEAT}\n\n{executive_summary}"
            if executive_summary
            else _DATA_SUFFICIENCY_CAVEAT
        )

    logger.info(
        "[synthesis] report built: %d risk signals, %d positive, %d categories, "
        "%d actions; overall_score=%.1f",
        len(negative), len(positive), len(detailed_sections),
        len(recommended_actions), overall_score,
    )

    report = _compile_report(
        entity=entity,
        evaluation_scope=evaluation_scope,
        data_sufficiency=data_sufficiency,
        negative=negative,
        positive=positive,
        category_scores=category_scores,
        overall_score=overall_score,
        executive_summary=executive_summary,
        strengths_section=strengths_section,
        detailed_sections=detailed_sections,
        recommended_actions=recommended_actions,
        state=state,
        llm_calls=prior_calls + provider.call_count,
        cost_usd=prior_cost + provider.total_cost_usd,
    )

    return {
        "report": report,
        "llm_call_count": prior_calls + provider.call_count,
        "total_cost": prior_cost + provider.total_cost_usd,
        "errors": errors,
    }


__all__ = [
    "compute_category_scores",
    "verify_citations",
    "synthesis_agent_node",
]
