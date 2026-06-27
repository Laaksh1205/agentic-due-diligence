"""
Task 1.4b.4 — Tests for the Companies House MCP server.

Unit tests mock _ch_request so no network access is needed.
Error-handling tests mock httpx.AsyncClient directly to verify retry/backoff.
Integration tests call the live Companies House API (globally accessible).

Run unit tests only:
    pytest tests/test_mcp_companies_house.py -v -m "not integration"
Run live tests:
    pytest tests/test_mcp_companies_house.py -v -m integration
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.mcp_servers.companies_house.server import (
    _ch_request,
    get_company_details,
    get_company_filings,
    get_company_officers,
    search_company,
)

# ── Shared mock data ──────────────────────────────────────────────────────────

_REVOLUT_SEARCH_ITEM = {
    "title": "REVOLUT LTD",
    "company_number": "08804411",
    "company_status": "active",
    "company_type": "ltd",
    "date_of_creation": "2013-07-16",
    "registered_office_address": {
        "address_line_1": "7 Westferry Circus",
        "locality": "London",
        "postal_code": "E14 4HD",
    },
}

_REVOLUT_PROFILE = {
    "company_name": "REVOLUT LTD",
    "company_number": "08804411",
    "company_status": "active",
    "type": "ltd",
    "date_of_creation": "2013-07-16",
    "registered_office_address": {
        "address_line_1": "7 Westferry Circus",
        "locality": "London",
        "postal_code": "E14 4HD",
    },
    "sic_codes": ["64190"],
    "accounts": {"next_due": "2025-09-30"},
    "confirmation_statement": {"next_due": "2025-07-16"},
}

_REVOLUT_OFFICER = {
    "name": "STORONSKY, Nikolay",
    "officer_role": "director",
    "appointed_on": "2013-07-16",
    "nationality": "Russian",
    "country_of_residence": "United Kingdom",
    "date_of_birth": {"month": 8, "year": 1984},
}

_REVOLUT_FILING = {
    "type": "CS01",
    "date": "2024-07-10",
    "description": "Confirmation statement made on 2024-07-10",
    "transaction_id": "MzMxODExMzI3OGFkaXF6a2N4",
}

_NOT_FOUND = {"error": "not_found", "message": "Resource not found: /company/NONEXISTENT"}
_AUTH_FAILED = {"error": "auth_failed", "message": "Invalid Companies House API key (HTTP 401)."}


# ── Helper ────────────────────────────────────────────────────────────────────

def _mock_http_response(status_code: int, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or str(json_data)
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    return resp


def _mock_http_client(response) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response)
    return client


# ── search_company unit tests ─────────────────────────────────────────────────

class TestSearchCompany:
    async def test_successful_search_returns_items(self):
        api_response = {
            "items": [_REVOLUT_SEARCH_ITEM],
            "total_results": 1,
            "items_per_page": 10,
        }
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value=api_response,
        ):
            result = await search_company("Revolut")

        assert result["total_results"] == 1
        assert result["items"][0]["company_number"] == "08804411"

    async def test_no_results_returns_empty_items(self):
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value={"items": [], "total_results": 0, "items_per_page": 10},
        ):
            result = await search_company("XYZ_FAKE_COMPANY_99999")

        assert result["total_results"] == 0
        assert result["items"] == []

    async def test_auth_failure_propagates(self):
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value=_AUTH_FAILED,
        ):
            result = await search_company("Revolut")

        assert result["error"] == "auth_failed"

    async def test_items_per_page_forwarded(self):
        req_mock = AsyncMock(return_value={"items": [], "total_results": 0, "items_per_page": 5})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await search_company("Stripe", items_per_page=5)

        _, params = req_mock.call_args[0]
        assert params["items_per_page"] == 5

    async def test_correct_endpoint_used(self):
        req_mock = AsyncMock(return_value={"items": [], "total_results": 0, "items_per_page": 10})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await search_company("Revolut")

        endpoint, _ = req_mock.call_args[0]
        assert endpoint == "/search/companies"


# ── get_company_details unit tests ────────────────────────────────────────────

class TestGetCompanyDetails:
    async def test_found_returns_profile(self):
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value=_REVOLUT_PROFILE,
        ):
            result = await get_company_details("08804411")

        assert result["company_name"] == "REVOLUT LTD"
        assert result["sic_codes"] == ["64190"]

    async def test_not_found_propagates(self):
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value=_NOT_FOUND,
        ):
            result = await get_company_details("NONEXISTENT")

        assert result["error"] == "not_found"

    async def test_company_number_uppercased_in_endpoint(self):
        req_mock = AsyncMock(return_value=_REVOLUT_PROFILE)
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_details("08804411")

        endpoint = req_mock.call_args[0][0]
        assert endpoint == "/company/08804411"

    async def test_lowercase_number_uppercased(self):
        req_mock = AsyncMock(return_value=_REVOLUT_PROFILE)
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_details("oc123456")

        endpoint = req_mock.call_args[0][0]
        assert "OC123456" in endpoint


# ── get_company_officers unit tests ───────────────────────────────────────────

class TestGetCompanyOfficers:
    async def test_returns_officers_and_counts(self):
        api_response = {
            "items": [_REVOLUT_OFFICER],
            "total_results": 1,
            "active_count": 1,
            "resigned_count": 5,
        }
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value=api_response,
        ):
            result = await get_company_officers("08804411")

        assert result["total_results"] == 1
        assert result["active_count"] == 1
        assert result["resigned_count"] == 5
        assert result["items"][0]["name"] == "STORONSKY, Nikolay"

    async def test_correct_endpoint(self):
        req_mock = AsyncMock(return_value={"items": [], "total_results": 0, "active_count": 0, "resigned_count": 0})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_officers("08804411")

        endpoint, _ = req_mock.call_args[0]
        assert endpoint == "/company/08804411/officers"

    async def test_items_per_page_forwarded(self):
        req_mock = AsyncMock(return_value={"items": [], "total_results": 0, "active_count": 0, "resigned_count": 0})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_officers("08804411", items_per_page=20)

        _, params = req_mock.call_args[0]
        assert params["items_per_page"] == 20

    async def test_not_found_propagates(self):
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value=_NOT_FOUND,
        ):
            result = await get_company_officers("NONEXISTENT")

        assert result["error"] == "not_found"

    async def test_include_resigned_true_does_not_add_register_view(self):
        req_mock = AsyncMock(return_value={"items": [], "total_results": 0, "active_count": 0, "resigned_count": 0})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_officers("08804411", include_resigned=True)

        _, params = req_mock.call_args[0]
        assert "register_view" not in params

    async def test_include_resigned_false_adds_register_view(self):
        req_mock = AsyncMock(return_value={"items": [], "total_results": 0, "active_count": 0, "resigned_count": 0})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_officers("08804411", include_resigned=False)

        _, params = req_mock.call_args[0]
        assert params.get("register_view") == "true"


# ── get_company_filings unit tests ────────────────────────────────────────────

class TestGetCompanyFilings:
    async def test_returns_filings_and_count(self):
        api_response = {
            "items": [_REVOLUT_FILING],
            "total_count": 1,
            "filing_history_status": "available",
        }
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value=api_response,
        ):
            result = await get_company_filings("08804411")

        assert result["total_count"] == 1
        assert result["items"][0]["type"] == "CS01"

    async def test_correct_endpoint(self):
        req_mock = AsyncMock(return_value={"items": [], "total_count": 0, "filing_history_status": ""})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_filings("08804411")

        endpoint, _ = req_mock.call_args[0]
        assert endpoint == "/company/08804411/filing-history"

    async def test_category_filter_forwarded_when_set(self):
        req_mock = AsyncMock(return_value={"items": [], "total_count": 0, "filing_history_status": ""})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_filings("08804411", category="accounts")

        _, params = req_mock.call_args[0]
        assert params["category"] == "accounts"

    async def test_no_category_param_when_not_set(self):
        req_mock = AsyncMock(return_value={"items": [], "total_count": 0, "filing_history_status": ""})
        with patch("src.mcp_servers.companies_house.server._ch_request", req_mock):
            await get_company_filings("08804411")

        _, params = req_mock.call_args[0]
        assert "category" not in params

    async def test_not_found_propagates(self):
        with patch(
            "src.mcp_servers.companies_house.server._ch_request",
            new_callable=AsyncMock,
            return_value=_NOT_FOUND,
        ):
            result = await get_company_filings("NONEXISTENT")

        assert result["error"] == "not_found"


# ── _ch_request error-handling / backoff tests ────────────────────────────────

class TestChRequestErrorHandling:
    async def test_429_retries_with_backoff_then_fails(self):
        resp = _mock_http_response(429, text="Too Many Requests")
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _ch_request("/search/companies", {"q": "test"})

        assert result["error"] == "rate_limited"
        assert sleep_mock.call_count == 2  # 2 sleeps for 3 attempts

    async def test_backoff_durations_are_exponential(self):
        resp = _mock_http_response(429, text="Too Many Requests")
        client = _mock_http_client(resp)
        sleep_calls = []

        async def fake_sleep(s):
            sleep_calls.append(s)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            await _ch_request("/search/companies", {"q": "test"})

        assert sleep_calls == [1, 2]

    async def test_503_retries_then_fails(self):
        resp = _mock_http_response(503, text="Service Unavailable")
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _ch_request("/search/companies", {"q": "test"})

        assert result["error"] == "rate_limited"

    async def test_401_returns_auth_failed_no_retry(self):
        resp = _mock_http_response(401, text="Unauthorized")
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _ch_request("/company/12345", None)

        assert result["error"] == "auth_failed"
        sleep_mock.assert_not_called()

    async def test_404_returns_not_found_no_retry(self):
        resp = _mock_http_response(404, text="Not Found")
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _ch_request("/company/NONEXISTENT", None)

        assert result["error"] == "not_found"
        sleep_mock.assert_not_called()

    async def test_timeout_retries_then_fails(self):
        client = _mock_http_client(None)
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _ch_request("/search/companies", {"q": "test"})

        assert result["error"] == "timeout"

    async def test_success_returns_json_no_sleep(self):
        resp = _mock_http_response(200, json_data={"items": [_REVOLUT_SEARCH_ITEM]})
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _ch_request("/search/companies", {"q": "Revolut"})

        assert "error" not in result
        sleep_mock.assert_not_called()

    async def test_unexpected_exception_returns_request_failed(self):
        client = _mock_http_client(None)
        client.get = AsyncMock(side_effect=RuntimeError("connection reset"))

        with patch("httpx.AsyncClient", return_value=client):
            result = await _ch_request("/search/companies", {"q": "test"})

        assert result["error"] == "request_failed"
        assert "connection reset" in result["message"]


# ── Integration tests (live Companies House API) ──────────────────────────────

@pytest.mark.integration
class TestCompaniesMCPIntegration:
    """Live tests against the real Companies House API.
    Globally accessible — no VPN or geo-block."""

    async def test_search_revolut_returns_results(self):
        result = await search_company("Revolut", items_per_page=5)
        assert "error" not in result
        assert result["total_results"] > 0
        numbers = [item["company_number"] for item in result["items"]]
        assert "08804411" in numbers

    async def test_get_revolut_details(self):
        result = await get_company_details("08804411")
        assert "error" not in result
        assert result.get("company_number") == "08804411"
        assert "REVOLUT" in result.get("company_name", "").upper()

    async def test_get_revolut_officers(self):
        result = await get_company_officers("08804411")
        assert "error" not in result
        assert result["total_results"] > 0
        assert len(result["items"]) > 0

    async def test_get_revolut_filings(self):
        result = await get_company_filings("08804411", category="accounts", items_per_page=5)
        assert "error" not in result
        assert result["total_count"] > 0

    async def test_nonexistent_company_returns_not_found(self):
        result = await get_company_details("00000000")
        assert result.get("error") in ("not_found", "api_error")
