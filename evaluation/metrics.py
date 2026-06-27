"""Evaluation metrics — Task 5.1.

Pure, dependency-light (rapidfuzz only) functions that score a pipeline output
against the Phase-0 manual ground truth. Kept side-effect-free so they're unit
testable without network or LLM calls; ``run_eval.py`` orchestrates them.

Honesty note on precision/recall: the manual baseline catalogs the *major* risks
(~12 for Boeing). The system extracts far more granular signals (50+), so:
  - RECALL (did we find each known risk?) is computed automatically here.
  - PRECISION in the strict "is each signal a genuine risk?" sense needs human
    labelling (plan 5.1.1). We expose `precision_from_labels()` for a labels file
    and a transparent `precision_proxy()` lower bound from ground-truth overlap.
"""

from __future__ import annotations

import re
import statistics
from typing import Iterable, Optional

from rapidfuzz import fuzz

# ── Severity ordering (for within-one-band accuracy) ─────────────────────────
SEVERITY_ORDER = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
SUFFICIENCY_ORDER = ["SPARSE", "LIMITED", "ADEQUATE", "RICH"]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def _host(url: str) -> str:
    s = (url or "").split("://", 1)[-1]
    return s.split("/", 1)[0].removeprefix("www.").lower()


_STOP = set(
    "the a an of for and or to in on at by with from is are was were be as that this it "
    "its has have had will not boeing company since due also their than over into out".split()
)


def _key_tokens(s: str) -> set[str]:
    """Distinctive tokens (len>3, non-stopword) used for topical overlap matching."""
    return {w for w in _norm(s).split() if len(w) > 3 and w not in _STOP}


def match_score(pred_text: str, truth_text: str) -> float:
    """0–100 similarity. Max of token-set, partial-ratio, and key-token overlap.

    The ground truth catalogs risks as long paragraphs while signals are short and
    reworded, so token_set_ratio alone under-counts genuine topical matches
    (e.g. "door plug blowout"). We blend it with partial_ratio and a distinctive-
    token overlap so a real topical hit isn't missed for lexical reasons.
    """
    pn, tn = _norm(pred_text), _norm(truth_text)
    lex = max(fuzz.token_set_ratio(pn, tn), fuzz.partial_ratio(pn, tn))
    pt, tt = _key_tokens(pred_text), _key_tokens(truth_text)
    shared = pt & tt
    overlap = (len(shared) / min(len(tt), 12)) * 100 if tt else 0.0
    # Require >=2 shared distinctive tokens before trusting overlap alone.
    if len(shared) >= 2:
        return max(lex, overlap)
    return lex


def _sig_text(sig: dict) -> str:
    return sig.get("text") or sig.get("normalized_text") or ""


def _best_truth_match(
    pred: dict, truth_signals: list[dict], threshold: float
) -> Optional[int]:
    """Return the id of the best lexically-matching ground-truth signal, or None.

    A predicted signal matches a truth signal if their texts are fuzzily similar
    OR they cite the same source host (same underlying event, reworded).
    """
    best_id, best = None, 0.0
    p_text, p_host = _sig_text(pred), _host(pred.get("source_url", ""))
    for t in truth_signals:
        score = match_score(p_text, t.get("text", ""))
        if p_host and p_host == _host(t.get("source_url", "")):
            score = max(score, threshold)  # same source → count as a match
        if score > best:
            best, best_id = score, t.get("id")
    return best_id if best >= threshold else None


# ── Unified match structure (lexical OR embedding) ────────────────────────────
#
# build_match() computes, for each ground-truth signal, the best-matching
# predicted signal and its score. Embeddings (all-MiniLM-L6-v2, the same model
# the dedup step uses) give semantically robust matches across the long-paragraph
# vs short-signal wording gap; lexical is the dependency-free fallback used by
# the unit tests. recall/precision/severity all consume this one structure.

# Default match thresholds per scale.
EMB_THRESHOLD = 0.45     # cosine on normalized MiniLM embeddings
LEX_THRESHOLD = 55.0     # blended fuzzy score (0–100)


def build_match(pred_signals: list[dict], truth_signals: list[dict], embedder=None) -> dict:
    """Best predicted match per ground-truth signal.

    Returns {scale, threshold, best_score_per_truth, best_pred_per_truth,
    best_score_per_pred}. ``embedder`` is a callable(texts)->normalized vectors;
    if None, falls back to the blended lexical ``match_score``.
    """
    n_t, n_p = len(truth_signals), len(pred_signals)
    best_truth = [0.0] * n_t
    best_pred_obj: list[Optional[dict]] = [None] * n_t
    best_per_pred = [0.0] * n_p

    if embedder is not None and n_t and n_p:
        import numpy as np

        pv = np.asarray(embedder([_sig_text(p) for p in pred_signals]), dtype=float)
        tv = np.asarray(embedder([t.get("text", "") for t in truth_signals]), dtype=float)
        sim = tv @ pv.T  # cosine (vectors are normalized)
        for ti in range(n_t):
            pi = int(sim[ti].argmax())
            best_truth[ti] = float(sim[ti][pi])
            best_pred_obj[ti] = pred_signals[pi]
        for pi in range(n_p):
            best_per_pred[pi] = float(sim[:, pi].max())
        return {
            "scale": "embed",
            "threshold": EMB_THRESHOLD,
            "best_score_per_truth": best_truth,
            "best_pred_per_truth": best_pred_obj,
            "best_score_per_pred": best_per_pred,
        }

    # Lexical fallback.
    for ti, t in enumerate(truth_signals):
        for p in pred_signals:
            sc = match_score(_sig_text(p), t.get("text", ""))
            if _host(p.get("source_url", "")) and _host(p.get("source_url", "")) == _host(t.get("source_url", "")):
                sc = max(sc, LEX_THRESHOLD)
            if sc > best_truth[ti]:
                best_truth[ti], best_pred_obj[ti] = sc, p
    for pi, p in enumerate(pred_signals):
        best_per_pred[pi] = max(
            (match_score(_sig_text(p), t.get("text", "")) for t in truth_signals), default=0.0
        )
    return {
        "scale": "lex",
        "threshold": LEX_THRESHOLD,
        "best_score_per_truth": best_truth,
        "best_pred_per_truth": best_pred_obj,
        "best_score_per_pred": best_per_pred,
    }


# ── Recall (vs manual ground truth) ───────────────────────────────────────────

def recall_vs_truth(
    pred_signals: list[dict],
    truth_signals: list[dict],
    threshold: Optional[float] = None,
    match: Optional[dict] = None,
) -> dict:
    """Fraction of ground-truth risks found by at least one predicted signal."""
    m = match or build_match(pred_signals, truth_signals)
    thr = threshold if threshold is not None else m["threshold"]
    total = len(truth_signals)
    found = [
        truth_signals[i]["id"]
        for i, sc in enumerate(m["best_score_per_truth"])
        if sc >= thr
    ]
    missed = [t["id"] for t in truth_signals if t["id"] not in set(found)]
    return {
        "recall": (len(found) / total) if total else 0.0,
        "matched_truth": len(found),
        "total_truth": total,
        "missed_truth_ids": missed,
    }


def precision_proxy(
    pred_signals: list[dict],
    truth_signals: list[dict],
    threshold: Optional[float] = None,
    match: Optional[dict] = None,
) -> dict:
    """LOWER-BOUND precision: share of predicted signals that map to a known risk.

    This understates true precision (the system legitimately finds more than the
    12-risk baseline), so treat it as a floor, not the headline number.
    """
    if not pred_signals:
        return {"precision_proxy": 0.0, "matched_pred": 0, "total_pred": 0}
    m = match or build_match(pred_signals, truth_signals)
    thr = threshold if threshold is not None else m["threshold"]
    matched = sum(1 for sc in m["best_score_per_pred"] if sc >= thr)
    return {
        "precision_proxy": matched / len(pred_signals),
        "matched_pred": matched,
        "total_pred": len(pred_signals),
    }


def precision_from_labels(labels: dict[str, bool]) -> dict:
    """Precision from a manual labels file: {signal_id: is_true_positive}."""
    if not labels:
        return {"precision": None, "labeled": 0}
    tp = sum(1 for v in labels.values() if v)
    return {"precision": tp / len(labels), "labeled": len(labels), "true_positives": tp}


# ── Severity accuracy ─────────────────────────────────────────────────────────

def severity_accuracy(
    pred_signals: list[dict],
    truth_signals: list[dict],
    threshold: Optional[float] = None,
    match: Optional[dict] = None,
) -> dict:
    """For matched (truth, best-pred) pairs, how often severities agree."""
    m = match or build_match(pred_signals, truth_signals)
    thr = threshold if threshold is not None else m["threshold"]
    exact = within_one = pairs = 0
    for i, t in enumerate(truth_signals):
        if m["best_score_per_truth"][i] < thr:
            continue
        best_p = m["best_pred_per_truth"][i]
        if best_p is None:
            continue
        ts, ps = t.get("severity", "").upper(), (best_p.get("severity") or "").upper()
        if ts not in SEVERITY_ORDER or ps not in SEVERITY_ORDER:
            continue
        pairs += 1
        if ts == ps:
            exact += 1
        if abs(SEVERITY_ORDER.index(ts) - SEVERITY_ORDER.index(ps)) <= 1:
            within_one += 1
    return {
        "severity_pairs": pairs,
        "severity_exact": (exact / pairs) if pairs else None,
        "severity_within_one": (within_one / pairs) if pairs else None,
    }


# ── Data sufficiency accuracy ─────────────────────────────────────────────────

def data_sufficiency_accuracy(pred_tier: Optional[str], truth_tier: Optional[str]) -> dict:
    if not pred_tier or not truth_tier:
        return {"exact": None, "within_one": None, "pred": pred_tier, "truth": truth_tier}
    p, t = pred_tier.upper(), truth_tier.upper()
    within = (
        abs(SUFFICIENCY_ORDER.index(p) - SUFFICIENCY_ORDER.index(t)) <= 1
        if p in SUFFICIENCY_ORDER and t in SUFFICIENCY_ORDER
        else None
    )
    return {"exact": p == t, "within_one": within, "pred": p, "truth": t}


# ── Verification / dedup / guardrails ─────────────────────────────────────────

def verification_stats(metadata: dict) -> dict:
    extracted = metadata.get("signals_extracted", 0) or 0
    rejected = metadata.get("signals_rejected", 0) or 0
    return {
        "signals_extracted": extracted,
        "signals_rejected": rejected,
        "rejection_rate": (rejected / extracted) if extracted else 0.0,
        "verification_pass_rate": ((extracted - rejected) / extracted) if extracted else None,
    }


def dedup_stats(pred_signals: list[dict]) -> dict:
    corroborated = sum(1 for s in pred_signals if s.get("is_corroborated"))
    contradictory = sum(1 for s in pred_signals if s.get("is_contradictory"))
    return {
        "total_signals": len(pred_signals),
        "corroborated": corroborated,
        "corroborated_rate": (corroborated / len(pred_signals)) if pred_signals else 0.0,
        "contradictory": contradictory,
    }


def guardrail_compliance(metadata: dict, *, max_llm_calls: int, max_cost_usd: float) -> dict:
    calls = metadata.get("llm_call_count", 0) or 0
    cost = metadata.get("estimated_cost_usd", 0.0) or 0.0
    ok = calls <= max_llm_calls and cost <= max_cost_usd
    return {
        "llm_call_count": calls,
        "max_llm_calls": max_llm_calls,
        "estimated_cost_usd": round(cost, 4),
        "max_cost_usd": max_cost_usd,
        "compliant": ok,
    }


# ── Faithfulness (citation grounding) — heuristic RAGAS substitute ────────────

_CITE_RE = re.compile(r"Signal-(\d+)", re.IGNORECASE)


def citation_grounding(report: dict) -> dict:
    """Fraction of [Signal-N] citations that reference a real signal id.

    A lightweight Faithfulness proxy: a grounded report cites only signals that
    exist. 1.0 = no orphan citations. (RAGAS Faithfulness needs an LLM; this is
    the deterministic floor used when ragas is unavailable.)
    """
    valid: set = set()
    for i, s in enumerate(report.get("risk_signals", []) + report.get("positive_signals", []), 1):
        valid.add(str(i))
        if s.get("id") is not None:
            valid.add(str(s["id"]))
    text = report.get("executive_summary", "") + " " + " ".join(
        (report.get("detailed_sections") or {}).values()
    )
    refs = _CITE_RE.findall(text)
    if not refs:
        return {"citations": 0, "grounded": None, "orphans": 0}
    orphans = [r for r in refs if r not in valid]
    return {
        "citations": len(refs),
        "grounded": (len(refs) - len(orphans)) / len(refs),
        "orphans": len(orphans),
    }


# ── Consistency (Jaccard) ─────────────────────────────────────────────────────

def signal_fingerprints(
    pred_signals: list[dict], truth_signals: Optional[list[dict]] = None, threshold: float = 55.0
) -> set[str]:
    """Canonical keys for a run's signals, for set-based consistency comparison.

    With ground truth: key = matched truth id (so the same underlying risk maps to
    the same key across runs). Without: key = normalized first 8 words.
    """
    keys: set[str] = set()
    for p in pred_signals:
        if truth_signals:
            mid = _best_truth_match(p, truth_signals, threshold)
            if mid is not None:
                keys.add(f"t{mid}")
                continue
        keys.add("n:" + " ".join(_norm(_sig_text(p)).split()[:8]))
    return keys


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def consistency(signal_sets: list[set]) -> dict:
    """Mean pairwise Jaccard across N runs (plan 5.1.4 target >= 0.80)."""
    if len(signal_sets) < 2:
        return {"runs": len(signal_sets), "mean_jaccard": None, "pairwise": []}
    pairwise = []
    for i in range(len(signal_sets)):
        for j in range(i + 1, len(signal_sets)):
            pairwise.append(jaccard(signal_sets[i], signal_sets[j]))
    return {
        "runs": len(signal_sets),
        "mean_jaccard": statistics.mean(pairwise),
        "min_jaccard": min(pairwise),
        "pairwise": [round(x, 3) for x in pairwise],
    }


def latency_percentiles(latencies: Iterable[float]) -> dict:
    vals = sorted(x for x in latencies if x and x > 0)
    if not vals:
        return {"p50": None, "p95": None, "n": 0}

    def pct(p: float) -> float:
        k = (len(vals) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)

    return {"p50": round(pct(0.50), 1), "p95": round(pct(0.95), 1), "n": len(vals)}
