# Evaluation (Phase 5.1)

Automated evaluation of the due-diligence pipeline against the Phase-0 manual
ground truth.

## Run

```bash
# Score the latest cached run for each company (offline, no API calls):
python evaluation/run_eval.py --all

# Fresh live runs of the current pipeline (real LLM + network; needs .env keys):
python evaluation/run_eval.py --all --live

# One company, with the 3x consistency test (5.1.4):
python evaluation/run_eval.py --company boeing --consistency 3 --live

# Add RAGAS LLM-judge metrics (5.1.5; needs `pip install -e .[eval]` + LLM key):
python evaluation/run_eval.py --all --ragas
```

Results print to the console and save to `evaluation/results_{timestamp}.json`.

## Files

| File | Purpose |
|---|---|
| `run_eval.py` | Orchestrator (Task 5.1.7) — loads ground truth, scores reports, prints/saves the table |
| `metrics.py` | Pure, unit-tested metric functions (`tests/test_eval_metrics.py`) |
| `ground_truth/*.json` | Manually-curated expected risks per company (Phase 0) |
| `manual_baseline.md` | The 30-minute human research baseline |
| `system_vs_human.md` | System-vs-analyst comparison (Task 5.1.3) |
| `results_*.json` | Saved metric runs |

## Metrics (plan 5.1.2 / 5.1.4 / 5.1.5 / 5.1.6)

- **Recall** — fraction of ground-truth risks the system found. Matching uses
  `all-MiniLM-L6-v2` embeddings (cosine ≥ 0.45) so reworded/short signals still
  match long ground-truth paragraphs; lexical fallback when the model is absent.
- **Precision (proxy)** — share of predicted signals mapping to a known risk. A
  **lower bound** (the system legitimately finds more than the ~12-risk baseline);
  true precision needs manual labelling (`precision_from_labels`).
- **Severity / data-sufficiency accuracy** — exact + within-one-band agreement.
- **Verification rejection rate**, **dedup/corroboration**, **cost**, **latency p50/p95**.
- **Consistency (5.1.4)** — mean pairwise Jaccard of signal sets across N runs
  (target ≥ 0.80). Use `--consistency N --live`; cached-run consistency is invalid
  (mixed code versions) and labelled as such.
- **Faithfulness / RAGAS (5.1.5)** — `--ragas` runs a real LLM judge
  (`evaluation/ragas_eval.py`, Gemini + `all-MiniLM` embeddings) implementing the
  RAGAS algorithms directly: **faithfulness** (atomic-statement support vs
  retrieved evidence), **context precision** (reference-weighted precision@k) and
  **answer relevancy** (cosine of LLM-generated questions vs the question). We
  implement these natively because the published `ragas` package's import is
  broken against the installed langchain stack. Without `--ragas` (or if the judge
  errors), faithfulness falls back to a deterministic citation-grounding floor
  (no orphan `[Signal-N]` refs). Tool-call accuracy still needs manual trace
  labelling.
- **Agentic (5.1.6)** — guardrail compliance, orchestration efficiency (0 redundant
  calls — fixed DAG), graceful degradation (report produced despite a failed source),
  agent-goal accuracy (manual).

## Status / caveats

**Live headline numbers are in** (`results_*.json`, 2026-06-26): mean recall 82%
(Boeing 75–83%, Stripe 71%, Chime 100%), latency p50 270s / p95 373s, ~$0.01/run,
guardrail compliance 100%, RAGAS faithfulness 95% / context-precision 89% /
answer-relevancy 58% (Boeing live, LLM judge). See `system_vs_human.md`.

Honest open items:
- **Consistency is below target**: mean Jaccard **0.53** across 3 live Boeing runs
  (target ≥ 0.80) — the signal set is not yet stable run-to-run.
- **Severity calibration**: mean severity-exact ~35%; the scorer is conservative
  vs the human's CRITICAL-heavy labels.
- **Tool-call accuracy** (5.1.6) and **agent-goal accuracy** still require manual
  trace labelling.
- Reports persist to the SQLite store, not to `output/` JSON — so `--ragas`
  without `--live` scores the most recent cached `output/` file; use
  `--live --ragas` to judge a fresh run.
