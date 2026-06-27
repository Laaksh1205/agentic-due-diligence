"""
LangGraph Supervisor — Task 1.8

AgentState TypedDict, StateGraph with 8 nodes, guardrail-protected edges,
and the public run_pipeline() entry point called by src/main.py.

Pipeline:
    entity_resolution → research → data_sufficiency_check
    → (guardrail) → extraction
    → (guardrail) → risk_analysis
    → (guardrail) → hitl_gate
    → synthesis
    → presentation → END
"""

import logging
import time
import uuid
from typing import Callable, Optional

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from src.agents.data_sufficiency import assess_sufficiency
from src.agents.research_agent import research_agent_node
from src.config import settings
from src.llm import tracing
from src.models.documents import DataSufficiency, RawDocument
from src.models.entities import ResolvedEntity
from src.models.report import DueDiligenceReport
from src.models.signals import RiskSignal
from src.resolution.entity_resolver import EntityNotFoundError, EntityResolver
from src.storage.database import audit, init_db, save_report

logger = logging.getLogger(__name__)


# ── 1.8.1 AgentState ─────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Pipeline identity
    run_id: str
    entity_name: str           # raw CLI input
    scope: str                 # full / financial / compliance
    auto_mode: bool            # True → skip HITL
    hitl_timeout: int          # seconds before HITL review auto-proceeds (PENDING_REVIEW)
    start_time: float          # time.monotonic() at pipeline start
    registry_id: str           # chosen registry candidate id (§8c picker); "" = autonomous
    jurisdiction: str          # optional registry jurisdiction filter; "" = any

    # Stage outputs
    resolved_entity: Optional[ResolvedEntity]
    documents: list[RawDocument]
    data_sufficiency: Optional[DataSufficiency]
    raw_signals: list[RiskSignal]
    scored_signals: list[RiskSignal]
    report: Optional[DueDiligenceReport]

    # Tracking
    sources_consulted: list[str]
    sources_failed: list[str]
    llm_call_count: int
    total_cost: float
    errors: list[str]


# ── 1.8.3 Guardrail helper ───────────────────────────────────────────────────

def _guardrail_exceeded(state: AgentState) -> bool:
    """Return True if any hard limit has been reached."""
    if state.get("llm_call_count", 0) >= settings.max_llm_calls:
        logger.warning(
            "GUARDRAIL: llm_call_count=%d >= max=%d",
            state["llm_call_count"], settings.max_llm_calls,
        )
        return True
    if state.get("total_cost", 0.0) >= settings.max_cost_usd:
        logger.warning(
            "GUARDRAIL: total_cost=%.4f >= max_cost=%.2f",
            state["total_cost"], settings.max_cost_usd,
        )
        return True
    elapsed = time.monotonic() - state.get("start_time", time.monotonic())
    if elapsed >= settings.max_wall_clock_seconds:
        logger.warning(
            "GUARDRAIL: wall_clock=%.0fs >= max=%ds",
            elapsed, settings.max_wall_clock_seconds,
        )
        return True
    return False


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def _node_entity_resolution(state: AgentState) -> dict:
    """Resolve raw company name → ResolvedEntity via EntityResolver."""
    logger.info("[entity_resolution] Resolving '%s'", state["entity_name"])
    try:
        resolver = EntityResolver()
        entity = await resolver.resolve(
            state["entity_name"],
            registry_id=state.get("registry_id", "") or "",
            jurisdiction=state.get("jurisdiction", "") or "",
        )
        logger.info("[entity_resolution] → %s (public=%s)", entity.canonical_name, entity.is_public)
        await audit("entity_resolved", entity.canonical_name, run_id=state["run_id"])
        return {"resolved_entity": entity}
    except EntityNotFoundError as exc:
        msg = f"entity_resolution: {exc}"
        logger.error("[entity_resolution] %s", exc)
        return {"errors": list(state.get("errors") or []) + [msg]}
    except Exception as exc:
        msg = f"entity_resolution: unexpected error: {exc}"
        logger.exception("[entity_resolution] %s", exc)
        return {"errors": list(state.get("errors") or []) + [msg]}


async def _node_research(state: AgentState) -> dict:
    """Gather raw documents from all MCP sources concurrently."""
    if not state.get("resolved_entity"):
        logger.warning("[research] Skipped — entity not resolved")
        return {
            "documents": list(state.get("documents") or []),
            "sources_consulted": list(state.get("sources_consulted") or []),
            "sources_failed": list(state.get("sources_failed") or []),
        }

    from src.agents.research_agent import ResearchState

    research_state: ResearchState = {
        "resolved_entity": state["resolved_entity"],
        "documents": list(state.get("documents") or []),
        "sources_consulted": list(state.get("sources_consulted") or []),
        "sources_failed": list(state.get("sources_failed") or []),
        "iteration_counts": {},
    }
    result = await research_agent_node(research_state)
    await audit(
        "research_complete",
        f"{len(result.get('documents', []))} docs | consulted={result.get('sources_consulted', [])}",
        run_id=state["run_id"],
    )
    # Return only AgentState keys (strip ResearchState-specific fields like iteration_counts)
    return {
        "documents": result.get("documents", []),
        "sources_consulted": result.get("sources_consulted", []),
        "sources_failed": result.get("sources_failed", []),
    }


async def _node_data_sufficiency_check(state: AgentState) -> dict:
    """Classify the document collection into a DataSufficiency tier."""
    docs = state.get("documents") or []
    tier = assess_sufficiency(docs)
    logger.info("[data_sufficiency] %d doc(s) → %s", len(docs), tier.value)
    await audit("data_sufficiency", tier.value, run_id=state["run_id"])
    return {"data_sufficiency": tier}


# ── Extraction / analysis / HITL / synthesis nodes ───────────────────────────

async def _node_extraction(state: AgentState) -> dict:
    """Extract verified risk signals from documents (Task 2.3, wired 3.5.1)."""
    from src.agents.extraction_agent import extraction_agent_node
    return await extraction_agent_node(state)


async def _node_risk_analysis(state: AgentState) -> dict:
    """Deduplicate + RAG-grounded severity scoring + contradictions (Task 2.7, wired 3.5.1)."""
    from src.agents.risk_analysis_agent import risk_analysis_agent_node
    return await risk_analysis_agent_node(state)


async def _node_hitl_gate(state: AgentState) -> dict:
    """HITL gate (Task 2.8) — surface CRITICAL / low-confidence signals for review.

    Auto mode leaves flagged signals at PENDING_REVIEW; interactive mode collects
    CLI verdicts (with timeout) and drops dismissed signals before synthesis.
    """
    from src.agents.hitl import run_human_review

    scored = list(state.get("scored_signals") or [])
    timeout = state.get("hitl_timeout") or settings.hitl_timeout_seconds
    updated = await run_human_review(
        scored,
        auto_mode=state.get("auto_mode", True),
        timeout=timeout,
        run_id=state.get("run_id"),
    )
    logger.info("[hitl_gate] %d signal(s) after review (auto_mode=%s)", len(updated), state.get("auto_mode", True))
    return {"scored_signals": updated}


async def _node_synthesis(state: AgentState) -> dict:
    """Generate DueDiligenceReport from scored signals (Task 3.2, wired 3.5.1)."""
    entity = state.get("resolved_entity")
    if not entity:
        return {"report": None}

    from src.agents.synthesis_agent import synthesis_agent_node as _synth
    result = await _synth(state)

    report = result.get("report")
    if report:
        await save_report(
            state["run_id"],
            entity.canonical_name,
            report.model_dump(mode="json"),
        )
    return result


async def _node_presentation(state: AgentState) -> dict:
    """Render the rich Phase-2 report to the terminal (Task 2.9)."""
    elapsed = time.monotonic() - state.get("start_time", time.monotonic())
    logger.info("[presentation] Pipeline complete in %.1fs", elapsed)

    # Rendering is a presentation concern only — the report is already synthesised
    # and persisted. A console/encoding hiccup here (e.g. a non-UTF-8 Windows
    # terminal that can't encode a glyph) must never fail an otherwise-complete run.
    try:
        from src.presentation.cli_report import render_from_state
        render_from_state(state)
    except Exception as exc:
        logger.warning("[presentation] report render skipped (%s): %s", type(exc).__name__, exc)

    errors = state.get("errors") or []
    if errors:
        logger.warning("[presentation] %d error(s): %s", len(errors), errors)

    # Must write ≥1 key; pass errors through so callers can inspect the final state.
    return {"errors": list(errors)}


# ── 1.8.2 Graph construction ─────────────────────────────────────────────────

def _traced_node(agent_name: str, fn):
    """Wrap a LangGraph node function in a Langfuse agent span (Task 3.3.3)."""
    async def _wrapper(state: AgentState) -> dict:
        async with tracing.agent_span(agent_name):
            return await fn(state)
    return _wrapper


def build_graph() -> StateGraph:
    """Construct the 8-node LangGraph StateGraph."""
    graph: StateGraph = StateGraph(AgentState)

    graph.add_node("entity_resolution", _traced_node("entity_resolution", _node_entity_resolution))
    graph.add_node("research", _traced_node("research", _node_research))
    graph.add_node("data_sufficiency_check", _traced_node("data_sufficiency_check", _node_data_sufficiency_check))
    graph.add_node("extraction", _traced_node("extraction", _node_extraction))
    graph.add_node("risk_analysis", _traced_node("risk_analysis", _node_risk_analysis))
    graph.add_node("hitl_gate", _traced_node("hitl_gate", _node_hitl_gate))
    graph.add_node("synthesis", _traced_node("synthesis", _node_synthesis))
    graph.add_node("presentation", _traced_node("presentation", _node_presentation))

    graph.set_entry_point("entity_resolution")

    # Direct edges (no guardrail needed for data-only nodes)
    graph.add_edge("entity_resolution", "research")
    graph.add_edge("research", "data_sufficiency_check")

    # Guardrail-protected conditional edges
    graph.add_conditional_edges(
        "data_sufficiency_check",
        lambda s: "synthesis" if _guardrail_exceeded(s) else "extraction",
        {"extraction": "extraction", "synthesis": "synthesis"},
    )
    graph.add_conditional_edges(
        "extraction",
        lambda s: "synthesis" if _guardrail_exceeded(s) else "risk_analysis",
        {"risk_analysis": "risk_analysis", "synthesis": "synthesis"},
    )
    graph.add_conditional_edges(
        "risk_analysis",
        lambda s: "synthesis" if _guardrail_exceeded(s) else "hitl_gate",
        {"hitl_gate": "hitl_gate", "synthesis": "synthesis"},
    )

    graph.add_edge("hitl_gate", "synthesis")
    graph.add_edge("synthesis", "presentation")
    graph.add_edge("presentation", END)

    return graph


def get_compiled_graph():
    """Return a compiled graph (lazily built singleton)."""
    return build_graph().compile()


async def _execute_graph(
    initial_state: AgentState,
    progress_cb: Optional[Callable[[str], None]],
) -> AgentState:
    """Run the compiled graph, returning the final state.

    With no ``progress_cb`` this is a plain ``ainvoke`` (unchanged behaviour for
    the CLI and tests). When a callback is supplied (the FastAPI backend), we
    stream node-completion updates so the web UI can show live progress. The
    AgentState channels are last-write-wins (no reducers), so accumulating each
    node's delta into the state dict reproduces the same final state ``ainvoke``
    would return.
    """
    graph = get_compiled_graph()
    if progress_cb is None:
        return await graph.ainvoke(initial_state)

    state: dict = dict(initial_state)
    async for update in graph.astream(initial_state, stream_mode="updates"):
        for node_name, delta in (update or {}).items():
            if isinstance(delta, dict):
                state.update(delta)
            try:
                progress_cb(node_name)
            except Exception:  # progress reporting must never break the pipeline
                logger.debug("[pipeline] progress_cb raised", exc_info=True)
    return state  # type: ignore[return-value]


# ── Public entry point ───────────────────────────────────────────────────────

async def run_pipeline(
    entity_name: str,
    *,
    scope: str = "full",
    auto_mode: bool = False,
    hitl_timeout: Optional[int] = None,
    run_id: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    registry_id: str = "",
    jurisdiction: str = "",
) -> AgentState:
    """Run the full due-diligence pipeline and return the final AgentState.

    ``run_id`` lets a caller (e.g. the FastAPI backend) choose the id up front so
    it can register a browser HITL provider and track the run before launch.
    ``progress_cb`` receives each pipeline node name as it completes, for live
    progress reporting.
    """
    await init_db()
    tracing.init()  # idempotent; no-op when keys absent

    run_id = run_id or str(uuid.uuid4())
    start_time = time.monotonic()

    initial_state: AgentState = {
        "run_id": run_id,
        "entity_name": entity_name,
        "scope": scope,
        "auto_mode": auto_mode,
        "hitl_timeout": hitl_timeout if hitl_timeout is not None else settings.hitl_timeout_seconds,
        "start_time": start_time,
        "registry_id": registry_id,
        "jurisdiction": jurisdiction,
        "resolved_entity": None,
        "documents": [],
        "data_sufficiency": None,
        "raw_signals": [],
        "scored_signals": [],
        "report": None,
        "sources_consulted": [],
        "sources_failed": [],
        "llm_call_count": 0,
        "total_cost": 0.0,
        "errors": [],
    }

    await audit("pipeline_start", entity_name, run_id=run_id)

    # Wrap the entire pipeline in a single Langfuse root trace (3.3.3).
    # All agent spans and LLM generations are automatically nested under it
    # via OTel context propagation through asyncio.
    try:
        async with tracing.start_pipeline_trace(run_id, entity_name, scope):
            try:
                final_state = await _execute_graph(initial_state, progress_cb)
            except Exception as exc:
                # 3.5.2 — catch unhandled graph errors; return partial state so
                # callers always get a dict (never a raw exception).
                logger.exception("[pipeline] Unhandled error in graph execution: %s", exc)
                final_state = dict(initial_state)
                final_state["errors"] = list(initial_state.get("errors") or []) + [
                    f"pipeline: {exc}"
                ]

            # Attach custom run metrics to the trace output (3.3.5).
            elapsed = time.monotonic() - start_time
            report = final_state.get("report")
            ds = final_state.get("data_sufficiency")
            # Record end-to-end latency on the report so the evaluation harness
            # (5.1.2) and any consumer can read it from report.metadata.
            if report is not None and getattr(report, "metadata", None) is not None:
                report.metadata.latency_seconds = round(elapsed, 2)
            tracing.set_trace_output({
                "total_cost_usd": round(final_state.get("total_cost", 0.0), 6),
                "llm_calls": final_state.get("llm_call_count", 0),
                "signals_extracted": report.metadata.signals_extracted if report else 0,
                "signals_rejected": report.metadata.signals_rejected if report else 0,
                "data_sufficiency": ds.value if ds else "SPARSE",
                "run_duration_seconds": round(elapsed, 1),
                "sources_consulted": final_state.get("sources_consulted", []),
                "sources_failed": final_state.get("sources_failed", []),
            })
    except Exception as exc:
        logger.warning("[pipeline] Tracing context error: %s", exc)
        if "final_state" not in dir():
            final_state = dict(initial_state)
            final_state["errors"] = [f"pipeline: {exc}"]

    tracing.flush()
    await audit("pipeline_complete", f"run_id={run_id}", run_id=run_id)

    return final_state
