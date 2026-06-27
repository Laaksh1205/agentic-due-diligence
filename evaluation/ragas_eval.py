"""RAGAS-style LLM-judge metrics (Task 5.1.5).

The ``ragas`` PyPI package (0.4.x) pins an incompatible langchain stack and its
import is currently broken on this environment, so rather than fight the
dependency matrix we implement the three core RAGAS metrics directly against the
project's own Gemini provider. The algorithms follow the published RAGAS method:

* **faithfulness** — decompose the generated answer into atomic statements, then
  judge each statement as supported / not-supported by the retrieved contexts.
  Score = supported / total. (RAGAS "Faithfulness".)
* **answer_relevancy** — ask the LLM to generate candidate questions the answer
  would answer, embed them, and take the mean cosine similarity to the real
  question. (RAGAS "ResponseRelevancy".)
* **context_precision** — with the ground-truth reference, judge each retrieved
  context as useful / not, then take the reference-weighted average precision@k.
  (RAGAS "LLMContextPrecisionWithReference".)

These are genuine LLM-judged numbers (Gemini as judge) plus real embeddings, not
the deterministic citation-grounding floor.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from pydantic import BaseModel, Field


# ── Structured judge schemas ──────────────────────────────────────────────────

class Statements(BaseModel):
    statements: list[str] = Field(default_factory=list)


class Verdicts(BaseModel):
    # One 0/1 verdict per input item, in order.
    verdicts: list[int] = Field(default_factory=list)


class Questions(BaseModel):
    questions: list[str] = Field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _answer_text(report: dict) -> str:
    parts = [report.get("executive_summary") or ""]
    secs = report.get("detailed_sections") or {}
    if isinstance(secs, dict):
        parts.extend(str(v) for v in secs.values())
    return "\n".join(p for p in parts if p).strip()


def _contexts(report: dict, limit: int = 80) -> list[str]:
    """Retrieved contexts = the verbatim source snippets behind each signal."""
    ctx: list[str] = []
    for s in (report.get("risk_signals") or []):
        snip = (s.get("source_snippet") or s.get("text") or "").strip()
        if snip:
            ctx.append(snip)
    # De-dup while preserving order, cap to keep judge calls bounded.
    seen, out = set(), []
    for c in ctx:
        k = c[:120].lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
        if len(out) >= limit:
            break
    return out


async def _complete_retry(provider, *args, attempts: int = 4, **kw):
    """provider.complete with backoff for transient 503 'high demand' errors."""
    delay = 3.0
    for i in range(attempts):
        try:
            return await provider.complete(*args, **kw)
        except Exception as exc:
            if "503" in str(exc) and i < attempts - 1:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            raise


def _cosine(a, b) -> float:
    import numpy as np
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


# ── Metric implementations ────────────────────────────────────────────────────

async def _faithfulness(provider, answer: str, contexts: list[str]) -> Optional[float]:
    if not answer or not contexts:
        return None
    decomp = await _complete_retry(provider, 
        f"Break the following analysis into a list of atomic factual statements "
        f"(one claim each, self-contained). Return at most 20.\n\nANALYSIS:\n{answer[:6000]}",
        Statements,
        system="You decompose text into atomic factual statements for verification.",
        use_fast=True,
    )
    statements = [s for s in decomp.statements if s.strip()][:20]
    if not statements:
        return None
    ctx_block = "\n".join(f"- {c}" for c in contexts)
    numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(statements))
    judged = await _complete_retry(provider, 
        f"CONTEXT (retrieved evidence):\n{ctx_block}\n\n"
        f"STATEMENTS:\n{numbered}\n\n"
        f"For each statement, output 1 if it can be directly inferred from the "
        f"CONTEXT, else 0. Return the verdicts in the same order.",
        Verdicts,
        system="You are a strict fact-verification judge. Only 1 if the context supports the claim.",
        use_fast=False,
    )
    v = judged.verdicts[: len(statements)]
    if not v:
        return None
    return round(sum(1 for x in v if x) / len(v), 4)


async def _answer_relevancy(provider, embedder, question: str, answer: str) -> Optional[float]:
    if not answer:
        return None
    gen = await _complete_retry(provider, 
        f"Given this ANSWER, generate 3 distinct questions that it directly and "
        f"completely answers.\n\nANSWER:\n{answer[:6000]}",
        Questions,
        system="You generate the questions an answer responds to (RAGAS answer-relevancy).",
        use_fast=True,
    )
    gen_qs = [q for q in gen.questions if q.strip()][:3]
    if not gen_qs:
        return None
    # The project embedder is a callable (texts -> np.ndarray), not a model object.
    encode = embedder.encode if hasattr(embedder, "encode") else embedder
    embs = encode([question] + gen_qs)
    q_emb, gen_embs = embs[0], embs[1:]
    sims = [_cosine(q_emb, g) for g in gen_embs]
    return round(sum(sims) / len(sims), 4) if sims else None


async def _context_precision(provider, contexts: list[str], reference: str) -> Optional[float]:
    if not contexts or not reference:
        return None
    ctx = contexts[:20]
    numbered = "\n".join(f"{i+1}. {c}" for i, c in enumerate(ctx))
    judged = await _complete_retry(provider, 
        f"REFERENCE ANSWER (ground truth risks):\n{reference[:6000]}\n\n"
        f"RETRIEVED CONTEXTS:\n{numbered}\n\n"
        f"For each context, output 1 if it is useful for arriving at the REFERENCE "
        f"ANSWER, else 0. Same order.",
        Verdicts,
        system="You judge whether each retrieved context is relevant to the reference answer.",
        use_fast=False,
    )
    rel = judged.verdicts[: len(ctx)]
    if not rel:
        return None
    # RAGAS reference-weighted average precision@k.
    hits, weighted = 0, 0.0
    for k, r in enumerate(rel, start=1):
        if r:
            hits += 1
            weighted += hits / k
    return round(weighted / hits, 4) if hits else 0.0


async def evaluate_ragas_async(report: dict, truth: dict | None, question: str,
                               embedder, provider) -> dict:
    answer = _answer_text(report)
    contexts = _contexts(report)
    reference = ""
    if truth:
        reference = "\n".join(
            f"- {s.get('text', '')}" for s in (truth.get("signals") or [])
        )

    faith = relev = ctxp = None
    errors: list[str] = []
    try:
        faith = await _faithfulness(provider, answer, contexts)
    except Exception as exc:  # judge errors must not crash the harness
        errors.append(f"faithfulness: {exc}")
    try:
        relev = await _answer_relevancy(provider, embedder, question, answer)
    except Exception as exc:
        errors.append(f"answer_relevancy: {exc}")
    try:
        ctxp = await _context_precision(provider, contexts, reference)
    except Exception as exc:
        errors.append(f"context_precision: {exc}")

    return {
        "faithfulness": faith,
        "answer_relevancy": relev,
        "context_precision": ctxp,
        "tool_call_accuracy": None,  # needs execution-trace labelling (5.1.6, manual)
        "source": "ragas-style LLM judge (Gemini) + embeddings",
        "n_statements_contexts": {"contexts": len(contexts)},
        "errors": errors,
    }


def evaluate_ragas(report: dict, truth: dict | None, question: str, embedder, provider) -> dict:
    return asyncio.run(evaluate_ragas_async(report, truth, question, embedder, provider))
