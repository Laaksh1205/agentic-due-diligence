"""
Langfuse v4 tracing integration — Task 3.3.

Public API (all no-op when Langfuse is unconfigured or unavailable):
  init()                      — initialize once; idempotent
  start_pipeline_trace(...)   — async context manager for the full run
  agent_span(name)            — async context manager for one LangGraph node
  mcp_span(name)              — async context manager for one MCP tool call
  llm_generation_span(model, prompt) — async context manager for one LLM call
  record_generation_usage(...)— call inside llm_generation_span with usage data
  set_trace_output(dict)      — set output metadata on the root trace
  flush()                     — wait for all pending events to be sent

OTel context propagation is automatic in asyncio: child coroutines inherit
the active span from their parent, so nesting works without manual wiring.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_initialized = False
_client: Any = None  # Langfuse instance (None when disabled)


# ── No-op context manager (sync + async) ─────────────────────────────────────

class _NoopCM:
    """Drop-in replacement when Langfuse is disabled — works with both ``with`` and ``async with``."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        pass


_NOOP: _NoopCM = _NoopCM()


class _AsyncAdapter:
    """Wrap a sync context manager so it works with both ``with`` and ``async with``.

    Langfuse v4's _AgnosticContextManager only implements the sync protocol.
    This adapter makes it safe to use inside LangGraph async task boundaries.
    """

    __slots__ = ("_cm",)

    def __init__(self, cm: Any) -> None:
        self._cm = cm

    def __enter__(self) -> Any:
        return self._cm.__enter__()

    def __exit__(self, *args: Any) -> Any:
        return self._cm.__exit__(*args)

    async def __aenter__(self) -> Any:
        return self._cm.__enter__()

    async def __aexit__(self, *args: Any) -> Any:
        return self._cm.__exit__(*args)


# ── Initialization (3.3.1) ────────────────────────────────────────────────────

def init() -> None:
    """Initialize the Langfuse client from settings. Idempotent; never raises."""
    global _initialized, _client
    if _initialized:
        return
    _initialized = True
    try:
        from langfuse import Langfuse  # type: ignore[import-untyped]
        from src.config import settings
        sk: str = getattr(settings, "langfuse_secret_key", "") or ""
        pk: str = getattr(settings, "langfuse_public_key", "") or ""
        host: str = getattr(settings, "langfuse_base_url", "https://cloud.langfuse.com") or "https://cloud.langfuse.com"
        if not sk or not pk:
            logger.info("[tracing] Langfuse keys not configured — tracing disabled")
            return
        _client = Langfuse(secret_key=sk, public_key=pk, host=host)
        logger.info("[tracing] Langfuse v%s ready → %s", _pkg_version(), host)
    except ImportError:
        logger.info("[tracing] langfuse not installed — tracing disabled")
    except Exception as exc:
        logger.warning("[tracing] Langfuse init failed: %s", exc)


def _pkg_version() -> str:
    try:
        import langfuse  # type: ignore[import-untyped]
        return langfuse.__version__
    except Exception:
        return "?"


# ── Pipeline-level trace (3.3.3) ──────────────────────────────────────────────

def start_pipeline_trace(run_id: str, entity_name: str, scope: str) -> Any:
    """Return a context manager wrapping the full pipeline as a Langfuse root span.

    Usage::

        async with tracing.start_pipeline_trace(run_id, entity_name, scope):
            final_state = await graph.ainvoke(initial_state)
            tracing.set_trace_output({...})
    """
    if _client is None:
        return _NOOP
    try:
        from langfuse.types import TraceContext  # type: ignore[import-untyped]
        return _AsyncAdapter(_client.start_as_current_observation(
            name=f"due-diligence:{entity_name}",
            as_type="agent",
            input={"entity_name": entity_name, "scope": scope},
            trace_context=TraceContext({"trace_id": run_id}),
        ))
    except Exception as exc:
        logger.debug("[tracing] start_pipeline_trace failed: %s", exc)
        return _NOOP


# ── Agent-level spans (3.3.3) ─────────────────────────────────────────────────

def agent_span(agent_name: str, *, input_data: Any = None) -> Any:
    """Return a context manager for one LangGraph agent node.

    Usage::

        async with tracing.agent_span("research"):
            ...
    """
    if _client is None:
        return _NOOP
    try:
        return _AsyncAdapter(_client.start_as_current_observation(
            name=agent_name,
            as_type="span",
            input=input_data,
        ))
    except Exception as exc:
        logger.debug("[tracing] agent_span failed (%s): %s", agent_name, exc)
        return _NOOP


# ── MCP tool spans (3.3.4) ────────────────────────────────────────────────────

def mcp_span(source_name: str, *, input_data: Any = None) -> Any:
    """Return a context manager for one MCP source call.

    Usage::

        async with tracing.mcp_span("web_search", input_data={"entity": ...}):
            docs = await _search_web(entity)
    """
    if _client is None:
        return _NOOP
    try:
        return _AsyncAdapter(_client.start_as_current_observation(
            name=f"mcp:{source_name}",
            as_type="tool",
            input=input_data,
        ))
    except Exception as exc:
        logger.debug("[tracing] mcp_span failed (%s): %s", source_name, exc)
        return _NOOP


# ── LLM generation spans (3.3.2) ─────────────────────────────────────────────

def llm_generation_span(model_name: str, prompt: str) -> Any:
    """Return a context manager for one LLM call.

    Call ``record_generation_usage()`` inside the block after the response.

    Usage::

        async with tracing.llm_generation_span(model_name, prompt):
            response = await llm_call(...)
            tracing.record_generation_usage(model=model_name, output=..., ...)
    """
    if _client is None:
        return _NOOP
    try:
        return _AsyncAdapter(_client.start_as_current_observation(
            name=f"llm:{model_name}",
            as_type="generation",
            model=model_name,
            model_parameters={"temperature": 0.0},
            input=prompt[:3_000],
        ))
    except Exception as exc:
        logger.debug("[tracing] llm_generation_span failed: %s", exc)
        return _NOOP


def record_generation_usage(
    *,
    model: str,
    output: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> None:
    """Record model, token counts, and cost on the currently active generation span.

    Must be called inside a ``llm_generation_span`` context manager.
    """
    if _client is None:
        return
    try:
        _client.update_current_generation(
            model=model,
            output=output[:3_000],
            usage_details={"input": prompt_tokens, "output": completion_tokens},
            cost_details={"total": cost_usd},
        )
    except Exception as exc:
        logger.debug("[tracing] record_generation_usage failed: %s", exc)


# ── Trace-level metrics (3.3.5) ───────────────────────────────────────────────

def set_trace_output(output: dict) -> None:
    """Set the output payload on the current root trace.

    Call this inside ``start_pipeline_trace`` just before it exits.
    """
    if _client is None:
        return
    try:
        _client.set_current_trace_io(output=output)
    except Exception as exc:
        logger.debug("[tracing] set_trace_output failed: %s", exc)


def flush() -> None:
    """Block until all pending Langfuse events have been sent."""
    if _client is None:
        return
    try:
        _client.flush()
        logger.debug("[tracing] flushed")
    except Exception as exc:
        logger.warning("[tracing] flush failed: %s", exc)


__all__ = [
    "init",
    "start_pipeline_trace",
    "agent_span",
    "mcp_span",
    "llm_generation_span",
    "record_generation_usage",
    "set_trace_output",
    "flush",
]
