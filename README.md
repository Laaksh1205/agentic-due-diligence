---
title: Due Diligence Platform
emoji: 🔎
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
license: mit
---

<!-- The YAML header above is Hugging Face Space metadata (Docker SDK, app port,
     hardware). HF strips it and renders everything below as the Space page. It
     is harmless when the repo is viewed on GitHub. -->

# Agentic Due Diligence Intelligence Platform

### 🔗 Live demo — **[lp012-due-diligence-platform.hf.space](https://lp012-due-diligence-platform.hf.space)**

[![Live Demo](https://img.shields.io/badge/%F0%9F%A4%97%20Live%20Demo-Hugging%20Face%20Space-blue)](https://lp012-due-diligence-platform.hf.space)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> Hosted on the Hugging Face free tier (US region) — if it's been idle it sleeps,
> so give it ~30–60s to wake on first load.

> Autonomous multi-agent system that researches a company across **521M+ global
> entities**, extracts **quote-verified** risk signals with an LLM, and synthesizes
> a **citation-grounded** due-diligence report — in **~5 minutes for ~1¢**, not
> weeks of analyst time.

Point it at any company name. A LangGraph supervisor orchestrates eight pipeline
stages that resolve the entity, gather evidence from custom MCP data sources
(corporate registries, Companies House, SEC EDGAR, web search), extract risk
signals, verify every quote against its source, dedupe and score severity against
a RAG-grounded rubric, pause for human review on critical findings, and write a
report where **every sentence cites the signal it came from**.

---

## Architecture

![Architecture](docs/architecture.png)

The pipeline is a fixed LangGraph DAG (no agent free-for-all), wrapped in hard
guardrails (≤ 50 LLM calls, ≤ $1.00, ≤ 600 s) and a single Langfuse trace per run.
Diagram source: [`docs/architecture.mmd`](docs/architecture.mmd).

---

## Key features

- **Four custom MCP servers** — Registry Lookup (global registries), Companies
  House (UK officers/filings), SEC EDGAR (10-K/10-Q/8-K + XBRL), plus Tavily web
  search. New data sources plug in with zero agent-code changes. See
  [`docs/mcp_servers.md`](docs/mcp_servers.md).
- **Zero unverified claims** — every extracted signal carries a verbatim
  `source_snippet` that is fuzzy-matched back to the source text (`rapidfuzz`);
  anything below threshold is rejected and logged.
- **Citation-grounded synthesis** — report sentences reference `[Signal-N]` IDs;
  orphan citations are detected and flagged (faithfulness floor).
- **RAG-grounded severity** — severity is scored against a FAISS-indexed
  knowledge base (custom rubric + NIST CSF + regulatory references), not vibes.
- **Embedding deduplication** — near-duplicate signals (cosine ≥ 0.85,
  `all-MiniLM-L6-v2`) collapse into one primary + corroborating links; corroborated
  signals get a confidence boost.
- **Human-in-the-loop gate** — pauses on CRITICAL/low-confidence signals (CLI or
  browser); a configurable timeout auto-proceeds as `PENDING_REVIEW` and never
  auto-confirms a CRITICAL finding.
- **Private-company aware** — a data-sufficiency tier (RICH/ADEQUATE/LIMITED/
  SPARSE) drives an explicit caveat when public data is thin.
- **Three interfaces** — Rich CLI, FastAPI + WebSocket backend, and a Next.js 14
  dashboard (radar chart, filterable signals, browser HITL, PDF/JSON export).
- **Full observability** — every agent span, MCP call, and LLM generation is
  traced in Langfuse with per-run cost/latency metrics.

---

## Quick start

```bash
# 1. install (Python 3.11+)
pip install -e .                       # add ".[web]" for the API, ".[pdf]" for PDF export

# 2. add API keys (all have free tiers)
cp .env.example .env                   # TAVILY_API_KEY, GOOGLE_API_KEY, REGISTRY_LOOKUP_API_KEY, COMPANIES_HOUSE_API_KEY

# 3. run an assessment
ddp "Boeing" --auto --verbose          # or: python -m src.main "Stripe" --auto
```

CLI flags: `--scope {full,financial,compliance}`, `--auto` (skip HITL),
`--no-cache`, `--hitl-timeout SECONDS`, `--pdf`, `--verbose`.

### Web UI

```bash
pip install -e ".[web]" && ddp-api --reload      # backend → http://localhost:8000 (docs at /docs)
cd frontend && npm install && npm run dev        # UI → http://localhost:3000 (proxies /api + /ws)
```

The Next.js dev server proxies `/api` and `/ws` to `BACKEND_ORIGIN` (default
`http://localhost:8000`), so no CORS setup is needed. Pages: company search +
entity confirmation → live pipeline progress (WebSocket) → browser HITL review →
report dashboard (radar chart, filterable signals, strengths, sources, PDF/JSON
export) → run history.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/assess` | Launch an assessment → `{run_id}` |
| `GET` | `/api/runs/{id}` | Live status (`researching`/`extracting`/`analyzing`/`reviewing`/`complete`) |
| `GET` | `/api/runs/{id}/report` | Full due-diligence report JSON |
| `GET` | `/api/runs/{id}/signals` | Risk signals (filter by `category`/`severity`, paginated) |
| `POST` | `/api/runs/{id}/review` | Submit a HITL verdict (`confirm`/`dismiss`/`investigate`) |
| `GET` | `/api/runs/{id}/export/pdf` · `/export/json` | Download report |
| `WS` | `/ws/{id}` | Real-time progress stream |

### Run with Docker

A multi-stage build compiles the Next.js static export and serves it together
with the FastAPI backend from one slim Python image on a single origin.

```bash
cp .env.example .env                       # 1. add your API keys
docker compose up --build                  # 2. build + run → http://localhost:8000
docker compose --profile monitoring up     # 3. (optional) + self-hosted Langfuse on :3000
```

The app reads keys from `.env` (never baked into the image) and persists the
SQLite store, exports, and the embedding-model cache via named volumes. Langfuse
is optional — the project defaults to Langfuse Cloud; the `monitoring` profile
spins up a self-hosted instance on a shared network instead.

---

## How it works

1. **Entity resolution** — registry search → canonical name, jurisdiction,
   aliases, public/private, SEC CIK; cached 7 days.
2. **Research** — all MCP sources called concurrently (`asyncio.gather`,
   `return_exceptions=True`); a failed source is recorded, never fatal.
3. **Data-sufficiency check** — classifies coverage into RICH/ADEQUATE/LIMITED/
   SPARSE from doc count × source-type diversity.
4. **Extraction** — per-document structured LLM extraction into `RiskSignal`
   objects (≤ 7/doc), each quote-anchor verified; temporal decay applied.
5. **Risk analysis** — dedupe → RAG-retrieve rubric context → LLM severity score →
   contradiction detection → corroboration boost.
6. **HITL gate** — interrupt on CRITICAL/low-confidence (auto mode tags
   `PENDING_REVIEW`).
7. **Synthesis** — per-category sections + executive summary with inline
   `[Signal-N]` citations + recommended actions + category scores.
8. **Presentation** — Rich CLI / web dashboard / PDF / JSON; all persisted to
   SQLite with a full audit log.

---

## Evaluation results

Live runs of the current pipeline (2026-06-26), scored by
[`evaluation/run_eval.py`](evaluation/run_eval.py) against the Phase-0 manual
baseline (semantic matching, `all-MiniLM-L6-v2`, cosine ≥ 0.45).

| Company | Signals | Recall vs human | Precision-proxy | Severity exact | Data sufficiency | Cost | Latency |
|---|---|---|---|---|---|---|---|
| Boeing (public) | 49–62 | **75–83%** | 65–67% | 56–70% | ADEQUATE/RICH | ~$0.017 | ~5–6 min |
| Stripe (private) | 10 | **71%** | 90% | 0% | ADEQUATE | ~$0.006 | ~4.5 min |
| Chime (private) | 27 | **100%** | 93% | 50% | ADEQUATE | ~$0.007 | ~4.5 min |

**Aggregate:** mean recall **82%** · latency **p50 270 s / p95 373 s** ·
guardrail compliance **100%** · verification rejection 0%.

**RAGAS (LLM judge, Boeing live):** faithfulness **95%** · context-precision
**89%** · answer-relevancy **58%** — implemented natively against the project's
own Gemini provider + embeddings ([`evaluation/ragas_eval.py`](evaluation/ragas_eval.py)),
since the published `ragas` package's import is broken against the installed
langchain stack.

**Consistency (3 live Boeing runs):** mean Jaccard **0.53** (target ≥ 0.80).

### System vs. human analyst

| | Human (30 min/company) | System |
|---|---|---|
| Boeing | 12 major risks | 9–10 of 12 recovered + ~40 granular signals |
| Stripe | 7 risks | 5 of 7 recovered at 90% precision |
| Time | ~30 min | ~5 min |
| Cost | analyst time | ~1¢ |

Full write-up: [`evaluation/system_vs_human.md`](evaluation/system_vs_human.md).

> **Honest open items:** severity calibration is the weakest dimension (the
> rubric scorer is conservative vs. the human's CRITICAL-heavy labels), and
> run-to-run consistency (0.53) is below the 0.80 target. These are the top two
> improvement targets — documented rather than hidden.

---

## Cost analysis

| Item | Value |
|---|---|
| Cost per assessment | **~$0.006–0.02** (Gemini 2.5 Flash-Lite, structured output) |
| LLM-call guardrail | 50 calls/run hard cap (synthesis reserved) |
| Cost guardrail | $1.00/run hard cap → pipeline degrades to a partial report |
| Data sources | All free tier: Tavily (1k/mo), Registry Lookup (5k/mo), Companies House (unlimited), SEC EDGAR (free) |
| Human equivalent | ~30 analyst-minutes/company |

At ~1¢ and ~5 min per company, the platform is ~3 orders of magnitude cheaper
than manual research while preserving an auditable evidence trail.

---

## Tech stack & rationale

| Layer | Choice | Why |
|---|---|---|
| Orchestration | **LangGraph** | Explicit, inspectable state-machine DAG with built-in interrupts for HITL — not an opaque agent loop. |
| Data sources | **MCP** (custom servers) | Tool/data access is decoupled from agents; a new source is a new server, zero agent changes. |
| LLM | **Gemini 2.5 Flash-Lite** | Native structured (Pydantic) output, generous free tier, low cost/latency. The provider supports a fast/smart tier split; the evaluated config uses Flash-Lite for both. |
| Verification | **rapidfuzz** | Deterministic quote-anchor matching → no hallucinated claims survive. |
| Dedup / embeddings | **sentence-transformers** (`all-MiniLM-L6-v2`) | Local, free, fast semantic similarity for dedup + eval matching. |
| Severity grounding | **FAISS** RAG | Severity decisions grounded in an indexed rubric/NIST/regulatory KB. |
| Storage | **SQLite** (`aiosqlite`) | Zero-ops async persistence + audit log + caching. |
| Observability | **Langfuse** | Per-run trace with nested agent/MCP/LLM spans and cost metrics. |
| Backend / UI | **FastAPI + WebSocket / Next.js 14** | Live progress streaming + a portfolio-grade dashboard. |

---

## Design decisions

**Why LangGraph (not a free-form agent loop)?** Due diligence needs auditability
and bounded cost. A fixed DAG with explicit guardrail checks means 0 redundant
agent calls, deterministic orchestration, and native `interrupt()` support for
the human-in-the-loop gate — properties an autonomous ReAct loop can't guarantee.

**Why MCP (not hardcoded API clients)?** The portfolio thesis is extensibility:
each data source is an independent MCP server with typed tools. Adding the News
API (Phase 5.6) requires registering one server — no agent code changes. It also
keeps credentials and rate-limit/backoff logic isolated per source.

**Why not CrewAI / AutoGen?** Role-playing multi-agent frameworks optimize for
emergent collaboration, the opposite of what a compliance-grade pipeline wants.
Here the "agents" are deterministic graph nodes with hard budgets and verifiable
I/O contracts; CrewAI's autonomy would add cost variance and remove the
inspectable state machine that makes the output trustworthy.

**Why quote-anchor verification?** The biggest risk in LLM due diligence is
fabricated findings. Requiring a verbatim source snippet that must fuzzy-match
the fetched text turns "trust the model" into "verify against the source," and
makes the rejection rate a measurable quality signal.

---

## Project layout

```
src/            supervisor + agents, models, mcp_servers, analysis, resolution,
                verification, storage, presentation, llm
api/            FastAPI backend (REST + WebSocket + HITL bridge)
frontend/       Next.js 14 web UI (App Router, TS, Tailwind, recharts)
knowledge_base/ severity rubric, NIST CSF, regulatory reference (FAISS-indexed)
evaluation/     run_eval.py, metrics.py, ragas_eval.py, ground_truth/, results
tests/          pytest suite (unit + integration markers)
docs/           architecture diagram + MCP server docs
```

Run the suite: `pytest -q -m "not integration"`. See
[`frontend/INTEGRATION.md`](frontend/INTEGRATION.md) for the web E2E tests and
[`evaluation/README.md`](evaluation/README.md) for the evaluation harness.

---

## License

MIT © Laaksh Parikh
