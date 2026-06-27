"""
Prompt templates — Task 2.1 (Prompt Engineering — Extraction)

This module is the single source of truth for every LLM prompt in the pipeline.
It contains three things:

  1. Structured-output schemas the LLM must fill (`ExtractionResult`,
     `SeverityAssessment`). These are passed to `LLMProvider.complete(..., schema=)`
     so the model returns JSON that Pydantic validates directly — no parsing.
  2. The extraction system prompt + 5 few-shot examples (Tasks 2.1.1, 2.1.2).
  3. The severity-scoring prompt for the Risk Analysis Agent (Task 2.1.5).

Design references: design doc Sections 7 (RiskSignal), 8b (quote-anchor), 8f
(severity rubric). The extraction prompt deliberately separates *what the LLM
infers* (text, snippet, category, severity, polarity, data_date, confidence,
entities) from *what the pipeline already knows* (source_url, source_type) — the
Extraction Agent (Task 2.3) fills the latter when mapping ExtractedSignal →
RiskSignal.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.models.entities import EntityType
from src.models.signals import RiskCategory, Severity, SignalPolarity

# Guardrail: design doc Section 8e caps signals per document at 7.
MAX_SIGNALS_PER_DOC = 7


# ── Structured-output schemas ─────────────────────────────────────────────────

class ExtractedEntity(BaseModel):
    """A person / organisation the LLM links to a signal."""
    name: str
    entity_type: EntityType
    role: str = ""


class ExtractedSignal(BaseModel):
    """One risk (or positive) signal as emitted by the extraction LLM.

    Only fields the model can know from the document text alone. source_url,
    source_type, temporal_weight, corroboration and the final id are filled by
    the Extraction Agent / Risk Analysis Agent downstream.
    """
    text: str = Field(description="One-sentence, factual statement of the signal.")
    source_snippet: str = Field(
        description="VERBATIM 20–50 word span copied exactly from the document "
                    "that substantiates this signal (the quote anchor)."
    )
    risk_category: RiskCategory
    risk_subcategory: str = Field(default="", description="snake_case tag, e.g. 'revenue_decline'.")
    severity: Severity = Field(description="Initial estimate; refined later against the rubric.")
    signal_polarity: SignalPolarity
    data_date: Optional[str] = Field(
        default=None,
        description="ISO-8601 date (YYYY-MM-DD or YYYY-MM or YYYY) of the underlying "
                    "event if the document states it, else null.",
    )
    confidence_score: float = Field(ge=0.0, le=1.0)
    related_entities: list[ExtractedEntity] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """Top-level schema returned per document."""
    signals: list[ExtractedSignal] = Field(default_factory=list)


class SeverityAssessment(BaseModel):
    """Risk Analysis Agent output (Task 2.1.5 / 2.7)."""
    severity: Severity
    reasoning: str = Field(description="Why this severity, in 1–2 sentences.")
    rubric_reference: str = Field(
        description="The rubric tier / line the judgment is grounded in. Must quote "
                    "or paraphrase the supplied rubric context — never freelance."
    )


# ── Extraction system prompt (Task 2.1.1) ─────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = f"""\
You are a due-diligence risk-extraction analyst. You read ONE source document \
about a target company and extract the material risk signals AND positive \
signals it contains — nothing more.

OUTPUT
- Return JSON matching the schema exactly: a top-level "signals" array.
- Emit at most {MAX_SIGNALS_PER_DOC} signals. If the document contains more, keep \
only the {MAX_SIGNALS_PER_DOC} most material (highest severity × concreteness).
- If the document contains no material signal, return {{"signals": []}}. An empty \
result is a valid, correct answer — never invent a signal to fill the array.

EVERY SIGNAL MUST HAVE A QUOTE ANCHOR
- "source_snippet" MUST be a verbatim span of 20–50 words copied character-for-character \
from the document. Do NOT paraphrase, summarise, fix typos, or stitch together \
non-contiguous fragments. This snippet is fuzzy-matched against the source; a \
paraphrase will be rejected downstream.
- Choose the MINIMAL contiguous span that substantiates THIS signal. Do not pad the \
snippet with adjacent sentences about other topics — each signal's snippet should be \
about that signal only.
- "text" is your own one-sentence factual paraphrase of the signal; the snippet is the proof.

CLASSIFY EACH SIGNAL
- risk_category: one of FINANCIAL, LEGAL, REGULATORY, REPUTATIONAL, OPERATIONAL, \
CYBERSECURITY, ESG.
- signal_polarity: NEGATIVE (a risk), POSITIVE (a strength / mitigant), or NEUTRAL \
(context). Always extract POSITIVE signals too — a report of only bad news is biased.
- severity: CRITICAL / HIGH / MEDIUM / LOW / INFO — your initial estimate only; it is \
re-graded against a rubric later, so do not inflate.
- risk_subcategory: a short snake_case tag (e.g. revenue_decline, pending_litigation, \
data_breach, supplier_concentration).
- data_date: the ISO date of the underlying event if the document states one, else null. \
Use the event date, not the document's publication date, when they differ.
- confidence_score: 0.90–1.00 for an explicit, concrete, stated fact; 0.70–0.89 when it \
requires light inference; below 0.70 only if genuinely uncertain (prefer to omit instead).
- related_entities: people / regulators / subsidiaries named in the signal.

DO NOT EXTRACT
- Speculative or forward-looking language ("could", "may", "expects to", "is exploring").
- Generic industry headwinds that affect all players equally.
- Marketing copy, mission statements, or self-praise with no verifiable substance.
- Analyst opinions or price targets with no concrete underlying event.
- Routine, neutral administrative events that carry no adverse or material positive \
weight: permit / license applications, office or address changes, routine officer \
appointments. (A license being *revoked* or *denied* IS material — extract that.)
- Anything not actually present in THIS document.

Be precise, be literal with snippets, and prefer fewer high-quality signals over many weak ones.
"""


# ── Few-shot examples (Task 2.1.2) ────────────────────────────────────────────
# Five diverse documents covering all 7 categories, positive signals, and
# explicit "do NOT extract" cases. Each snippet is a verbatim 20–50 word span of
# its DOCUMENT. These objects are validated against the schema in tests.

class FewShotExample(BaseModel):
    source_type: str          # human label, e.g. "NEWS_ARTICLE"
    document: str
    extraction: ExtractionResult


_EX1_NEWS = FewShotExample(
    source_type="NEWS_ARTICLE",
    document=(
        "Acme Robotics CEO John Carver resigned on March 12, 2025 amid an internal "
        "investigation into falsified safety-test records, the company confirmed. "
        "Separately, a class-action lawsuit filed in the Northern District of "
        "California in February 2025 alleges the firm misled investors about order "
        "backlogs. Analysts said the stock could rebound if a new CEO is named quickly."
    ),
    extraction=ExtractionResult(signals=[
        ExtractedSignal(
            text="Acme Robotics' CEO resigned amid an investigation into falsified safety-test records.",
            source_snippet="Acme Robotics CEO John Carver resigned on March 12, 2025 amid an internal investigation into falsified safety-test records, the company confirmed.",
            risk_category=RiskCategory.REPUTATIONAL,
            risk_subcategory="executive_misconduct",
            severity=Severity.HIGH,
            signal_polarity=SignalPolarity.NEGATIVE,
            data_date="2025-03-12",
            confidence_score=0.95,
            related_entities=[ExtractedEntity(name="John Carver", entity_type=EntityType.PERSON, role="former CEO")],
        ),
        ExtractedSignal(
            text="A securities class-action lawsuit alleges Acme Robotics misled investors about order backlogs.",
            source_snippet="a class-action lawsuit filed in the Northern District of California in February 2025 alleges the firm misled investors about order backlogs",
            risk_category=RiskCategory.LEGAL,
            risk_subcategory="securities_litigation",
            severity=Severity.HIGH,
            signal_polarity=SignalPolarity.NEGATIVE,
            data_date="2025-02",
            confidence_score=0.9,
            related_entities=[ExtractedEntity(name="U.S. District Court, N.D. California", entity_type=EntityType.COURT, role="venue")],
        ),
        # NOTE: the "stock could rebound if a new CEO is named" sentence is
        # speculative analyst opinion — deliberately NOT extracted.
    ]),
)

_EX2_FILING = FewShotExample(
    source_type="SEC_FILING",
    document=(
        "In fiscal year 2024, total revenue declined 38% to $412 million from $664 "
        "million, driven by lower demand in our core segment. We rely on a single "
        "supplier for 82% of our battery cells, and any disruption could materially "
        "affect production. As of December 31, 2024, the company held $310 million in "
        "cash and cash equivalents and had no outstanding long-term debt."
    ),
    extraction=ExtractionResult(signals=[
        ExtractedSignal(
            text="Fiscal 2024 revenue fell 38% year over year to $412 million.",
            source_snippet="In fiscal year 2024, total revenue declined 38% to $412 million from $664 million, driven by lower demand in our core segment.",
            risk_category=RiskCategory.FINANCIAL,
            risk_subcategory="revenue_decline",
            severity=Severity.HIGH,
            signal_polarity=SignalPolarity.NEGATIVE,
            data_date="2024",
            confidence_score=0.97,
        ),
        ExtractedSignal(
            text="The company depends on a single supplier for 82% of its battery cells.",
            source_snippet="We rely on a single supplier for 82% of our battery cells, and any disruption could materially affect production.",
            risk_category=RiskCategory.OPERATIONAL,
            risk_subcategory="supplier_concentration",
            severity=Severity.MEDIUM,
            signal_polarity=SignalPolarity.NEGATIVE,
            data_date=None,
            confidence_score=0.92,
        ),
        ExtractedSignal(
            text="The company holds $310M cash with no long-term debt — a strong balance sheet.",
            source_snippet="As of December 31, 2024, the company held $310 million in cash and cash equivalents and had no outstanding long-term debt.",
            risk_category=RiskCategory.FINANCIAL,
            risk_subcategory="strong_liquidity",
            severity=Severity.INFO,
            signal_polarity=SignalPolarity.POSITIVE,
            data_date="2024-12-31",
            confidence_score=0.96,
        ),
    ]),
)

_EX3_REGISTRY = FewShotExample(
    source_type="COMPANY_REGISTRY",
    document=(
        "Company: NORTHWIND LOGISTICS LTD. Company number 04123987. Status: Active. "
        "Incorporated 12 June 2003. Registered in England and Wales. Two charges "
        "registered against the company remain outstanding as of 2024. Director Priya "
        "Nair was appointed on 1 September 2024."
    ),
    extraction=ExtractionResult(signals=[
        ExtractedSignal(
            text="Northwind Logistics has been an active registered company since 2003.",
            source_snippet="Status: Active. Incorporated 12 June 2003. Registered in England and Wales. Two charges registered against the company remain outstanding as of 2024.",
            risk_category=RiskCategory.OPERATIONAL,
            risk_subcategory="business_continuity",
            severity=Severity.INFO,
            signal_polarity=SignalPolarity.POSITIVE,
            data_date="2003-06-12",
            confidence_score=0.9,
        ),
        ExtractedSignal(
            text="Two charges remain registered (outstanding) against the company.",
            source_snippet="Two charges registered against the company remain outstanding as of 2024. Director Priya Nair was appointed on 1 September 2024.",
            risk_category=RiskCategory.FINANCIAL,
            risk_subcategory="secured_debt",
            severity=Severity.LOW,
            signal_polarity=SignalPolarity.NEGATIVE,
            data_date="2024",
            confidence_score=0.85,
        ),
        # NOTE: a routine director appointment is INFO/neutral background — not a
        # risk — and is deliberately NOT extracted as its own signal.
    ]),
)

_EX4_REGULATORY = FewShotExample(
    source_type="COURT_RECORD",
    document=(
        "The FDA issued a Warning Letter to Vertex Pharma Labs on 8 January 2025 "
        "citing significant violations of current Good Manufacturing Practice. In a "
        "filing with the state attorney general, the company disclosed a data breach "
        "in November 2024 exposing personal records of approximately 1.4 million "
        "patients. The EPA also assessed a $2.3 million penalty for wastewater "
        "discharge violations in 2023."
    ),
    extraction=ExtractionResult(signals=[
        ExtractedSignal(
            text="The FDA issued a GMP-violation Warning Letter to the company in January 2025.",
            source_snippet="The FDA issued a Warning Letter to Vertex Pharma Labs on 8 January 2025 citing significant violations of current Good Manufacturing Practice.",
            risk_category=RiskCategory.REGULATORY,
            risk_subcategory="fda_warning_letter",
            severity=Severity.HIGH,
            signal_polarity=SignalPolarity.NEGATIVE,
            data_date="2025-01-08",
            confidence_score=0.96,
            related_entities=[ExtractedEntity(name="U.S. Food and Drug Administration", entity_type=EntityType.REGULATOR, role="issuer")],
        ),
        ExtractedSignal(
            text="A November 2024 data breach exposed personal records of ~1.4 million patients.",
            source_snippet="In a filing with the state attorney general, the company disclosed a data breach in November 2024 exposing personal records of approximately 1.4 million patients.",
            risk_category=RiskCategory.CYBERSECURITY,
            risk_subcategory="data_breach",
            severity=Severity.CRITICAL,
            signal_polarity=SignalPolarity.NEGATIVE,
            data_date="2024-11",
            confidence_score=0.94,
        ),
        ExtractedSignal(
            text="The EPA assessed a $2.3M penalty for wastewater discharge violations.",
            source_snippet="approximately 1.4 million patients. The EPA also assessed a $2.3 million penalty for wastewater discharge violations in 2023.",
            risk_category=RiskCategory.ESG,
            risk_subcategory="environmental_penalty",
            severity=Severity.MEDIUM,
            signal_polarity=SignalPolarity.NEGATIVE,
            data_date="2023",
            confidence_score=0.93,
            related_entities=[ExtractedEntity(name="Environmental Protection Agency", entity_type=EntityType.REGULATOR, role="issuer")],
        ),
    ]),
)

_EX5_WEBSITE = FewShotExample(
    source_type="COMPANY_WEBSITE",
    document=(
        "At BrightPath Health we are passionate about reimagining patient care and "
        "delighting our customers every single day. BrightPath achieved ISO 27001 "
        "certification for information security in 2024 and was named to the Forbes "
        "Best Employers list. Our world-class team is second to none."
    ),
    extraction=ExtractionResult(signals=[
        ExtractedSignal(
            text="BrightPath Health achieved ISO 27001 information-security certification in 2024.",
            source_snippet="BrightPath achieved ISO 27001 certification for information security in 2024 and was named to the Forbes Best Employers list.",
            risk_category=RiskCategory.CYBERSECURITY,
            risk_subcategory="security_certification",
            severity=Severity.INFO,
            signal_polarity=SignalPolarity.POSITIVE,
            data_date="2024",
            confidence_score=0.88,
        ),
        # NOTE: "passionate about reimagining patient care", "world-class team
        # second to none" — marketing fluff with no verifiable substance — is
        # deliberately NOT extracted.
    ]),
)

FEW_SHOT_EXAMPLES: list[FewShotExample] = [
    _EX1_NEWS, _EX2_FILING, _EX3_REGISTRY, _EX4_REGULATORY, _EX5_WEBSITE,
]


# ── Prompt builders ───────────────────────────────────────────────────────────

def _render_examples() -> str:
    blocks: list[str] = []
    for i, ex in enumerate(FEW_SHOT_EXAMPLES, 1):
        blocks.append(
            f"### Example {i} — source type: {ex.source_type}\n"
            f"DOCUMENT:\n\"\"\"\n{ex.document}\n\"\"\"\n"
            f"CORRECT EXTRACTION:\n{ex.extraction.model_dump_json(indent=2)}"
        )
    return "\n\n".join(blocks)


def build_extraction_prompt(
    document_text: str,
    entity_name: str,
    *,
    source_type: str = "",
    include_examples: bool = True,
) -> str:
    """Assemble the user-turn prompt for extracting signals from one document.

    The system instruction (`EXTRACTION_SYSTEM_PROMPT`) is passed separately via
    `LLMProvider.complete(..., system=EXTRACTION_SYSTEM_PROMPT)`.
    """
    parts: list[str] = []
    if include_examples:
        parts.append("Here are worked examples of correct extraction:\n\n" + _render_examples())
    src = f" (source type: {source_type})" if source_type else ""
    parts.append(
        f"Now extract signals for the target company \"{entity_name}\" from the "
        f"following document{src}. Copy snippets VERBATIM. Return only JSON.\n\n"
        f"DOCUMENT:\n\"\"\"\n{document_text}\n\"\"\""
    )
    return "\n\n".join(parts)


# ── Severity scoring prompt (Task 2.1.5) ──────────────────────────────────────

SEVERITY_SYSTEM_PROMPT = """\
You are a risk-severity adjudicator. You assign a severity level to ONE risk \
signal, grounded explicitly in the provided severity rubric — never on intuition \
alone.

Rules:
- Choose exactly one of: CRITICAL, HIGH, MEDIUM, LOW, INFO.
- You MUST justify the level against the supplied rubric context. Quote or closely \
paraphrase the rubric tier you relied on in "rubric_reference".
- Calibrate conservatively. Reserve CRITICAL for immediate threats to business \
continuity or legal exposure (active fraud/sanctions/large breach under \
investigation). Do not mark everything HIGH — severity inflation destroys trust.
- POSITIVE signals are strengths; grade their materiality as INFO or LOW unless the \
rubric says otherwise.
- Return JSON matching the schema: severity, reasoning, rubric_reference.
"""


def build_severity_prompt(
    signal_text: str,
    risk_category: str,
    signal_polarity: str,
    rubric_context: str,
    *,
    source_snippet: str = "",
) -> str:
    """Assemble the user-turn prompt for re-grading one signal's severity.

    `rubric_context` is the top-k chunks retrieved from the knowledge base by the
    RAG layer (Task 2.6.5). The system instruction is `SEVERITY_SYSTEM_PROMPT`.
    """
    snippet_line = f"\nSOURCE SNIPPET (evidence): \"{source_snippet}\"" if source_snippet else ""
    return (
        f"SEVERITY RUBRIC CONTEXT (authoritative — ground your judgment here):\n"
        f"\"\"\"\n{rubric_context}\n\"\"\"\n\n"
        f"SIGNAL TO GRADE\n"
        f"- category: {risk_category}\n"
        f"- polarity: {signal_polarity}\n"
        f"- statement: {signal_text}{snippet_line}\n\n"
        f"Grade this signal's severity using the rubric above. Return only JSON."
    )


# ── Contradiction detection prompt (Task 2.7.4) ───────────────────────────────

class ContradictionAssessment(BaseModel):
    """Risk Analysis Agent output when checking whether two signals conflict."""
    is_contradictory: bool
    reason: str = Field(description="One sentence explaining the (non-)contradiction.")


CONTRADICTION_SYSTEM_PROMPT = """\
You judge whether two risk signals about the SAME company genuinely contradict \
each other — i.e. they cannot both be true / they paint opposite pictures of the \
same fact.

Rules:
- TRUE contradiction: the two signals make conflicting factual claims about the \
same underlying matter (e.g. "revenue grew 20%" vs "revenue fell 15%" for the \
same period; "passed the audit" vs "failed the audit").
- NOT a contradiction: two different facts that merely coexist (a lawsuit AND an \
award), or a risk AND an unrelated strength, or the same fact stated twice.
- Opposite polarity alone is NOT a contradiction unless the underlying claim \
conflicts.
- Return JSON: is_contradictory (bool), reason (one sentence).
"""


def build_contradiction_prompt(
    signal_a_text: str,
    signal_b_text: str,
    *,
    source_a: str = "",
    source_b: str = "",
) -> str:
    """Assemble the user-turn prompt for confirming a contradiction between two signals."""
    src_a = f" (source: {source_a})" if source_a else ""
    src_b = f" (source: {source_b})" if source_b else ""
    return (
        f"SIGNAL A{src_a}: {signal_a_text}\n"
        f"SIGNAL B{src_b}: {signal_b_text}\n\n"
        f"Do these two signals genuinely contradict each other? Return only JSON."
    )


# ── Synthesis output schemas (Task 3.2.1) ─────────────────────────────────────

class RecommendedActionOutput(BaseModel):
    """One recommended action from the synthesis LLM call."""
    description: str = Field(description="Specific, actionable 1–2 sentence recommendation.")
    priority: str = Field(description="Exactly IMMEDIATE, SHORT_TERM, or MONITOR.")
    signal_refs: list[str] = Field(
        default_factory=list,
        description="[Signal-N] citation strings triggering this action, e.g. ['[Signal-3]'].",
    )


class CategorySectionOutput(BaseModel):
    """LLM output for one risk category's detailed section."""
    section_text: str = Field(
        description=(
            "Analytical paragraph (2–4 sentences) about this category's risk signals. "
            "Every factual sentence must contain at least one [Signal-N] citation. "
            "Do not invent facts beyond the supplied signals."
        )
    )


class OverallSynthesisOutput(BaseModel):
    """LLM output for executive summary, strengths, and recommended actions."""
    executive_summary: str = Field(
        description=(
            "3–5 sentence executive summary of key findings across all categories. "
            "Every sentence must end with at least one [Signal-N] citation. "
            "Written for a senior decision-maker — direct and factual."
        )
    )
    strengths_section: str = Field(
        description=(
            "1–3 sentence summary of positive signals with [Signal-N] citations. "
            "If no positive signals exist, write exactly: "
            "'No material strengths identified from available data.'"
        )
    )
    recommended_actions: list[RecommendedActionOutput] = Field(
        default_factory=list,
        description="Actions for HIGH and CRITICAL signals only. Leave empty if none.",
    )


# ── Synthesis system prompts (Task 3.2.1) ─────────────────────────────────────

SYNTHESIS_CATEGORY_SYSTEM_PROMPT = """\
You are a due-diligence analyst writing ONE section of a risk report for a specific \
risk category. You receive numbered, verified risk signals for that category.

RULES
- Every factual sentence must end with at least one citation [Signal-N], where N is \
the signal number from the input list.
- Write 2–4 analytical sentences. Each sentence states a finding and cites its signal(s).
- Do NOT paraphrase or soften signals — they are verified facts.
- Do NOT use hedging language ("may", "could", "might").
- Return JSON with a single "section_text" field.
"""

SYNTHESIS_OVERALL_SYSTEM_PROMPT = """\
You are a senior due-diligence analyst writing the executive summary and recommendations \
for a risk assessment report.

RULES
executive_summary:
  - 3–5 sentences covering the most material findings across ALL risk categories.
  - Every sentence must end with at least one [Signal-N] citation.
  - If there are no signals, write: "No material risk signals were identified \
from the available data sources."

strengths_section:
  - 1–3 sentences about POSITIVE signals with [Signal-N] citations.
  - If no positive signals: write exactly \
"No material strengths identified from available data."

recommended_actions:
  - Generate actions for HIGH and CRITICAL signals ONLY.
  - CRITICAL → priority IMMEDIATE. HIGH → priority SHORT_TERM.
  - Each action must include signal_refs listing the triggering [Signal-N] strings.
  - Leave the list empty if there are no HIGH or CRITICAL signals.

Return JSON only — no prose outside the schema.
"""


def build_category_synthesis_prompt(
    category: str,
    signals_text: str,
    entity_name: str,
) -> str:
    """Assemble the user-turn prompt for one risk category's synthesis section."""
    return (
        f"COMPANY: {entity_name}\n"
        f"CATEGORY: {category}\n\n"
        f"SIGNALS (cite each with its [Signal-N] tag):\n{signals_text}\n\n"
        f"Write the {category} risk section for {entity_name}. "
        "Cite every factual sentence. Return JSON only."
    )


def build_overall_synthesis_prompt(
    entity_name: str,
    all_signals_text: str,
    positive_signals_text: str,
    category_sections: dict,
    data_sufficiency: str,
    evaluation_scope: str,
) -> str:
    """Assemble the user-turn prompt for executive summary, strengths, and actions."""
    sections_block = ""
    if category_sections:
        lines = [
            f"[{k.value if hasattr(k, 'value') else k}]\n{v}"
            for k, v in category_sections.items()
        ]
        sections_block = "\n\nGENERATED CATEGORY SECTIONS (for context):\n" + "\n\n".join(lines)

    return (
        f"COMPANY: {entity_name}\n"
        f"DATA SUFFICIENCY: {data_sufficiency}\n"
        f"SCOPE: {evaluation_scope}\n\n"
        f"ALL RISK SIGNALS:\n{all_signals_text or '(none)'}\n\n"
        f"POSITIVE SIGNALS:\n{positive_signals_text or '(none)'}"
        f"{sections_block}\n\n"
        "Write the executive summary (3–5 sentences with [Signal-N] citations), "
        "strengths section, and recommended actions for HIGH/CRITICAL signals. "
        "Return JSON only."
    )


__all__ = [
    "MAX_SIGNALS_PER_DOC",
    "ExtractedEntity",
    "ExtractedSignal",
    "ExtractionResult",
    "SeverityAssessment",
    "EXTRACTION_SYSTEM_PROMPT",
    "FewShotExample",
    "FEW_SHOT_EXAMPLES",
    "build_extraction_prompt",
    "SEVERITY_SYSTEM_PROMPT",
    "build_severity_prompt",
    "ContradictionAssessment",
    "CONTRADICTION_SYSTEM_PROMPT",
    "build_contradiction_prompt",
    # Task 3.2
    "RecommendedActionOutput",
    "CategorySectionOutput",
    "OverallSynthesisOutput",
    "SYNTHESIS_CATEGORY_SYSTEM_PROMPT",
    "SYNTHESIS_OVERALL_SYSTEM_PROMPT",
    "build_category_synthesis_prompt",
    "build_overall_synthesis_prompt",
]
