"""
Task 1.4.5 — Tests for the Registry Lookup MCP server.

Unit tests mock _request (the HTTP helper) so no network access is needed.
Error-handling tests mock httpx.AsyncClient directly to verify retry / backoff.
Integration tests call the live API and run with -m integration.

Run unit tests only:
    pytest tests/test_mcp_registry_lookup.py -v -m "not integration"
Run live tests:
    pytest tests/test_mcp_registry_lookup.py -v -m integration
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.mcp_servers.registry_lookup.server import (
    _request,
    get_company_by_id,
    get_company_by_jurisdiction,
    search_company,
)

# ── Shared mock data ──────────────────────────────────────────────────────────

_STRIPE = {
    "id": "stripe_us_001",
    "legal_name": "Stripe, Inc.",
    "jurisdiction_code": "us-de",
    "company_type": "Private",
    "status": "active",
}

_REVOLUT = {
    "id": "revolut_gb_001",
    "legal_name": "REVOLUT LTD",
    "jurisdiction_code": "gb",
    "company_number": "08804411",
    "status": "active",
}

_GEO_BLOCK = {"error": "geo_blocked", "message": "Registry Lookup is not accessible from this location (HTTP 403). Use SEC EDGAR or Companies House instead."}
_RATE_LIMIT = {"error": "rate_limited", "message": "Rate limit exceeded after retries. Free tier: 1 000 calls/day, 10 req/sec. Try again in 60 s."}
_NOT_FOUND = {"error": "not_found", "message": "Company not found"}


# ── Helper: build an httpx-like mock response ─────────────────────────────────

def _mock_http_response(status_code: int, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or str(json_data)
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    return resp


def _mock_http_client(response) -> MagicMock:
    """Return a MagicMock that acts as an async context manager for httpx.AsyncClient."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response)
    return client


# ── search_company unit tests ─────────────────────────────────────────────────

class TestSearchCompany:
    async def test_successful_search_returns_results(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value={"data": [_STRIPE]},
        ):
            result = await search_company("Stripe")

        assert result["count"] == 1
        assert result["results"][0]["legal_name"] == "Stripe, Inc."

    async def test_list_response_shape_normalised(self):
        """API sometimes returns a bare list instead of {"data": [...]}."""
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value=[_STRIPE, _REVOLUT],
        ):
            result = await search_company("company")

        assert result["count"] == 2

    async def test_results_key_response_shape(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value={"results": [_STRIPE]},
        ):
            result = await search_company("Stripe")

        assert result["count"] == 1

    async def test_empty_results(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value={"data": []},
        ):
            result = await search_company("XYZ_DOES_NOT_EXIST_99999")

        assert result["count"] == 0
        assert result["results"] == []

    async def test_malformed_response_returns_empty(self):
        """Unknown response shape → empty results, not an exception."""
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value={"unexpected_key": "unexpected_value"},
        ):
            result = await search_company("Stripe")

        assert result["count"] == 0

    async def test_geo_blocked_propagates(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value=_GEO_BLOCK,
        ):
            result = await search_company("Tesla")

        assert result["error"] == "geo_blocked"

    async def test_rate_limited_propagates(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value=_RATE_LIMIT,
        ):
            result = await search_company("Apple")

        assert result["error"] == "rate_limited"

    async def test_jurisdiction_filter_forwarded(self):
        req_mock = AsyncMock(return_value={"data": [_STRIPE]})
        with patch("src.mcp_servers.registry_lookup.server._request", req_mock):
            await search_company("Stripe", jurisdiction="us-de")

        _, params = req_mock.call_args[0]
        assert params.get("jurisdiction_code") == "us-de"

    async def test_status_filter_forwarded(self):
        req_mock = AsyncMock(return_value={"data": []})
        with patch("src.mcp_servers.registry_lookup.server._request", req_mock):
            await search_company("Stripe", status="active")

        _, params = req_mock.call_args[0]
        assert params.get("status") == "active"

    async def test_no_filters_when_not_provided(self):
        req_mock = AsyncMock(return_value={"data": []})
        with patch("src.mcp_servers.registry_lookup.server._request", req_mock):
            await search_company("Stripe")

        _, params = req_mock.call_args[0]
        assert "jurisdiction_code" not in params
        assert "status" not in params


# ── get_company_by_id unit tests ──────────────────────────────────────────────

class TestGetCompanyById:
    async def test_found_returns_company_dict(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value=_STRIPE,
        ):
            result = await get_company_by_id("stripe_us_001")

        assert result["legal_name"] == "Stripe, Inc."

    async def test_not_found_error_propagates(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value=_NOT_FOUND,
        ):
            result = await get_company_by_id("nonexistent_id")

        assert result["error"] == "not_found"

    async def test_geo_block_propagates(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value=_GEO_BLOCK,
        ):
            result = await get_company_by_id("any_id")

        assert result["error"] == "geo_blocked"

    async def test_correct_endpoint_called(self):
        req_mock = AsyncMock(return_value=_STRIPE)
        with patch("src.mcp_servers.registry_lookup.server._request", req_mock):
            await get_company_by_id("stripe_us_001")

        endpoint, _ = req_mock.call_args[0]
        assert endpoint == "/companies/stripe_us_001"


# ── get_company_by_jurisdiction unit tests ────────────────────────────────────

class TestGetCompanyByJurisdiction:
    async def test_found_returns_first_result(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value={"data": [_REVOLUT]},
        ):
            result = await get_company_by_jurisdiction("gb", "08804411")

        assert result["legal_name"] == "REVOLUT LTD"

    async def test_not_found_returns_structured_error(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value={"data": []},
        ):
            result = await get_company_by_jurisdiction("us-de", "NONEXISTENT_99")

        assert result["error"] == "not_found"
        assert "NONEXISTENT_99" in result["message"]
        assert "us-de" in result["message"]

    async def test_correct_params_forwarded(self):
        req_mock = AsyncMock(return_value={"data": [_REVOLUT]})
        with patch("src.mcp_servers.registry_lookup.server._request", req_mock):
            await get_company_by_jurisdiction("gb", "08804411")

        _, params = req_mock.call_args[0]
        assert params["jurisdiction_code"] == "gb"
        assert params["registry_number"] == "08804411"
        assert params["limit"] == 1

    async def test_geo_block_propagates(self):
        with patch(
            "src.mcp_servers.registry_lookup.server._request",
            new_callable=AsyncMock,
            return_value=_GEO_BLOCK,
        ):
            result = await get_company_by_jurisdiction("us-de", "12345")

        assert result["error"] == "geo_blocked"


# ── _request error-handling / backoff tests ───────────────────────────────────

class TestRequestErrorHandling:
    async def test_rate_limit_retries_then_fails(self):
        """429 response → 3 attempts with backoff → rate_limited error."""
        resp = _mock_http_response(429, text="Too Many Requests")
        mock_client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _request("/companies/search", {"q": "test"})

        assert result["error"] == "rate_limited"
        # 3 attempts → 2 sleeps (sleep before attempt 2 and 3)
        assert sleep_mock.call_count == 2

    async def test_rate_limit_backoff_durations(self):
        """Backoff should be 1 s then 2 s (2^0, 2^1)."""
        resp = _mock_http_response(429, text="Too Many Requests")
        mock_client = _mock_http_client(resp)
        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            await _request("/companies/search", {"q": "test"})

        assert sleep_calls == [1, 2]

    async def test_timeout_retries_then_fails(self):
        """TimeoutException → retries → timeout error dict returned."""
        mock_client = _mock_http_client(None)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _request("/companies/search", {"q": "test"})

        assert result["error"] == "timeout"

    async def test_geo_block_returns_immediately_no_retry(self):
        """403 geo-block → returns immediately, no backoff sleep."""
        resp = _mock_http_response(403, text="Forbidden")
        mock_client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _request("/companies/search", {"q": "test"})

        assert result["error"] == "geo_blocked"
        sleep_mock.assert_not_called()

    async def test_cloudflare_520_treated_as_geo_block(self):
        resp = _mock_http_response(520, text="Unknown error")
        mock_client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _request("/companies/search", {"q": "test"})

        assert result["error"] == "geo_blocked"

    async def test_success_on_first_attempt_no_sleep(self):
        resp = _mock_http_response(200, json_data={"data": [_STRIPE]})
        mock_client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _request("/companies/search", {"q": "Stripe"})

        assert isinstance(result, dict)
        assert "error" not in result
        sleep_mock.assert_not_called()

    async def test_unexpected_exception_returns_request_failed(self):
        mock_client = _mock_http_client(None)
        mock_client.get = AsyncMock(side_effect=RuntimeError("unexpected"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _request("/companies/search", {"q": "test"})

        assert result["error"] == "request_failed"
        assert "unexpected" in result["message"]


# ── Integration tests (live Registry Lookup API) ──────────────────────────────

@pytest.mark.integration
class TestRegistryLookupIntegration:
    """Live tests — from non-US IPs the API returns geo_blocked, which is the
    expected graceful failure. Either a real result OR a geo_blocked / rate_limited
    error dict is acceptable; the point is no exception is raised."""

    async def test_search_stripe_graceful(self):
        """From India: geo_blocked is expected. From US: results returned."""
        result = await search_company("Stripe")
        assert "results" in result or result.get("error") in (
            "geo_blocked", "rate_limited", "timeout", "request_failed"
        )

    async def test_get_by_id_unknown_id_graceful(self):
        """Any unknown ID should return not_found or geo_blocked — never an exception."""
        result = await get_company_by_id("test_nonexistent_id_12345")
        assert "error" in result
        assert result["error"] in ("not_found", "geo_blocked", "api_error", "request_failed")
