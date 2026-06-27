# System vs. Human Analyst — Comparison (Task 5.1.3)

How the automated pipeline compares to the 30-minute manual research baseline
(`manual_baseline.md`, Phase 0), across public and private companies.

> **Basis.** The figures below are from **live runs of the current pipeline**
> (`python evaluation/run_eval.py --all --live --consistency 3`, 2026-06-26),
> scored by `run_eval.py` with semantic matching (`all-MiniLM-L6-v2`, recall
> threshold 0.45 cosine). RAGAS numbers are from a dedicated
> `--company boeing --live --ragas` run (LLM judge = Gemini).

## Headline (live)

| Company | Sigs | Recall | Prec-proxy | Severity exact | Suff (pred/truth) | Cost | Latency |
|---|---|---|---|---|---|---|---|
| Boeing (public mega-cap) | 49–62 | **75–83%** | 65–67% | 56–70% | ADEQUATE/RICH | ~$0.015–0.019 | ~5–6 min |
| Stripe (private, well-known) | 10 | **71%** | 90% | 0% | ADEQUATE/ADEQUATE | ~$0.006 | ~4.5 min |
| Chime (private fintech) | 27 | **100%** | 93% | 50% | ADEQUATE/ADEQUATE | ~$0.007 | ~4.5 min |

**Aggregate:** mean recall **82%**, mean severity-exact 35%, data-sufficiency
exact 67%, latency **p50 270s / p95 373s**, avg cost **~$0.01/run**, guardrail
compliance 100%, verification rejection 0%.

> Boeing shows a range because signal count varies run-to-run (49→62 signals,
> 75%→83% recall). That variance is real and is exactly what the consistency
> metric below measures — see the honesty note.

## Boeing — public mega-cap

| | Human (30 min) | System (live) |
|---|---|---|
| Risk signals | 12 major risks | 49–62 granular signals |
| Recall vs human baseline | — | **75–83%** (9–10 / 12 major risks) |
| Time | ~30 min | ~5–6 min |
| Cost | analyst time | ~$0.015–0.019 |

The live runs recover the DOJ fraud/guilty-plea saga, 737 MAX (MCAS), Alaska
door-plug blowout, FAA quality-system fines, whistleblower retaliation, NTSB
agreement violation, SEC settlement and safety-culture failure — and, unlike the
earlier cached run, now also surface several of the financial/operational risks
(losses, machinists' strike) that the cached run had missed. The remaining misses
are concentrated in fast-moving financial detail (credit-rating moves) and the
Starliner reputational item.

## Stripe — private, well-known

| | Human (30 min) | System (live) |
|---|---|---|
| Risk signals | 7 | 10 |
| Recall vs human baseline | — | **71%** (5 / 7 found) |
| Precision proxy | — | 90% |
| Data sufficiency | ADEQUATE | ADEQUATE (exact match) |

Stripe demonstrates the private-company value proposition: with no SEC filings,
the system still recovered 71% of the manually-found risks from web + registry
sources, at high precision.

## Chime — private fintech

The strongest live result: **100% recall** of the ground-truth risks at 93%
precision-proxy and 50% severity-exact, for ~$0.007 — the kind of fast, cheap,
high-coverage screen the platform is built for.

## RAGAS (LLM-judge, Boeing live)

| Metric | Score | Method |
|---|---|---|
| Faithfulness | **95%** | atomic-statement support vs retrieved evidence |
| Context precision | **89%** | reference-weighted precision@k of retrieved contexts |
| Answer relevancy | **58%** | cosine of LLM-generated questions vs the real question |

Faithfulness is high (claims are grounded in the gathered evidence); answer
relevancy is moderate — the executive summary covers more ground than the single
framing question, which pulls the relevancy cosine down (an expected property of
broad due-diligence summaries).

## Takeaways

- **Recall is strong and improved under live runs** (Chime 100%, Boeing 75–83%,
  Stripe 71%) — confirming the earlier cached-run weakness was research breadth
  in that specific run, not extraction quality.
- **Severity calibration is the weakest dimension** (mean 35% exact; Stripe 0% in
  the small live run) — the rubric-grounded scorer is conservative vs the human's
  CRITICAL-heavy labels. A calibration pass is the highest-value next improvement.
- **Consistency is below target** (mean Jaccard 0.53 across 3 live Boeing runs;
  target ≥ 0.80) — the signal set is not yet stable run-to-run. This is the second
  priority after severity calibration. See `README.md` / the consistency section.
- **Speed/cost dominate**: ~5 min and ~1¢ per company vs 30 analyst-minutes.
