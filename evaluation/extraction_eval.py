"""
Extraction prompt evaluation harness — Tasks 2.1.3 / 2.1.4.

Runs the extraction prompt (src/llm/prompts.py) over a labeled set of 10 diverse
documents and scores it on the two failure modes the prompt is designed to
prevent (design doc Section 9):

  * hallucinated quote anchors  -> snippet must be a VERBATIM span of the document
  * over-extraction             -> deliberate "trap" phrases (speculation,
                                   marketing fluff, routine background) must NOT
                                   be extracted as signals

precision = good_signals / total_extracted
            (a signal is "good" iff its snippet is verbatim AND it does not hit a trap)
recall    = must_find_facts_covered / total_must_find_facts

Usage:
    python evaluation/extraction_eval.py
    python evaluation/extraction_eval.py --no-examples   # ablation: drop few-shots

This is an automated proxy for the manual 30-signal review in Task 2.1.4 — it
gives a real, repeatable precision number against designed traps, which is what
the prompt-iteration loop needs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# allow `python evaluation/extraction_eval.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.gemini import GeminiProvider  # noqa: E402
from src.llm.prompts import (  # noqa: E402
    EXTRACTION_SYSTEM_PROMPT,
    ExtractionResult,
    build_extraction_prompt,
)


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


# ── Labeled corpus (10 documents) ─────────────────────────────────────────────
# must_find: lowercase key phrases a correct extraction should surface (recall).
# traps:     lowercase phrases that must NOT become signals (precision).

DOCS: list[dict] = [
    {
        "id": "01_news_recall", "entity": "Northgate Motors", "source_type": "NEWS_ARTICLE",
        "text": ("Northgate Motors recalled 120,000 vehicles on 14 March 2025 over a faulty "
                 "brake sensor linked to three reported crashes. The company said it is "
                 "passionate about customer safety above all else. Analysts noted shares could "
                 "rally if recall costs stay contained."),
        "must_find": ["recall", "brake"],
        "traps": ["passionate about customer safety", "shares could rally"],
    },
    {
        "id": "02_sec_10k", "entity": "Helix Energy", "source_type": "SEC_FILING",
        "text": ("Revenue decreased 31% to $540 million in fiscal 2024. Our independent auditors "
                 "expressed substantial doubt about our ability to continue as a going concern. "
                 "One customer accounted for 47% of revenue. The company expects conditions to "
                 "improve in 2025. We ended the year with $200 million in unrestricted cash."),
        "must_find": ["revenue decreased 31%", "going concern", "47%"],
        "traps": ["expects conditions to improve"],
    },
    {
        "id": "03_registry", "entity": "Brightwater Holdings Ltd", "source_type": "COMPANY_REGISTRY",
        "text": ("Company: BRIGHTWATER HOLDINGS LTD. Company number 07788991. Status: Active - "
                 "Proposal to Strike Off. Incorporated 4 February 2010. One outstanding charge "
                 "registered 2022. Registered office address changed on 3 January 2025."),
        "must_find": ["proposal to strike off", "outstanding charge"],
        "traps": ["registered office address changed"],
    },
    {
        "id": "04_regulatory", "entity": "Vela Pharma", "source_type": "COURT_RECORD",
        "text": ("The FDA issued a Warning Letter to Vela Pharma on 2 February 2025 for data "
                 "integrity violations at its Ohio plant. The EPA assessed a $4.1 million fine for "
                 "air-quality violations in 2024. Vela has applied for a new manufacturing license "
                 "in Texas."),
        "must_find": ["warning letter", "epa", "4.1 million"],
        "traps": ["applied for a new manufacturing license"],
    },
    {
        "id": "05_breach", "entity": "DataNimbus", "source_type": "NEWS_ARTICLE",
        "text": ("DataNimbus confirmed a data breach in October 2024 that exposed 3.2 million "
                 "customer records. A class-action suit was filed in December 2024 over the "
                 "incident. The company stated that security is its top priority and it may explore "
                 "additional safeguards."),
        "must_find": ["data breach", "3.2 million", "class-action"],
        "traps": ["security is its top priority", "may explore additional safeguards"],
    },
    {
        "id": "06_sanctions", "entity": "Orion Trading FZE", "source_type": "SANCTIONS_LIST",
        "text": ("Orion Trading FZE was added to the OFAC Specially Designated Nationals list on "
                 "9 January 2025 for sanctions evasion. A trade publication reported the firm may "
                 "also face EU restrictions, though nothing has been confirmed."),
        "must_find": ["ofac", "specially designated nationals"],
        "traps": ["may also face eu restrictions"],
    },
    {
        "id": "07_positive_site", "entity": "Verdant Foods", "source_type": "COMPANY_WEBSITE",
        "text": ("Verdant Foods became a Certified B Corporation in 2024 and achieved SOC 2 Type II "
                 "compliance the same year. We are the world's most loved food brand and our team "
                 "is simply the best on earth."),
        "must_find": ["b corporation", "soc 2"],
        "traps": ["world's most loved food brand", "simply the best on earth"],
    },
    {
        "id": "08_litigation", "entity": "Cobalt Systems", "source_type": "NEWS_ARTICLE",
        "text": ("Cobalt Systems settled a patent-infringement lawsuit for $26 million in 2023. A "
                 "separate antitrust complaint brought by a competitor remains pending as of early "
                 "2025. The CEO said the company will vigorously defend itself."),
        "must_find": ["patent", "settled", "antitrust", "pending"],
        "traps": ["will vigorously defend itself"],
    },
    {
        "id": "09_esg_labor", "entity": "Summit Apparel", "source_type": "COURT_RECORD",
        "text": ("Summit Apparel was fined $900,000 in 2024 by the Department of Labor for child-"
                 "labor violations at a subcontractor. A factory fire in 2022 injured 14 workers. "
                 "The company says it is committed to a sustainable future."),
        "must_find": ["child-labor", "fined", "factory fire"],
        "traps": ["committed to a sustainable future"],
    },
    {
        "id": "10_financial_downgrade", "entity": "Aurora Telecom", "source_type": "NEWS_ARTICLE",
        "text": ("Moody's downgraded Aurora Telecom's credit rating to B3 in November 2024, citing "
                 "a breached debt covenant. Separately, Aurora closed a $150 million Series D "
                 "funding round in 2024. One analyst set a price target of $40 on the stock."),
        "must_find": ["downgraded", "covenant", "series d"],
        "traps": ["price target of $40"],
    },
]


async def _complete_with_retry(prov: GeminiProvider, prompt: str, attempts: int = 6) -> ExtractionResult | None:
    delay = 6.0
    for i in range(attempts):
        try:
            return await prov.complete(prompt, ExtractionResult, system=EXTRACTION_SYSTEM_PROMPT, use_fast=True)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)[:70]
            print(f"    attempt {i + 1}/{attempts} failed: {msg}")
            if i < attempts - 1:
                await asyncio.sleep(delay)
                delay = min(delay * 1.6, 30.0)
    return None


def _score_doc(doc: dict, result: ExtractionResult) -> dict:
    ndoc = _norm(doc["text"])
    traps = [_norm(t) for t in doc["traps"]]
    rows, good = [], 0
    for s in result.signals:
        snip = _norm(s.source_snippet)
        verbatim = snip in ndoc
        hit_trap = any(t in snip or t in _norm(s.text) for t in traps)
        ok = verbatim and not hit_trap
        good += int(ok)
        rows.append({
            "polarity": s.signal_polarity.value, "category": s.risk_category.value,
            "severity": s.severity.value, "verbatim": verbatim, "trap": hit_trap,
            "ok": ok, "text": s.text,
        })
    covered = sum(
        1 for mf in doc["must_find"]
        if any(_norm(mf) in _norm(s.source_snippet) or _norm(mf) in _norm(s.text) for s in result.signals)
    )
    return {
        "id": doc["id"], "extracted": len(result.signals), "good": good,
        "must_find": len(doc["must_find"]), "covered": covered, "signals": rows,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-examples", action="store_true", help="ablate few-shot examples")
    args = ap.parse_args()

    prov = GeminiProvider()
    per_doc, consecutive_fail = [], 0
    for doc in DOCS:
        print(f"[{doc['id']}] {doc['entity']} ...")
        prompt = build_extraction_prompt(
            doc["text"], doc["entity"], source_type=doc["source_type"],
            include_examples=not args.no_examples,
        )
        res = await _complete_with_retry(prov, prompt)
        if res is None:
            consecutive_fail += 1
            if consecutive_fail >= 3:
                print("\nABORTED: Gemini unreachable for 3 consecutive documents "
                      "(Google Cloud asia-south2 incident / model 503). Try again later.")
                return 2
            continue
        consecutive_fail = 0
        sc = _score_doc(doc, res)
        per_doc.append(sc)
        for r in sc["signals"]:
            flag = "OK " if r["ok"] else ("HALLUC" if not r["verbatim"] else "TRAP")
            print(f"    [{flag:6}] {r['polarity']:8} {r['category']:13} {r['severity']:8} {r['text'][:58]}")

    if not per_doc:
        print("No documents scored — Gemini unavailable.")
        return 2

    tot_extracted = sum(d["extracted"] for d in per_doc)
    tot_good = sum(d["good"] for d in per_doc)
    tot_mf = sum(d["must_find"] for d in per_doc)
    tot_cov = sum(d["covered"] for d in per_doc)
    precision = tot_good / tot_extracted if tot_extracted else 0.0
    recall = tot_cov / tot_mf if tot_mf else 0.0

    print("\n" + "=" * 60)
    print(f"Docs scored      : {len(per_doc)}/{len(DOCS)}")
    print(f"Signals extracted: {tot_extracted}")
    print(f"Precision        : {precision:.1%}  ({tot_good}/{tot_extracted} good)   target >= 80%")
    print(f"Recall           : {recall:.1%}  ({tot_cov}/{tot_mf} facts)")
    print(f"LLM calls / cost : {prov.call_count} / ${prov.total_cost_usd:.5f}")
    print("=" * 60)

    out = Path(__file__).parent / "extraction_eval_results.json"
    out.write_text(json.dumps({
        "precision": precision, "recall": recall,
        "signals_extracted": tot_extracted, "good": tot_good,
        "docs_scored": len(per_doc), "per_doc": per_doc,
        "cost_usd": prov.total_cost_usd, "with_examples": not args.no_examples,
    }, indent=2), encoding="utf-8")
    print(f"Results written to {out}")
    return 0 if precision >= 0.80 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
