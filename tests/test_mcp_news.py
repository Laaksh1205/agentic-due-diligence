"""
Task 5.6 — Tests for the News API MCP server.

Unit tests mock _request so no network access is needed.
Error-handling tests mock httpx.AsyncClient directly to verify retry/backoff.
Integration tests call the live NewsAPI.org API (requires NEWS_API_KEY).

Run unit tests only:
    pytest tests/test_mcp_news.py -v -m "not integration"
Run live tests:
    pytest tests/test_mcp_news.py -v -m integration
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import settings
from src.mcp_servers.news.server import _request, search_news


# ── Shared fixtures ───────────────────────────────────────────────────────────

_NEWS_OK = {
    "status": "ok",
    "totalResults": 2,
    "articles": [
        {
            "source": {"id": "reuters", "name": "Reuters"},
            "author": "Jane Doe",
            "title": "Acme Corp faces antitrust probe",
            "description": "Regulators have opened an investigation into Acme Corp.",
            "url": "https://reuters.com/acme-probe",
            "publishedAt": "2026-06-20T14:03:00Z",
            "content": "Acme Corp is under investigation for anti-competitive practices...",
        },
        {
            "source": {"id": None, "name": "Bloomberg"},
            "author": None,
            "title": "Acme Corp settles lawsuit for $50M",
            "description": "The company agreed to a settlement on Friday.",
            "url": "https://bloomberg.com/acme-settle",
            "publishedAt": "2026-06-18T09:00:00Z",
            "content": "Acme Corp will pay $50 million to settle a class-action suit...",
        },
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_http_response(status_code: int, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or (json.dumps(json_data) if json_data else "")
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    return resp


def _mock_http_client(response) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response)
    return client


# ── search_news ────────────────────────────────────────────────────────────────

class TestSearchNews:
    async def test_returns_normalised_articles(self):
        with patch(
            "src.mcp_servers.news.server._request",
            new_callable=AsyncMock,
            return_value=_NEWS_OK,
        ):
            result = await search_news("Acme Corp")

        assert "error" not in result
        assert result["query"] == "Acme Corp"
        assert result["total_results"] == 2
        assert result["returned"] == 2
        first = result["articles"][0]
        assert first["title"] == "Acme Corp faces antitrust probe"
        assert first["source"] == "Reuters"
        assert first["url"] == "https://reuters.com/acme-probe"
        assert first["published_at"] == "2026-06-20T14:03:00Z"

    async def test_empty_query_rejected_without_request(self):
        req_mock = AsyncMock()
        with patch("src.mcp_servers.news.server._request", req_mock):
            result = await search_news("   ")

        assert result["error"] == "bad_request"
        req_mock.assert_not_called()

    async def test_query_and_filters_passed_to_request(self):
        req_mock = AsyncMock(return_value=_NEWS_OK)
        with patch("src.mcp_servers.news.server._request", req_mock):
            await search_news("Tesla", from_date="2026-06-01", to_date="2026-06-27", language="en")

        endpoint, params = req_mock.call_args[0]
        assert endpoint == "/everything"
        assert params["q"] == "Tesla"
        assert params["from"] == "2026-06-01"
        assert params["to"] == "2026-06-27"
        assert params["language"] == "en"
        assert params["sortBy"] == "publishedAt"

    async def test_pagesize_capped_at_max_docs_per_source(self):
        req_mock = AsyncMock(return_value=_NEWS_OK)
        with patch("src.mcp_servers.news.server._request", req_mock):
            await search_news("Tesla")

        params = req_mock.call_args[0][1]
        assert params["pageSize"] == min(settings.max_docs_per_source, 100)

    async def test_request_error_propagates(self):
        with patch(
            "src.mcp_servers.news.server._request",
            new_callable=AsyncMock,
            return_value={"error": "rate_limited", "message": "slow down"},
        ):
            result = await search_news("Acme Corp")

        assert result["error"] == "rate_limited"

    async def test_status_error_in_200_body_becomes_error(self):
        """NewsAPI returns status='error' with a 200 in some cases."""
        with patch(
            "src.mcp_servers.news.server._request",
            new_callable=AsyncMock,
            return_value={"status": "error", "code": "parameterInvalid", "message": "bad param"},
        ):
            result = await search_news("Acme Corp")

        assert result["error"] == "parameterInvalid"
        assert result["message"] == "bad param"

    async def test_no_articles_returns_empty_list(self):
        with patch(
            "src.mcp_servers.news.server._request",
            new_callable=AsyncMock,
            return_value={"status": "ok", "totalResults": 0, "articles": []},
        ):
            result = await search_news("Nonexistent Co")

        assert result["returned"] == 0
        assert result["articles"] == []


# ── _request error handling / backoff ─────────────────────────────────────────

class TestRequestErrorHandling:
    def _with_key(self):
        return patch.object(settings, "news_api_key", "test-key")

    async def test_missing_key_returns_error_without_network(self):
        client = _mock_http_client(_mock_http_response(200, json_data=_NEWS_OK))
        with patch.object(settings, "news_api_key", None), \
             patch("httpx.AsyncClient", return_value=client):
            result = await _request("/everything", {"q": "Tesla"})

        assert result["error"] == "missing_key"
        client.get.assert_not_called()

    async def test_success_returns_json_no_sleep(self):
        resp = _mock_http_response(200, json_data=_NEWS_OK)
        client = _mock_http_client(resp)
        with self._with_key(), \
             patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _request("/everything", {"q": "Tesla"})

        assert result["status"] == "ok"
        sleep_mock.assert_not_called()

    async def test_api_key_sent_in_header(self):
        resp = _mock_http_response(200, json_data=_NEWS_OK)
        client = _mock_http_client(resp)
        with self._with_key(), patch("httpx.AsyncClient", return_value=client):
            await _request("/everything", {"q": "Tesla"})

        headers = client.get.call_args.kwargs["headers"]
        assert headers["X-Api-Key"] == "test-key"

    async def test_429_retries_with_exponential_backoff(self):
        resp = _mock_http_response(429, text="Too Many Requests")
        client = _mock_http_client(resp)
        sleep_calls: list[float] = []

        async def fake_sleep(s: float) -> None:
            sleep_calls.append(s)

        with self._with_key(), \
             patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            result = await _request("/everything", {"q": "Tesla"})

        assert result["error"] == "rate_limited"
        assert sleep_calls == [1, 2]  # 3 attempts → 2 sleeps

    async def test_401_returns_unauthorized_immediately(self):
        resp = _mock_http_response(401, text="Unauthorized")
        client = _mock_http_client(resp)
        with self._with_key(), \
             patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _request("/everything", {"q": "Tesla"})

        assert result["error"] == "unauthorized"
        sleep_mock.assert_not_called()

    async def test_426_returns_upgrade_required(self):
        resp = _mock_http_response(426, text="Upgrade Required")
        client = _mock_http_client(resp)
        with self._with_key(), patch("httpx.AsyncClient", return_value=client):
            result = await _request("/everything", {"q": "Tesla", "from": "2020-01-01"})

        assert result["error"] == "upgrade_required"

    async def test_timeout_retries_then_fails(self):
        client = _mock_http_client(None)
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with self._with_key(), \
             patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _request("/everything", {"q": "Tesla"})

        assert result["error"] == "timeout"

    async def test_unexpected_exception_returns_request_failed(self):
        client = _mock_http_client(None)
        client.get = AsyncMock(side_effect=RuntimeError("connection reset"))
        with self._with_key(), patch("httpx.AsyncClient", return_value=client):
            result = await _request("/everything", {"q": "Tesla"})

        assert result["error"] == "request_failed"
        assert "connection reset" in result["message"]


# ── Integration tests (live NewsAPI.org) ───────────────────────────────────────

@pytest.mark.integration
class TestNewsIntegration:
    """Live tests against the real NewsAPI.org API. Requires NEWS_API_KEY in .env.
    Free tier: 100 requests/day — keep these few."""

    async def test_search_returns_articles(self):
        if not settings.news_api_key:
            pytest.skip("NEWS_API_KEY not configured")
        result = await search_news("Microsoft", language="en")
        assert "error" not in result, result
        assert result["returned"] > 0
        assert result["articles"][0]["url"].startswith("http")
        assert result["articles"][0]["title"]
