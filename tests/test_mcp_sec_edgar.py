"""
Task 3.1.5 — Tests for the SEC EDGAR MCP server.

Unit tests mock _edgar_get so no network access is needed.
Error-handling tests mock httpx.AsyncClient directly to verify retry/backoff.
Integration tests call the live EDGAR API (public, no auth required).

Run unit tests only:
    pytest tests/test_mcp_sec_edgar.py -v -m "not integration"
Run live tests:
    pytest tests/test_mcp_sec_edgar.py -v -m integration
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.mcp_servers.sec_edgar.server import (
    _edgar_get,
    _normalise_accession,
    _pad_cik,
    get_company_facts,
    get_filing_text,
    search_filings,
)


# ── Shared fixtures ───────────────────────────────────────────────────────────

_TESLA_CIK = "1318605"
_TESLA_CIK_PADDED = "0001318605"
_ACC_WITH_DASH = "0001318605-24-000080"
_ACC_NO_DASH = "000131860524000080"

_TESLA_SUBMISSIONS = {
    "cik": "1318605",
    "name": "Tesla, Inc.",
    "filings": {
        "recent": {
            "accessionNumber": [_ACC_WITH_DASH, "0001318605-24-000045"],
            "form": ["10-K", "8-K"],
            "filingDate": ["2024-01-29", "2024-01-08"],
            "reportDate": ["2023-12-31", ""],
            "primaryDocument": ["tsla20231231_10k.htm", "tsla20240108_8k.htm"],
            "primaryDocDescription": ["10-K", "8-K"],
        }
    },
}

_TESLA_FACTS = {
    "cik": "0001318605",
    "entityName": "Tesla, Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "description": "Amount of revenue recognized from goods sold and services rendered.",
                "units": {
                    "USD": [
                        {"val": 81462000000, "filed": "2023-02-01", "form": "10-K", "end": "2022-12-31"},
                        {"val": 97690000000, "filed": "2024-01-29", "form": "10-K", "end": "2023-12-31"},
                    ]
                },
            },
            "NetIncomeLoss": {
                "label": "Net Income (Loss)",
                "description": "The portion of profit or loss for the period, net of income taxes.",
                "units": {
                    "USD": [
                        {"val": 12556000000, "filed": "2024-01-29", "form": "10-K", "end": "2023-12-31"},
                    ]
                },
            },
        }
    },
}

_FILING_HTML = "<html><body><h1>Tesla 10-K</h1><p>Annual report text content here.</p></body></html>"


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


# ── _pad_cik ──────────────────────────────────────────────────────────────────

class TestPadCik:
    def test_pads_short_cik(self):
        assert _pad_cik("1318605") == "0001318605"

    def test_already_padded_unchanged(self):
        assert _pad_cik("0001318605") == "0001318605"

    def test_strips_extra_leading_zeros_then_repads(self):
        assert _pad_cik("00000001318605") == "0001318605"

    def test_integer_input(self):
        assert _pad_cik(789019) == "0000789019"


# ── _normalise_accession ─────────────────────────────────────────────────────

class TestNormaliseAccession:
    def test_with_dashes_returns_both_forms(self):
        with_dash, no_dash = _normalise_accession("0001318605-24-000080")
        assert with_dash == "0001318605-24-000080"
        assert no_dash == "000131860524000080"

    def test_without_dashes_returns_both_forms(self):
        with_dash, no_dash = _normalise_accession("000131860524000080")
        assert with_dash == "0001318605-24-000080"
        assert no_dash == "000131860524000080"

    def test_no_dash_version_is_18_chars(self):
        _, no_dash = _normalise_accession("0001318605-24-000080")
        assert len(no_dash) == 18


# ── search_filings ────────────────────────────────────────────────────────────

class TestSearchFilings:
    async def test_returns_filings_with_correct_structure(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=_TESLA_SUBMISSIONS,
        ):
            result = await search_filings(_TESLA_CIK)

        assert result["company_name"] == "Tesla, Inc."
        assert result["cik"] == _TESLA_CIK_PADDED
        assert result["total_returned"] == 2
        assert result["filings"][0]["accession_number"] == _ACC_WITH_DASH
        assert result["filings"][0]["filing_type"] == "10-K"
        assert result["filings"][0]["filing_date"] == "2024-01-29"
        assert result["filings"][0]["report_date"] == "2023-12-31"

    async def test_filing_type_filter_applies(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=_TESLA_SUBMISSIONS,
        ):
            result = await search_filings(_TESLA_CIK, filing_type="10-K")

        assert result["total_returned"] == 1
        assert result["filings"][0]["filing_type"] == "10-K"

    async def test_date_from_filter_excludes_older_filings(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=_TESLA_SUBMISSIONS,
        ):
            result = await search_filings(_TESLA_CIK, date_from="2024-01-15")

        # Only 2024-01-29 passes; 2024-01-08 is excluded
        assert result["total_returned"] == 1
        assert result["filings"][0]["filing_date"] == "2024-01-29"

    async def test_date_to_filter_excludes_newer_filings(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=_TESLA_SUBMISSIONS,
        ):
            result = await search_filings(_TESLA_CIK, date_to="2024-01-15")

        assert result["total_returned"] == 1
        assert result["filings"][0]["filing_date"] == "2024-01-08"

    async def test_cik_padded_in_request_url(self):
        get_mock = AsyncMock(return_value=_TESLA_SUBMISSIONS)
        with patch("src.mcp_servers.sec_edgar.server._edgar_get", get_mock):
            await search_filings("1318605")

        called_url = get_mock.call_args[0][0]
        assert "CIK0001318605" in called_url

    async def test_error_propagates(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value={"error": "not_found", "message": "Not found"},
        ):
            result = await search_filings("9999999999")

        assert result["error"] == "not_found"

    async def test_empty_filings_returns_zero_total(self):
        empty_sub = {
            "name": "Ghost Corp",
            "filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "reportDate": [], "primaryDocument": [], "primaryDocDescription": []}},
        }
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=empty_sub,
        ):
            result = await search_filings("0000000001")

        assert result["total_returned"] == 0
        assert result["filings"] == []

    async def test_primary_document_included_in_result(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=_TESLA_SUBMISSIONS,
        ):
            result = await search_filings(_TESLA_CIK, filing_type="10-K")

        assert result["filings"][0]["primary_document"] == "tsla20231231_10k.htm"


# ── get_filing_text ───────────────────────────────────────────────────────────

class TestGetFilingText:
    async def test_returns_plain_text_from_html(self):
        async def fake_get(url, **kwargs):
            if "submissions" in url:
                return _TESLA_SUBMISSIONS
            return _FILING_HTML

        with patch("src.mcp_servers.sec_edgar.server._edgar_get", side_effect=fake_get):
            result = await get_filing_text(_ACC_WITH_DASH)

        assert "error" not in result
        assert "Tesla 10-K" in result["text"] or "Annual report" in result["text"]
        assert result["accession_number"] == _ACC_WITH_DASH
        assert result["filing_type"] == "10-K"
        assert result["text_length"] > 0

    async def test_accepts_accession_without_dashes(self):
        async def fake_get(url, **kwargs):
            if "submissions" in url:
                return _TESLA_SUBMISSIONS
            return _FILING_HTML

        with patch("src.mcp_servers.sec_edgar.server._edgar_get", side_effect=fake_get):
            result = await get_filing_text(_ACC_NO_DASH)

        assert "error" not in result
        assert result["accession_number"] == _ACC_WITH_DASH

    async def test_error_from_submissions_falls_back_to_txt(self):
        """If submissions lookup fails, it falls back to .txt file and still tries to fetch."""
        call_count = {"n": 0}

        async def fake_get(url, **kwargs):
            call_count["n"] += 1
            if "submissions" in url:
                return {"error": "not_found", "message": "Not found"}
            # Second call is for the fallback .txt document
            return "Annual report plain text content"

        with patch("src.mcp_servers.sec_edgar.server._edgar_get", side_effect=fake_get):
            result = await get_filing_text(_ACC_WITH_DASH)

        # Should have made two calls: submissions lookup + document fetch
        assert call_count["n"] == 2
        assert "error" not in result

    async def test_document_fetch_error_propagates(self):
        async def fake_get(url, **kwargs):
            if "submissions" in url:
                return _TESLA_SUBMISSIONS
            return {"error": "not_found", "message": "Document not found"}

        with patch("src.mcp_servers.sec_edgar.server._edgar_get", side_effect=fake_get):
            result = await get_filing_text(_ACC_WITH_DASH)

        assert result["error"] == "not_found"

    async def test_source_url_uses_archives_path(self):
        async def fake_get(url, **kwargs):
            if "submissions" in url:
                return _TESLA_SUBMISSIONS
            return _FILING_HTML

        with patch("src.mcp_servers.sec_edgar.server._edgar_get", side_effect=fake_get):
            result = await get_filing_text(_ACC_WITH_DASH)

        assert "Archives/edgar/data" in result["source_url"]
        assert "1318605" in result["source_url"]

    async def test_cik_extracted_from_accession_prefix(self):
        """The CIK embedded in the accession number prefix must appear in the archive URL."""
        submissions_calls = []
        async def fake_get(url, **kwargs):
            if "submissions" in url:
                submissions_calls.append(url)
                return _TESLA_SUBMISSIONS
            return _FILING_HTML

        with patch("src.mcp_servers.sec_edgar.server._edgar_get", side_effect=fake_get):
            await get_filing_text(_ACC_WITH_DASH)

        assert len(submissions_calls) == 1
        # Submissions URL must use the 10-digit padded CIK
        assert "CIK0001318605" in submissions_calls[0]

    async def test_subject_cik_overrides_accession_prefix(self):
        """Agent-filed docs: an explicit subject CIK must drive the archive path.

        The accession prefix is the filing agent's CIK (1628280), not the
        subject company's (12927). Without the override these documents 404.
        """
        agent_acc = "0001628280-26-004357"  # prefix = filing agent, not the subject
        urls = []

        async def fake_get(url, **kwargs):
            urls.append(url)
            if "submissions" in url:
                return _TESLA_SUBMISSIONS
            return _FILING_HTML

        with patch("src.mcp_servers.sec_edgar.server._edgar_get", side_effect=fake_get):
            result = await get_filing_text(agent_acc, cik="12927")

        # Submissions lookup + archive path must use the subject CIK, not the agent prefix.
        assert any("CIK0000012927" in u for u in urls)
        assert all("data/1628280/" not in u for u in urls)
        assert "Archives/edgar/data/12927/" in result["source_url"]


# ── get_company_facts ─────────────────────────────────────────────────────────

class TestGetCompanyFacts:
    async def test_returns_distilled_facts(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=_TESLA_FACTS,
        ):
            result = await get_company_facts(_TESLA_CIK)

        assert result["company_name"] == "Tesla, Inc."
        assert result["cik"] == _TESLA_CIK_PADDED
        assert "us-gaap" in result["facts_summary"]
        assert "Revenues" in result["facts_summary"]["us-gaap"]

    async def test_picks_most_recent_10k_value(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=_TESLA_FACTS,
        ):
            result = await get_company_facts(_TESLA_CIK)

        revenues = result["facts_summary"]["us-gaap"]["Revenues"]
        # Most recent 10-K: 2024-01-29, value = 97_690_000_000
        assert revenues["latest_value"] == 97690000000
        assert revenues["latest_filed"] == "2024-01-29"

    async def test_total_concepts_counted(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=_TESLA_FACTS,
        ):
            result = await get_company_facts(_TESLA_CIK)

        assert result["total_concepts"] == 2  # Revenues + NetIncomeLoss

    async def test_cik_padded_in_url(self):
        get_mock = AsyncMock(return_value=_TESLA_FACTS)
        with patch("src.mcp_servers.sec_edgar.server._edgar_get", get_mock):
            await get_company_facts("1318605")

        called_url = get_mock.call_args[0][0]
        assert "CIK0001318605" in called_url
        assert "companyfacts" in called_url

    async def test_error_propagates(self):
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value={"error": "not_found", "message": "Not found"},
        ):
            result = await get_company_facts("9999999999")

        assert result["error"] == "not_found"

    async def test_concept_without_10k_falls_back_to_any_filing(self):
        """If no 10-K entries exist, the most recent of any filing type is used."""
        facts_no_10k = {
            "entityName": "Some Corp",
            "facts": {
                "us-gaap": {
                    "Assets": {
                        "label": "Assets",
                        "description": "Total assets.",
                        "units": {
                            "USD": [
                                {"val": 5000000, "filed": "2024-01-15", "form": "10-Q", "end": "2023-09-30"},
                            ]
                        },
                    }
                }
            },
        }
        with patch(
            "src.mcp_servers.sec_edgar.server._edgar_get",
            new_callable=AsyncMock,
            return_value=facts_no_10k,
        ):
            result = await get_company_facts("0000001234")

        assert "Assets" in result["facts_summary"]["us-gaap"]
        assert result["facts_summary"]["us-gaap"]["Assets"]["latest_value"] == 5000000


# ── _edgar_get error-handling / backoff ──────────────────────────────────────

class TestEdgarGetErrorHandling:
    async def test_429_retries_with_exponential_backoff(self):
        resp = _mock_http_response(429, text="Too Many Requests")
        client = _mock_http_client(resp)
        sleep_calls: list[float] = []

        async def fake_sleep(s: float) -> None:
            sleep_calls.append(s)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            result = await _edgar_get("https://data.sec.gov/test")

        assert result["error"] == "rate_limited"
        assert sleep_calls == [1, 2]  # 3 attempts → 2 sleeps

    async def test_503_retries_then_rate_limited(self):
        resp = _mock_http_response(503, text="Service Unavailable")
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _edgar_get("https://data.sec.gov/test")

        assert result["error"] == "rate_limited"

    async def test_404_returns_not_found_immediately(self):
        resp = _mock_http_response(404, text="Not Found")
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _edgar_get("https://data.sec.gov/test")

        assert result["error"] == "not_found"
        sleep_mock.assert_not_called()

    async def test_timeout_retries_then_fails(self):
        client = _mock_http_client(None)
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _edgar_get("https://data.sec.gov/test")

        assert result["error"] == "timeout"

    async def test_success_returns_json_no_sleep(self):
        resp = _mock_http_response(200, json_data={"name": "Tesla, Inc."})
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = await _edgar_get("https://data.sec.gov/test")

        assert result == {"name": "Tesla, Inc."}
        sleep_mock.assert_not_called()

    async def test_as_text_true_returns_string(self):
        resp = _mock_http_response(200, text="<html>raw text</html>")
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client):
            result = await _edgar_get("https://www.sec.gov/Archives/test.htm", as_text=True)

        assert isinstance(result, str)
        assert "raw text" in result

    async def test_unexpected_exception_returns_request_failed(self):
        client = _mock_http_client(None)
        client.get = AsyncMock(side_effect=RuntimeError("connection reset"))

        with patch("httpx.AsyncClient", return_value=client):
            result = await _edgar_get("https://data.sec.gov/test")

        assert result["error"] == "request_failed"
        assert "connection reset" in result["message"]

    async def test_200_non_json_returns_text(self):
        """Responses with non-JSON bodies (e.g. filing HTML) are returned as string."""
        resp = _mock_http_response(200, text="<html>Annual Report</html>")
        resp.json = MagicMock(side_effect=ValueError("not JSON"))
        client = _mock_http_client(resp)

        with patch("httpx.AsyncClient", return_value=client):
            result = await _edgar_get("https://www.sec.gov/test.htm")

        assert isinstance(result, str)


# ── Integration tests (live SEC EDGAR API) ────────────────────────────────────

@pytest.mark.integration
class TestSecEdgarIntegration:
    """Live tests against the real SEC EDGAR API. No API key needed.
    Rate limit: 10 req/sec — run with -p no:timeout or increase timeout."""

    async def test_search_tesla_10k_returns_filings(self):
        result = await search_filings(_TESLA_CIK, filing_type="10-K")
        assert "error" not in result
        assert result["company_name"] == "Tesla, Inc."
        assert result["total_returned"] >= 1
        assert result["filings"][0]["filing_type"] == "10-K"

    async def test_search_microsoft_no_filter(self):
        result = await search_filings("789019")
        assert "error" not in result
        assert result["total_returned"] > 0
        # EDGAR returns the name uppercased ('MICROSOFT CORP') — match case-insensitively.
        assert "MICROSOFT" in result["company_name"].upper()

    async def test_search_with_date_range_filters(self):
        result = await search_filings(_TESLA_CIK, date_from="2023-01-01", date_to="2024-12-31")
        assert "error" not in result
        for filing in result["filings"]:
            assert "2023-01-01" <= filing["filing_date"] <= "2024-12-31"

    async def test_nonexistent_cik_returns_not_found(self):
        result = await search_filings("9999999999")
        assert result.get("error") in ("not_found", "api_error")

    async def test_get_tesla_10k_filing_text(self):
        # First get a real accession number
        search = await search_filings(_TESLA_CIK, filing_type="10-K")
        assert "error" not in search and search["total_returned"] >= 1
        acc_num = search["filings"][0]["accession_number"]

        # Pass the subject CIK — recent filings are often agent-submitted, so the
        # accession prefix alone resolves to the wrong archive path and 404s.
        result = await get_filing_text(acc_num, cik=_TESLA_CIK)
        assert "error" not in result
        assert result["text_length"] > 1000
        assert result["filing_type"] == "10-K"
        assert "Archives/edgar/data" in result["source_url"]

    async def test_get_tesla_company_facts(self):
        result = await get_company_facts(_TESLA_CIK)
        assert "error" not in result
        # EDGAR may use a non-breaking space in the entity name — normalise before comparing.
        assert result["company_name"].replace("\xa0", " ") == "Tesla, Inc."
        assert result["total_concepts"] > 10
        assert "us-gaap" in result["facts_summary"]

    async def test_get_microsoft_company_facts_has_revenues(self):
        result = await get_company_facts("789019")
        assert "error" not in result
        assert "us-gaap" in result["facts_summary"]
        gaap = result["facts_summary"]["us-gaap"]
        assert any("Revenue" in k for k in gaap)

    async def test_private_company_not_in_edgar(self):
        """Private companies are not in EDGAR — expect not_found."""
        result = await search_filings("0000000001")
        assert result.get("error") in ("not_found", "api_error") or result.get("total_returned", 0) == 0
