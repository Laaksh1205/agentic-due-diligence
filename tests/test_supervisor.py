"""
Tests for src/agents/supervisor.py and src/main.py — Task 1.8

Covers:
  - AgentState has all required fields
  - Graph compiles without error
  - Guardrail routing (llm_call_count, total_cost, wall_clock)
  - Individual node functions (entity_resolution, data_sufficiency_check,
    synthesis, presentation) with mocks
  - Full pipeline run with all real nodes mocked
  - CLI argument parsing
"""

import argparse
import time
import uuid
from typing import Optional
from unittest import mock

import pytest

from src.agents.supervisor import (
    AgentState,
    _guardrail_exceeded,
    _node_data_sufficiency_check,
    _node_entity_resolution,
    _node_hitl_gate,
    _node_presentation,
    _node_research,
    _node_synthesis,
    build_graph,
    run_pipeline,
)
from src.config import settings
from src.models.documents import DataSufficiency, RawDocument
from src.models.entities import ResolvedEntity
from src.models.signals import SourceType


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))


def _make_state(**overrides) -> AgentState:
    base: AgentState = {
        "run_id": str(uuid.uuid4()),
        "entity_name": "Acme Corp",
        "scope": "full",
        "auto_mode": True,
        "start_time": time.monotonic(),
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
    base.update(overrides)
    return base


def _entity(canonical_name: str = "Acme Corp", is_public: bool = False) -> ResolvedEntity:
    return ResolvedEntity(
        canonical_name=canonical_name,
        aliases=[canonical_name, "Acme"],
        jurisdiction="us-de",
        is_public=is_public,
    )


def _doc(source_type: SourceType = SourceType.NEWS_ARTICLE) -> RawDocument:
    return RawDocument(
        source_url="https://example.com/doc",
        source_type=source_type,
        raw_text="Some raw text about Acme Corp.",
        entity_name="Acme Corp",
    )


# ── 1.8.1 AgentState schema ───────────────────────────────────────────────────

class TestAgentState:
    def test_all_required_fields_present(self):
        state = _make_state()
        required_fields = {
            "run_id", "entity_name", "scope", "auto_mode", "start_time",
            "resolved_entity", "documents", "data_sufficiency",
            "raw_signals", "scored_signals", "report",
            "sources_consulted", "sources_failed",
            "llm_call_count", "total_cost", "errors",
        }
        assert required_fields <= set(state.keys())

    def test_default_values(self):
        state = _make_state()
        assert state["documents"] == []
        assert state["raw_signals"] == []
        assert state["scored_signals"] == []
        assert state["llm_call_count"] == 0
        assert state["total_cost"] == 0.0
        assert state["errors"] == []
        assert state["resolved_entity"] is None
        assert state["report"] is None

    def test_scope_values(self):
        for scope in ("full", "financial", "compliance"):
            state = _make_state(scope=scope)
            assert state["scope"] == scope


# ── 1.8.2 Graph compilation ───────────────────────────────────────────────────

class TestGraphCompilation:
    def test_build_graph_returns_state_graph(self):
        from langgraph.graph import StateGraph
        g = build_graph()
        assert isinstance(g, StateGraph)

    def test_compiled_graph_has_invoke(self):
        compiled = build_graph().compile()
        assert callable(getattr(compiled, "ainvoke", None))

    def test_all_nodes_present(self):
        g = build_graph()
        nodes = set(g.nodes)
        expected = {
            "entity_resolution", "research", "data_sufficiency_check",
            "extraction", "risk_analysis", "hitl_gate", "synthesis", "presentation",
        }
        assert expected <= nodes


# ── 1.8.3 Guardrail logic ─────────────────────────────────────────────────────

class TestGuardrails:
    def test_no_guardrail_by_default(self):
        state = _make_state()
        assert not _guardrail_exceeded(state)

    def test_llm_call_count_at_max_triggers(self, monkeypatch):
        monkeypatch.setattr(settings, "max_llm_calls", 5)
        state = _make_state(llm_call_count=5)
        assert _guardrail_exceeded(state)

    def test_llm_call_count_below_max_ok(self, monkeypatch):
        monkeypatch.setattr(settings, "max_llm_calls", 5)
        state = _make_state(llm_call_count=4)
        assert not _guardrail_exceeded(state)

    def test_cost_at_max_triggers(self, monkeypatch):
        monkeypatch.setattr(settings, "max_cost_usd", 1.00)
        state = _make_state(total_cost=1.00)
        assert _guardrail_exceeded(state)

    def test_cost_below_max_ok(self, monkeypatch):
        monkeypatch.setattr(settings, "max_cost_usd", 1.00)
        state = _make_state(total_cost=0.99)
        assert not _guardrail_exceeded(state)

    def test_wall_clock_exceeded_triggers(self, monkeypatch):
        monkeypatch.setattr(settings, "max_wall_clock_seconds", 10)
        # start_time well in the past to simulate timeout
        state = _make_state(start_time=time.monotonic() - 20)
        assert _guardrail_exceeded(state)

    def test_wall_clock_within_limit_ok(self, monkeypatch):
        monkeypatch.setattr(settings, "max_wall_clock_seconds", 600)
        state = _make_state(start_time=time.monotonic() - 1)
        assert not _guardrail_exceeded(state)


# ── Node: entity_resolution ───────────────────────────────────────────────────

class TestNodeEntityResolution:
    async def test_successful_resolution(self):
        entity = _entity()
        state = _make_state()

        with mock.patch(
            "src.agents.supervisor.EntityResolver.resolve",
            new_callable=mock.AsyncMock,
            return_value=entity,
        ):
            result = await _node_entity_resolution(state)

        assert result["resolved_entity"] == entity
        assert "errors" not in result or result.get("errors") == []

    async def test_entity_not_found_records_error(self):
        from src.resolution.entity_resolver import EntityNotFoundError

        state = _make_state()
        with mock.patch(
            "src.agents.supervisor.EntityResolver.resolve",
            new_callable=mock.AsyncMock,
            side_effect=EntityNotFoundError("not found"),
        ):
            result = await _node_entity_resolution(state)

        assert result.get("errors")
        assert any("entity_resolution" in e for e in result["errors"])

    async def test_unexpected_exception_records_error(self):
        state = _make_state()
        with mock.patch(
            "src.agents.supervisor.EntityResolver.resolve",
            new_callable=mock.AsyncMock,
            side_effect=RuntimeError("network down"),
        ):
            result = await _node_entity_resolution(state)

        assert result.get("errors")


# ── Node: data_sufficiency_check ──────────────────────────────────────────────

class TestNodeDataSufficiencyCheck:
    async def test_empty_docs_returns_sparse(self):
        state = _make_state(documents=[])
        result = await _node_data_sufficiency_check(state)
        assert result["data_sufficiency"] == DataSufficiency.SPARSE

    async def test_rich_classification(self):
        docs = [
            _doc(SourceType.NEWS_ARTICLE),
            _doc(SourceType.NEWS_ARTICLE),
            _doc(SourceType.NEWS_ARTICLE),
            _doc(SourceType.NEWS_ARTICLE),
            _doc(SourceType.NEWS_ARTICLE),
            _doc(SourceType.COMPANY_REGISTRY),
            _doc(SourceType.COMPANY_REGISTRY),
            _doc(SourceType.COMPANY_REGISTRY),
            _doc(SourceType.COMPANY_REGISTRY),
            _doc(SourceType.SEC_FILING),
            _doc(SourceType.SEC_FILING),
            _doc(SourceType.SEC_FILING),
            _doc(SourceType.COMPANY_WEBSITE),
            _doc(SourceType.COMPANY_WEBSITE),
            _doc(SourceType.COMPANY_WEBSITE),
        ]
        state = _make_state(documents=docs)
        result = await _node_data_sufficiency_check(state)
        assert result["data_sufficiency"] == DataSufficiency.RICH


# ── Node: research (skip when no entity) ─────────────────────────────────────

class TestNodeResearch:
    async def test_skips_when_no_entity(self):
        state = _make_state(resolved_entity=None)
        result = await _node_research(state)
        # Node must write ≥1 key; skipped path returns empty lists for each field
        assert result["documents"] == []
        assert result["sources_consulted"] == []
        assert result["sources_failed"] == []

    async def test_delegates_to_research_agent_node(self):
        entity = _entity()
        state = _make_state(resolved_entity=entity)

        fake_docs = [_doc()]
        # research_agent_node is imported at supervisor module level → patch there
        mock_result = {
            "documents": fake_docs,
            "sources_consulted": ["web_search"],
            "sources_failed": [],
            "iteration_counts": {"web_search": 1},  # ResearchState field — must be stripped
        }
        with mock.patch(
            "src.agents.supervisor.research_agent_node",
            new_callable=mock.AsyncMock,
            return_value=mock_result,
        ):
            result = await _node_research(state)

        assert result["documents"] == fake_docs
        assert "web_search" in result["sources_consulted"]
        # iteration_counts must NOT appear in AgentState update
        assert "iteration_counts" not in result


# ── Node: synthesis ───────────────────────────────────────────────────────────

class TestNodeSynthesis:
    async def test_returns_empty_when_no_entity(self):
        state = _make_state(resolved_entity=None)
        result = await _node_synthesis(state)
        # Synthesis must write ≥1 key even when skipping; report stays None
        assert "report" in result
        assert result["report"] is None

    async def test_builds_stub_report_with_entity(self):
        from src.storage.database import init_db as real_init_db
        await real_init_db()

        entity = _entity()
        state = _make_state(
            resolved_entity=entity,
            data_sufficiency=DataSufficiency.ADEQUATE,
        )
        result = await _node_synthesis(state)
        assert result.get("report") is not None
        report = result["report"]
        assert report.target_entity.canonical_name == entity.canonical_name
        assert report.data_sufficiency == DataSufficiency.ADEQUATE


# ── Node: hitl_gate placeholder ───────────────────────────────────────────────

class TestNodeHitlGate:
    async def test_passes_signals_through(self):
        state = _make_state(auto_mode=True, scored_signals=[])
        result = await _node_hitl_gate(state)
        # Placeholder passes scored_signals through (LangGraph requires ≥1 key written)
        assert "scored_signals" in result
        assert result["scored_signals"] == []


# ── Node: presentation ────────────────────────────────────────────────────────

class TestNodePresentation:
    async def test_writes_errors_key(self, capsys):
        entity = _entity()
        state = _make_state(
            resolved_entity=entity,
            data_sufficiency=DataSufficiency.RICH,
            documents=[_doc()],
        )
        result = await _node_presentation(state)
        # Presentation must write ≥1 key; passes errors through
        assert "errors" in result

    async def test_prints_entity_name(self, capsys):
        entity = _entity("MegaCorp Industries")
        state = _make_state(resolved_entity=entity)
        await _node_presentation(state)
        captured = capsys.readouterr()
        assert "MegaCorp Industries" in captured.out

    async def test_handles_missing_entity(self, capsys):
        state = _make_state(resolved_entity=None)
        result = await _node_presentation(state)
        assert "errors" in result  # still writes ≥1 key
        captured = capsys.readouterr()
        assert "ERROR" in captured.out  # shows error message instead of report table

    async def test_render_failure_does_not_fail_pipeline(self):
        """A console/encoding error during render (e.g. cp1252 can't encode ⚠ on
        Windows) must be swallowed — the report is already synthesised/saved, so
        the run must still complete rather than be marked failed."""
        state = _make_state(resolved_entity=_entity(), data_sufficiency=DataSufficiency.RICH)
        boom = UnicodeEncodeError("charmap", "⚠", 0, 1, "character maps to <undefined>")
        with mock.patch(
            "src.presentation.cli_report.render_from_state", side_effect=boom
        ):
            result = await _node_presentation(state)  # must NOT raise
        assert "errors" in result
        assert state.get("errors") == result["errors"]  # no new error introduced


# ── Full pipeline (fully mocked) ──────────────────────────────────────────────

class TestRunPipeline:
    async def test_pipeline_completes_without_error(self):
        entity = _entity("Tesla Inc", is_public=True)
        from src.storage.database import init_db as real_init_db
        await real_init_db()

        # research_agent_node is at supervisor module level → patch there
        with (
            mock.patch(
                "src.agents.supervisor.EntityResolver.resolve",
                new_callable=mock.AsyncMock,
                return_value=entity,
            ),
            mock.patch(
                "src.agents.supervisor.research_agent_node",
                new_callable=mock.AsyncMock,
                return_value={
                    "documents": [_doc()],
                    "sources_consulted": ["web_search"],
                    "sources_failed": [],
                    # iteration_counts is stripped by _node_research; no need to include
                },
            ),
            # extraction / risk_analysis are now real LLM nodes (wired in 3.5.1) —
            # mock them so this "fully mocked pipeline" test makes no network calls.
            mock.patch(
                "src.agents.supervisor._node_extraction",
                new_callable=mock.AsyncMock,
                return_value={"raw_signals": []},
            ),
            mock.patch(
                "src.agents.supervisor._node_risk_analysis",
                new_callable=mock.AsyncMock,
                return_value={"scored_signals": []},
            ),
        ):
            final_state = await run_pipeline("Tesla Inc", scope="full", auto_mode=True)

        assert final_state["resolved_entity"].canonical_name == "Tesla Inc"
        assert len(final_state["documents"]) == 1
        assert final_state["data_sufficiency"] == DataSufficiency.SPARSE
        assert final_state["report"] is not None
        assert final_state["errors"] == []

    async def test_pipeline_handles_entity_not_found(self):
        from src.resolution.entity_resolver import EntityNotFoundError
        from src.storage.database import init_db as real_init_db
        await real_init_db()

        with mock.patch(
            "src.agents.supervisor.EntityResolver.resolve",
            new_callable=mock.AsyncMock,
            side_effect=EntityNotFoundError("No match"),
        ):
            final_state = await run_pipeline("Unknown XYZ Corp", auto_mode=True)

        assert final_state["errors"]
        assert final_state["resolved_entity"] is None

    async def test_guardrail_skips_extraction_when_cost_exceeded(
        self, monkeypatch
    ):
        entity = _entity()
        from src.storage.database import init_db as real_init_db
        await real_init_db()

        # Low cost limit so guardrail fires before extraction
        monkeypatch.setattr(settings, "max_cost_usd", 0.0)

        extraction_called = False

        async def fake_extraction(state):
            nonlocal extraction_called
            extraction_called = True
            return {"raw_signals": []}

        with (
            mock.patch(
                "src.agents.supervisor.EntityResolver.resolve",
                new_callable=mock.AsyncMock,
                return_value=entity,
            ),
            mock.patch(
                "src.agents.supervisor.research_agent_node",
                new_callable=mock.AsyncMock,
                return_value={
                    "documents": [],
                    "sources_consulted": [],
                    "sources_failed": [],
                },
            ),
            mock.patch(
                "src.agents.supervisor._node_extraction",
                side_effect=fake_extraction,
            ),
        ):
            await run_pipeline("Acme Corp", auto_mode=True)

        # extraction should be skipped because guardrail fired
        assert not extraction_called


# ── CLI argument parsing ──────────────────────────────────────────────────────

class TestCliParser:
    def _parse(self, *args: str) -> argparse.Namespace:
        from src.main import _build_parser
        return _build_parser().parse_args(args)

    def test_positional_entity(self):
        ns = self._parse("Tesla Inc")
        assert ns.entity == "Tesla Inc"

    def test_scope_default(self):
        ns = self._parse("Tesla Inc")
        assert ns.scope == "full"

    def test_scope_financial(self):
        ns = self._parse("Tesla Inc", "--scope", "financial")
        assert ns.scope == "financial"

    def test_scope_compliance(self):
        ns = self._parse("Tesla Inc", "--scope", "compliance")
        assert ns.scope == "compliance"

    def test_auto_flag(self):
        ns = self._parse("Tesla Inc", "--auto")
        assert ns.auto is True

    def test_auto_default_false(self):
        ns = self._parse("Tesla Inc")
        assert ns.auto is False

    def test_no_cache_flag(self):
        ns = self._parse("Tesla Inc", "--no-cache")
        assert ns.no_cache is True

    def test_verbose_flag(self):
        ns = self._parse("Tesla Inc", "--verbose")
        assert ns.verbose is True

    def test_all_flags_together(self):
        ns = self._parse("Stripe", "--scope", "compliance", "--auto", "--no-cache", "--verbose")
        assert ns.entity == "Stripe"
        assert ns.scope == "compliance"
        assert ns.auto is True
        assert ns.no_cache is True
        assert ns.verbose is True
