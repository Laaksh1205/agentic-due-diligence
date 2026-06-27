"""
Tests for GeminiProvider transient-error retry (backoff on 503/429).

These exercise the retry wrapper directly with a mocked genai client — no live
API calls — so they verify the retry/backoff contract without network access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.llm.gemini import GeminiProvider, _is_retryable


class _ApiError(Exception):
    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code


# ── _is_retryable ─────────────────────────────────────────────────────────────

def test_is_retryable_by_code() -> None:
    assert _is_retryable(_ApiError(503, "service down"))
    assert _is_retryable(_ApiError(429, "too many"))
    assert _is_retryable(_ApiError(500, "internal"))


def test_is_retryable_by_message() -> None:
    assert _is_retryable(Exception("503 UNAVAILABLE high demand"))
    assert _is_retryable(Exception("RESOURCE_EXHAUSTED"))


def test_not_retryable() -> None:
    assert not _is_retryable(_ApiError(400, "INVALID_ARGUMENT"))
    assert not _is_retryable(Exception("schema validation failed"))


# ── _generate_with_retry ──────────────────────────────────────────────────────

@pytest.fixture
def provider(monkeypatch) -> GeminiProvider:
    # Avoid real sleeps so tests run instantly.
    monkeypatch.setattr("src.llm.gemini.asyncio.sleep", AsyncMock())
    return GeminiProvider()


async def test_retries_then_succeeds(provider, monkeypatch) -> None:
    monkeypatch.setattr("src.config.settings.max_agent_retries", 2)
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _ApiError(503, "503 UNAVAILABLE")
        return "OK"

    provider._client.aio.models.generate_content = flaky
    result = await provider._generate_with_retry("gemini-2.5-flash", "prompt", None)
    assert result == "OK"
    assert calls["n"] == 3  # initial + 2 retries


async def test_retries_exhausted_raises(provider, monkeypatch) -> None:
    monkeypatch.setattr("src.config.settings.max_agent_retries", 2)
    calls = {"n": 0}

    async def always_503(**kwargs):
        calls["n"] += 1
        raise _ApiError(503, "503 UNAVAILABLE")

    provider._client.aio.models.generate_content = always_503
    with pytest.raises(_ApiError):
        await provider._generate_with_retry("gemini-2.5-flash", "prompt", None)
    assert calls["n"] == 3  # initial + 2 retries, then gives up


async def test_non_retryable_raises_immediately(provider) -> None:
    calls = {"n": 0}

    async def bad_request(**kwargs):
        calls["n"] += 1
        raise _ApiError(400, "INVALID_ARGUMENT")

    provider._client.aio.models.generate_content = bad_request
    with pytest.raises(_ApiError):
        await provider._generate_with_retry("gemini-2.5-flash", "prompt", None)
    assert calls["n"] == 1  # no retry on deterministic errors
