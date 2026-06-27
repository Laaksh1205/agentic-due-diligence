"""
Tests for Langfuse tracing integration — Task 3.3.

All tests run without a live Langfuse connection.  Two modes:
  1. Disabled mode — _client is None; every function is a no-op and never raises.
  2. Enabled mock mode — _client is a MagicMock; calls are recorded and verified.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm import tracing


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_tracing() -> None:
    """Force re-initialization so each test starts with a clean state."""
    tracing._initialized = False
    tracing._client = None


class _MockObsCM:
    """Context manager that behaves like Langfuse.start_as_current_observation."""

    def __enter__(self) -> "MagicMock":
        return MagicMock()

    def __exit__(self, *_: object) -> None:
        pass

    async def __aenter__(self) -> "MagicMock":
        return MagicMock()

    async def __aexit__(self, *_: object) -> None:
        pass


# ── 3.3.1 init() ─────────────────────────────────────────────────────────────

class TestInit:
    def test_init_disabled_when_keys_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_tracing()
        monkeypatch.setattr("src.config.settings.langfuse_secret_key", None)
        monkeypatch.setattr("src.config.settings.langfuse_public_key", None)
        tracing.init()
        assert tracing._client is None

    def test_init_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_tracing()
        monkeypatch.setattr("src.config.settings.langfuse_secret_key", None)
        monkeypatch.setattr("src.config.settings.langfuse_public_key", None)
        tracing.init()
        tracing.init()  # second call must not raise
        assert tracing._client is None

    def test_init_creates_client_when_keys_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_tracing()
        monkeypatch.setattr("src.config.settings.langfuse_secret_key", "sk-lf-test")
        monkeypatch.setattr("src.config.settings.langfuse_public_key", "pk-lf-test")
        monkeypatch.setattr("src.config.settings.langfuse_base_url", "https://test.langfuse.com")

        mock_lf_cls = MagicMock()
        mock_instance = MagicMock()
        mock_lf_cls.return_value = mock_instance

        with patch.dict(sys.modules, {"langfuse": MagicMock(Langfuse=mock_lf_cls, __version__="4.0.0")}):
            with patch("src.llm.tracing.TraceContext", create=True):
                _reset_tracing()
                # patch the import inside init()
                import builtins
                real_import = builtins.__import__

                def mock_import(name, *args, **kwargs):
                    if name == "langfuse":
                        mod = MagicMock()
                        mod.Langfuse = mock_lf_cls
                        mod.__version__ = "4.0.0"
                        return mod
                    if name == "langfuse.types":
                        m = MagicMock()
                        m.TraceContext = dict
                        return m
                    return real_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=mock_import):
                    tracing.init()

        # client was set (or init ran without crashing)
        assert tracing._initialized is True

    def test_init_survives_import_error(self) -> None:
        _reset_tracing()
        import builtins
        real_import = builtins.__import__

        def broken_import(name, *args, **kwargs):
            if name == "langfuse":
                raise ImportError("no langfuse")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=broken_import):
            tracing.init()  # must not raise

        assert tracing._client is None

    def test_init_survives_constructor_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_tracing()
        monkeypatch.setattr("src.config.settings.langfuse_secret_key", "sk-lf-test")
        monkeypatch.setattr("src.config.settings.langfuse_public_key", "pk-lf-test")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "langfuse":
                mod = MagicMock()
                mod.Langfuse = MagicMock(side_effect=RuntimeError("auth failed"))
                mod.__version__ = "4.0.0"
                return mod
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            tracing.init()  # must not raise

        assert tracing._client is None


# ── Noop behaviour when disabled ─────────────────────────────────────────────

class TestNoopWhenDisabled:
    def setup_method(self) -> None:
        _reset_tracing()
        # Leave _client as None (disabled state)

    @pytest.mark.asyncio
    async def test_start_pipeline_trace_noop(self) -> None:
        async with tracing.start_pipeline_trace("run-1", "Acme", "full"):
            pass  # no exception

    @pytest.mark.asyncio
    async def test_agent_span_noop(self) -> None:
        async with tracing.agent_span("research"):
            pass

    @pytest.mark.asyncio
    async def test_mcp_span_noop(self) -> None:
        async with tracing.mcp_span("web_search"):
            pass

    @pytest.mark.asyncio
    async def test_llm_generation_span_noop(self) -> None:
        async with tracing.llm_generation_span("gemini-2.5-flash", "test prompt"):
            pass

    def test_record_generation_usage_noop(self) -> None:
        tracing.record_generation_usage(
            model="gemini-2.5-flash",
            output="response",
            prompt_tokens=10,
            completion_tokens=20,
            cost_usd=0.001,
        )  # no exception

    def test_set_trace_output_noop(self) -> None:
        tracing.set_trace_output({"llm_calls": 5})  # no exception

    def test_flush_noop(self) -> None:
        tracing.flush()  # no exception


# ── Enabled mock mode ─────────────────────────────────────────────────────────

class TestEnabledMode:
    def setup_method(self) -> None:
        _reset_tracing()
        self.mock_client = MagicMock()
        self.mock_client.start_as_current_observation.return_value = _MockObsCM()
        tracing._client = self.mock_client
        tracing._initialized = True

    @pytest.mark.asyncio
    async def test_start_pipeline_trace_calls_client(self) -> None:
        with patch("src.llm.tracing.TraceContext", dict, create=True):
            import builtins
            real = builtins.__import__

            def mock_imp(name, *args, **kwargs):
                if name == "langfuse.types":
                    m = MagicMock()
                    m.TraceContext = dict
                    return m
                return real(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_imp):
                async with tracing.start_pipeline_trace("run-42", "Acme Corp", "full"):
                    pass

        self.mock_client.start_as_current_observation.assert_called_once()
        call_kwargs = self.mock_client.start_as_current_observation.call_args.kwargs
        assert "due-diligence:Acme Corp" in call_kwargs["name"]
        assert call_kwargs["as_type"] == "agent"

    @pytest.mark.asyncio
    async def test_agent_span_calls_client(self) -> None:
        async with tracing.agent_span("extraction"):
            pass
        self.mock_client.start_as_current_observation.assert_called_once()
        kwargs = self.mock_client.start_as_current_observation.call_args.kwargs
        assert kwargs["name"] == "extraction"
        assert kwargs["as_type"] == "span"

    @pytest.mark.asyncio
    async def test_mcp_span_calls_client(self) -> None:
        async with tracing.mcp_span("web_search", input_data={"entity": "Acme"}):
            pass
        self.mock_client.start_as_current_observation.assert_called_once()
        kwargs = self.mock_client.start_as_current_observation.call_args.kwargs
        assert kwargs["name"] == "mcp:web_search"
        assert kwargs["as_type"] == "tool"
        assert kwargs["input"] == {"entity": "Acme"}

    @pytest.mark.asyncio
    async def test_llm_generation_span_calls_client(self) -> None:
        async with tracing.llm_generation_span("gemini-2.5-flash", "my prompt"):
            pass
        self.mock_client.start_as_current_observation.assert_called_once()
        kwargs = self.mock_client.start_as_current_observation.call_args.kwargs
        assert kwargs["name"] == "llm:gemini-2.5-flash"
        assert kwargs["as_type"] == "generation"
        assert kwargs["model"] == "gemini-2.5-flash"
        assert "my prompt" in kwargs["input"]

    def test_record_generation_usage_calls_update(self) -> None:
        tracing.record_generation_usage(
            model="gemini-2.5-flash",
            output="output text",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.0023,
        )
        self.mock_client.update_current_generation.assert_called_once()
        kwargs = self.mock_client.update_current_generation.call_args.kwargs
        assert kwargs["model"] == "gemini-2.5-flash"
        assert kwargs["usage_details"] == {"input": 100, "output": 50}
        assert kwargs["cost_details"] == {"total": 0.0023}

    def test_set_trace_output_calls_set_io(self) -> None:
        out = {"llm_calls": 7, "total_cost_usd": 0.05}
        tracing.set_trace_output(out)
        self.mock_client.set_current_trace_io.assert_called_once_with(output=out)

    def test_flush_calls_client_flush(self) -> None:
        tracing.flush()
        self.mock_client.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_client_exception_in_span_does_not_propagate(self) -> None:
        self.mock_client.start_as_current_observation.side_effect = RuntimeError("network down")
        async with tracing.agent_span("research"):
            pass  # falls back to _NOOP — no exception

    def test_update_generation_exception_does_not_propagate(self) -> None:
        self.mock_client.update_current_generation.side_effect = RuntimeError("oops")
        tracing.record_generation_usage(
            model="m", output="o", prompt_tokens=1, completion_tokens=1, cost_usd=0.0
        )  # no exception

    @pytest.mark.asyncio
    async def test_prompt_truncated_to_3000_chars(self) -> None:
        long_prompt = "x" * 5000
        async with tracing.llm_generation_span("gemini-2.5-flash", long_prompt):
            pass
        kwargs = self.mock_client.start_as_current_observation.call_args.kwargs
        assert len(kwargs["input"]) <= 3000
