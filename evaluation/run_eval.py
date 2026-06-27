"""Automated evaluation harness — Task 5.1.7.

Loads ground truth, evaluates the pipeline's output for each company, and prints
+ saves a metrics table covering plan 5.1.2 (precision/recall, verification,
severity, dedup, latency, cost), 5.1.4 (consistency), 5.1.5 (RAGAS / faithfulness),
and 5.1.6 (agentic metrics).

Usage:
    python evaluation/run_eval.py --all
    python evaluation/run_eval.py --company boeing
    python evaluation/run_eval.py --all --live          # fresh pipeline runs (real API calls)
    python evaluation/run_eval.py --company boeing --consistency 3 --live
    python evaluation/run_eval.py --all --ragas          # add RAGAS LLM metrics (needs `ragas`)

Without --live it evaluates the most recent cached run in output/ for each
company, so the harness is runnable and verifiable offline. --live performs real
pipeline runs (LLM + network) and is gated behind the flag to avoid surprise spend.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# UTF-8 stdout (Windows consoles default to cp1252; report glyphs need utf-8).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import metrics  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH_DIR = ROOT / "evaluation" / "ground_truth"
OUTPUT_DIR = ROOT / "output"
RESULTS_DIR = ROOT / "evaluation"

# Company registry: query name, ground-truth file, cached-output glob.
COMPANIES = {
    "boeing": {"query": "Boeing", "truth": "boeing.json", "glob": "boeing*_*.json"},
    "stripe": {"query": "Stripe", "truth": "stripe.json", "glob": "stripe*_*.json"},
    "chime": {"query": "Chime", "truth": "chime.json", "glob": "chime*_*.json"},
}


# ── Loading ───────────────────────────────────────────────────────────────────

def _load_ground_truth(name: str) -> dict | None:
    p = GROUND_TRUTH_DIR / COMPANIES[name]["truth"]
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _adapt_to_report(raw: dict) -> dict:
    """Normalize a cached file (full report OR signals-only export) to a report."""
    if "risk_signals" in raw:
        return raw
    sigs = raw.get("signals", [])
    return {
        "risk_signals": sigs,
        "positive_signals": [],
        "data_sufficiency": raw.get("data_sufficiency"),
        "category_scores": {},
        "executive_summary": "",
        "detailed_sections": {},
        "sources_consulted": [],
        "sources_failed": [],
        "metadata": {"signals_extracted": len(sigs), "signals_rejected": 0},
        "_signals_only": True,
    }


def _cached_reports(name: str) -> list[tuple[Path, dict]]:
    """All cached full reports (with signals) for a company, newest first."""
    files = sorted(OUTPUT_DIR.glob(COMPANIES[name]["glob"]), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "risk_signals" in d and d["risk_signals"]:
            out.append((f, d))
    return out


def _latest_cached(name: str) -> tuple[Path, dict] | None:
    reports = _cached_reports(name)
    if reports:
        return reports[0]
    # Fall back to any cached file (e.g. a signals-only export).
    files = sorted(OUTPUT_DIR.glob(COMPANIES[name]["glob"]), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            return f, _adapt_to_report(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return None


async def _run_live(query: str) -> dict | None:
    from src.agents.supervisor import run_pipeline

    state = await run_pipeline(query, scope="full", auto_mode=True)
    rep = state.get("report")
    return rep.model_dump(mode="json") if rep is not None else None


# ── Per-company evaluation ────────────────────────────────────────────────────

_EMBEDDER = None
_EMBEDDER_TRIED = False


def _embedder():
    """Lazily load the all-MiniLM embedder (same model the dedup step uses).

    Embeddings give semantically robust truth-matching across the long-paragraph
    vs short-signal wording gap. Falls back to the lexical matcher if unavailable.
    """
    global _EMBEDDER, _EMBEDDER_TRIED
    if not _EMBEDDER_TRIED:
        _EMBEDDER_TRIED = True
        try:
            from src.analysis.deduplication import get_default_embedder
            _EMBEDDER = get_default_embedder()
        except Exception as exc:
            print(f"[warn] embedder unavailable, using lexical matcher: {exc}")
            _EMBEDDER = None
    return _EMBEDDER


def evaluate_company(name: str, report: dict, truth: dict | None, limits: dict) -> dict:
    signals = report.get("risk_signals", [])
    truth_signals = (truth or {}).get("signals", [])
    meta = report.get("metadata", {})

    res: dict = {
        "company": name,
        "signals_found": len(signals),
        "positive_signals": len(report.get("positive_signals", [])),
        "signals_only": report.get("_signals_only", False),
    }
    if truth_signals:
        match = metrics.build_match(signals, truth_signals, embedder=_embedder())
        res["match_scale"] = match["scale"]
        res.update(metrics.recall_vs_truth(signals, truth_signals, match=match))
        res.update(metrics.precision_proxy(signals, truth_signals, match=match))
        res.update(metrics.severity_accuracy(signals, truth_signals, match=match))
    res["data_sufficiency"] = metrics.data_sufficiency_accuracy(
        report.get("data_sufficiency"), (truth or {}).get("data_sufficiency_tier")
    )
    res["verification"] = metrics.verification_stats(meta)
    res["dedup"] = metrics.dedup_stats(signals)
    res["faithfulness_citation"] = metrics.citation_grounding(report)
    res["guardrails"] = metrics.guardrail_compliance(
        meta, max_llm_calls=limits["max_llm_calls"], max_cost_usd=limits["max_cost_usd"]
    )
    # Graceful degradation (5.1.6): a report produced despite a failed source.
    res["graceful_degradation"] = bool(report.get("sources_failed")) and len(signals) > 0
    res["cost_usd"] = round(meta.get("estimated_cost_usd", 0.0) or 0.0, 4)
    res["latency_s"] = meta.get("latency_seconds", 0.0) or 0.0
    return res


# ── RAGAS (5.1.5) ─────────────────────────────────────────────────────────────

def ragas_metrics(report: dict, truth: dict | None, enabled: bool, question: str = "") -> dict:
    """RAGAS LLM-judge scores when --ragas is set, else the citation-grounding floor.

    The published ``ragas`` package import is broken against the installed
    langchain stack, so we run the same three metrics via the project's own
    Gemini provider + embeddings (see evaluation/ragas_eval.py). Any judge error
    falls back to the deterministic citation-grounding floor for that metric.
    """
    cite = metrics.citation_grounding(report)
    base = {
        "faithfulness": cite["grounded"],  # citation-grounding proxy
        "context_precision": None,
        "answer_relevancy": None,
        "tool_call_accuracy": None,
        "source": "citation-grounding (heuristic floor)",
    }
    if not enabled or not report.get("risk_signals"):
        return base
    try:
        from evaluation import ragas_eval
        from src.llm.gemini import GeminiProvider
        embedder = _embedder()
        if embedder is None:
            base["source"] = "embedder unavailable — heuristic floor used"
            return base
        q = question or f"What are the material due-diligence risks for this company?"
        scores = ragas_eval.evaluate_ragas(report, truth, q, embedder, GeminiProvider())
        # Keep the citation-grounding floor if the LLM judge couldn't score faithfulness.
        if scores.get("faithfulness") is None:
            scores["faithfulness"] = cite["grounded"]
        return scores
    except Exception as exc:
        base["source"] = f"ragas judge failed ({exc}) — heuristic floor used"
        return base


# ── Aggregation + reporting ───────────────────────────────────────────────────

def _fmt(x, pct=False):
    if x is None:
        return "n/a"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if pct:
        return f"{x*100:.0f}%"
    return f"{x:.2f}" if isinstance(x, float) else str(x)


def aggregate(per_company: list[dict]) -> dict:
    recalls = [c["recall"] for c in per_company if c.get("recall") is not None]
    sev = [c["severity_exact"] for c in per_company if c.get("severity_exact") is not None]
    rej = [c["verification"]["rejection_rate"] for c in per_company]
    costs = [c["cost_usd"] for c in per_company]
    lat = metrics.latency_percentiles([c["latency_s"] for c in per_company])
    suff_exact = [c["data_sufficiency"]["exact"] for c in per_company if c["data_sufficiency"]["exact"] is not None]
    return {
        "companies_evaluated": len(per_company),
        "mean_recall": (sum(recalls) / len(recalls)) if recalls else None,
        "mean_severity_exact": (sum(sev) / len(sev)) if sev else None,
        "mean_rejection_rate": (sum(rej) / len(rej)) if rej else None,
        "data_sufficiency_exact_rate": (sum(suff_exact) / len(suff_exact)) if suff_exact else None,
        "avg_cost_usd": (sum(costs) / len(costs)) if costs else None,
        "latency": lat,
        "guardrail_compliance_rate": (
            sum(1 for c in per_company if c["guardrails"]["compliant"]) / len(per_company)
        ) if per_company else None,
        "graceful_degradation_observed": any(c["graceful_degradation"] for c in per_company),
    }


def print_report(per_company, agg, consistency_res, ragas_res, source_note):
    print("\n" + "=" * 78)
    print("  DUE DILIGENCE PLATFORM — EVALUATION RESULTS (Task 5.1)")
    print("  " + source_note)
    print("=" * 78)

    print("\nPer-company (5.1.1 / 5.1.2):")
    hdr = f"{'Company':<10}{'Sigs':>5}{'Recall':>8}{'PrecProxy':>10}{'SevExact':>9}{'Suff':>14}{'Reject':>8}{'Cost$':>8}"
    print(hdr)
    print("-" * len(hdr))
    for c in per_company:
        suff = c["data_sufficiency"]
        suff_str = f"{_fmt(suff['pred'])}/{_fmt(suff['truth'])}"
        print(
            f"{c['company']:<10}{c['signals_found']:>5}"
            f"{_fmt(c.get('recall'), pct=True):>8}"
            f"{_fmt(c.get('precision_proxy'), pct=True):>10}"
            f"{_fmt(c.get('severity_exact'), pct=True):>9}"
            f"{suff_str:>14}"
            f"{_fmt(c['verification']['rejection_rate'], pct=True):>8}"
            f"{c['cost_usd']:>8.3f}"
        )

    print("\nAggregate (5.1.2):")
    print(f"  mean recall vs baseline ......... {_fmt(agg['mean_recall'], pct=True)}")
    print(f"  mean severity exact-match ....... {_fmt(agg['mean_severity_exact'], pct=True)}")
    print(f"  data-sufficiency exact-match .... {_fmt(agg['data_sufficiency_exact_rate'], pct=True)}")
    print(f"  mean verification rejection ..... {_fmt(agg['mean_rejection_rate'], pct=True)}")
    print(f"  avg cost / run .................. ${_fmt(agg['avg_cost_usd'])}")
    print(f"  latency p50 / p95 ............... {agg['latency']['p50']}s / {agg['latency']['p95']}s")

    print("\nConsistency (5.1.4, target >= 0.80 Jaccard):")
    if consistency_res.get("mean_jaccard") is not None:
        verdict = "PASS" if consistency_res["mean_jaccard"] >= 0.80 else "BELOW TARGET"
        print(f"  Boeing x{consistency_res['runs']} runs: mean Jaccard "
              f"{consistency_res['mean_jaccard']:.2f} (min {consistency_res['min_jaccard']:.2f}) -> {verdict}")
        print(f"  pairwise: {consistency_res['pairwise']}")
        if consistency_res.get("caveat"):
            print(f"  NOTE: {consistency_res['caveat']}")
    else:
        print("  n/a (need >= 2 runs; use --consistency N --live or more cached Boeing runs)")

    print("\nRAGAS / Faithfulness (5.1.5):")
    print(f"  faithfulness (citation-grounded) {_fmt(ragas_res['faithfulness'], pct=True)}")
    print(f"  context_precision ............... {_fmt(ragas_res['context_precision'], pct=True)}")
    print(f"  answer_relevancy ................ {_fmt(ragas_res['answer_relevancy'], pct=True)}")
    print(f"  tool_call_accuracy .............. {_fmt(ragas_res['tool_call_accuracy'], pct=True)}")
    print(f"  source: {ragas_res['source']}")

    print("\nAgentic metrics (5.1.6):")
    print(f"  guardrail compliance ............ {_fmt(agg['guardrail_compliance_rate'], pct=True)} (target 100%)")
    print(f"  orchestration efficiency ........ 0 redundant agent calls (fixed DAG, no agent loops)")
    print(f"  graceful degradation observed ... {_fmt(agg['graceful_degradation_observed'])} "
          f"(report produced despite a failed source)")
    print(f"  agent goal accuracy ............. manual labelling required (see plan 5.1.6)")
    print("=" * 78 + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Due Diligence evaluation harness (Task 5.1)")
    ap.add_argument("--all", action="store_true", help="Evaluate all registered companies")
    ap.add_argument("--company", choices=list(COMPANIES), help="Evaluate one company")
    ap.add_argument("--live", action="store_true", help="Run the pipeline live (real API/LLM calls)")
    ap.add_argument("--consistency", type=int, default=0, metavar="N",
                    help="Run Boeing N times live for the Jaccard consistency metric")
    ap.add_argument("--ragas", action="store_true", help="Enable RAGAS metrics (needs `ragas` + LLM key)")
    args = ap.parse_args()

    if not args.all and not args.company:
        ap.error("specify --all or --company NAME")

    names = list(COMPANIES) if args.all else [args.company]

    from src.config import settings
    limits = {"max_llm_calls": settings.max_llm_calls, "max_cost_usd": settings.max_cost_usd}

    per_company: list[dict] = []
    used_live = False
    # Retain a full report (with risk_signals) for the RAGAS LLM judge — prefer a
    # live one so --ragas scores the run we just produced, not a stale cached file.
    ragas_target: tuple[dict, dict | None, str] | None = None
    for name in names:
        truth = _load_ground_truth(name)
        report = None
        if args.live:
            report = asyncio.run(_run_live(COMPANIES[name]["query"]))
            used_live = used_live or report is not None
        if report is None:
            cached = _latest_cached(name)
            if cached is None:
                print(f"[skip] {name}: no cached output and no live run. "
                      f"Run with --live (needs API keys) to generate one.")
                continue
            _, report = cached
        if ragas_target is None and report.get("risk_signals"):
            ragas_target = (report, truth, COMPANIES[name]["query"])
        per_company.append(evaluate_company(name, report, truth, limits))

    if not per_company:
        print("No companies evaluated.")
        return 1

    # Consistency (5.1.4): live N Boeing runs, else reuse cached Boeing reports.
    consistency_res = {"runs": 0, "mean_jaccard": None}
    boeing_truth = _load_ground_truth("boeing")
    boeing_ts = (boeing_truth or {}).get("signals", [])
    if args.consistency >= 2 and args.live:
        runs = [asyncio.run(_run_live("Boeing")) for _ in range(args.consistency)]
        sets = [metrics.signal_fingerprints(r.get("risk_signals", []), boeing_ts) for r in runs if r]
        consistency_res = metrics.consistency(sets)
    else:
        cached_boeing = _cached_reports("boeing")
        if len(cached_boeing) >= 2:
            sets = [metrics.signal_fingerprints(d.get("risk_signals", []), boeing_ts) for _, d in cached_boeing[:3]]
            consistency_res = metrics.consistency(sets)
            # Cached Boeing runs are from different development stages, not 3 runs of
            # the same final system — so this is NOT a valid 5.1.4 measurement.
            consistency_res["caveat"] = "cached runs are mixed code versions; use --consistency 3 --live"

    # RAGAS / faithfulness from the first evaluated full report (live preferred).
    if ragas_target is not None:
        r_report, r_truth, r_query = ragas_target
        r_question = f"What are the material due-diligence risks for {r_query}?"
    else:
        c = next((_latest_cached(n) for n in names if _latest_cached(n)), None)
        r_report = c[1] if c and not c[1].get("_signals_only") else {}
        r_truth, r_question = boeing_truth, ""
    ragas_res = ragas_metrics(r_report, r_truth, args.ragas, r_question)

    agg = aggregate(per_company)
    source_note = "Source: LIVE pipeline runs" if used_live else "Source: cached output/ runs (use --live for fresh runs)"
    print_report(per_company, agg, consistency_res, ragas_res, source_note)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "live" if used_live else "cached",
        "per_company": per_company,
        "aggregate": agg,
        "consistency": consistency_res,
        "ragas": ragas_res,
        "guardrail_limits": limits,
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"results_{ts}.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Results saved to {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
