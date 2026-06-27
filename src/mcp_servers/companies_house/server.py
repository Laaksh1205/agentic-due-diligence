"""
Custom Companies House MCP Server — Task 1.4b

Exposes 4 tools over the Model Context Protocol:
  • search_company         — search UK companies by name
  • get_company_details    — full company profile by company number
  • get_company_officers   — current and past officers (directors, secretaries)
  • get_company_filings    — filing history (accounts, confirmations, charges)

Auth: HTTP Basic Auth — Companies House API key as username, empty password.
Base URL: https://api.company-information.service.gov.uk
Rate limits: no published hard limit; reasonable backoff added for 429/503.
Globally accessible (no geo-block).
"""

import asyncio
import base64
import logging

import httpx
from mcp.server.fastmcp import FastMCP

from src.config import settings

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "companies-house",
    instructions=(
        "Companies House gives free access to UK company registration data. "
        "Use search_company to find a UK company by name, get_company_details "
        "for its full profile (SIC codes, registered address, status), "
        "get_company_officers for directors and secretaries (including past ones), "
        "and get_company_filings for accounts, confirmation statements, and charges. "
        "All tools are globally accessible — no geo-block."
    ),
)

_BASE_URL = "https://api.company-information.service.gov.uk"
_MAX_RETRIES = 3
_RETRY_CODES = {429, 503}


# ── Auth helper ───────────────────────────────────────────────────────────────

def _auth_header() -> str:
    """Companies House Basic Auth: API key as username, empty password."""
    token = base64.b64encode(f"{settings.companies_house_api_key}:".encode()).decode()
    return f"Basic {token}"


# ── Core HTTP helper ──────────────────────────────────────────────────────────

async def _ch_request(endpoint: str, params: dict | None = None) -> dict | list:
    """Make a GET request to the Companies House API with backoff on 429/503.

    Returns the parsed JSON on 200, or a structured error dict on all other
    outcomes so callers never receive a raw exception from the network layer.
    """
    url = f"{_BASE_URL}{endpoint}"
    headers = {"Authorization": _auth_header()}

    async with httpx.AsyncClient() as client:
        for attempt in range(_MAX_RETRIES):
            try:
                r = await client.get(url, params=params or {}, headers=headers, timeout=10)

                if r.status_code == 200:
                    return r.json()

                if r.status_code in _RETRY_CODES:
                    if attempt < _MAX_RETRIES - 1:
                        wait = 2 ** attempt  # 1 s → 2 s → 4 s
                        logger.warning(
                            "Companies House HTTP %s — retrying in %ds (attempt %d/%d)",
                            r.status_code, wait, attempt + 1, _MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue
                    return {
                        "error": "rate_limited",
                        "message": f"Companies House returned HTTP {r.status_code} after {_MAX_RETRIES} retries.",
                    }

                if r.status_code == 401:
                    return {"error": "auth_failed", "message": "Invalid Companies House API key (HTTP 401)."}

                if r.status_code == 404:
                    return {"error": "not_found", "message": f"Resource not found: {endpoint}"}

                return {
                    "error": "api_error",
                    "message": f"HTTP {r.status_code}: {r.text[:300]}",
                }

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Companies House timed out — retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue
                return {"error": "timeout", "message": "Request timed out after retries."}

            except Exception as exc:
                logger.error("Companies House request failed: %s", exc)
                return {"error": "request_failed", "message": str(exc)}

    return {"error": "max_retries", "message": "Max retries exceeded"}


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_company(name: str, items_per_page: int = 10) -> dict:
    """Search for UK companies by name in the Companies House registry.

    Args:
        name: Company name to search for (required). Partial names work.
        items_per_page: Number of results to return (default 10, max 100).

    Returns:
        On success: {"items": [...], "total_results": N, "items_per_page": N}
            Each item includes: company_number, title, company_status,
            company_type, date_of_creation, registered_office_address.
        On failure: {"error": "<code>", "message": "<reason>"}
    """
    data = await _ch_request("/search/companies", {"q": name, "items_per_page": items_per_page})

    if isinstance(data, dict) and "error" in data:
        return data

    return {
        "items": data.get("items", []),
        "total_results": data.get("total_results", 0),
        "items_per_page": data.get("items_per_page", items_per_page),
    }


@mcp.tool()
async def get_company_details(company_number: str) -> dict:
    """Retrieve the full company profile from Companies House by company number.

    Returns registered address, SIC codes, company status, incorporation date,
    accounts due date, confirmation statement due date, and more.

    Args:
        company_number: Companies House company number (e.g. '08804411' for Revolut).

    Returns:
        On success: full company profile dict from Companies House.
        On failure: {"error": "<code>", "message": "<reason>"}
    """
    return await _ch_request(f"/company/{company_number.upper()}")


@mcp.tool()
async def get_company_officers(
    company_number: str,
    items_per_page: int = 50,
    include_resigned: bool = True,
) -> dict:
    """Retrieve current and past officers (directors, secretaries) for a UK company.

    Useful for beneficial ownership checks, director disqualification research,
    and cross-referencing individuals across multiple companies.

    Args:
        company_number: Companies House company number.
        items_per_page: Number of officer records to return (default 50).
        include_resigned: Include resigned officers (default True for due diligence).

    Returns:
        On success: {"items": [...], "total_results": N}
            Each item includes: name, officer_role, appointed_on,
            resigned_on (if applicable), nationality, country_of_residence,
            date_of_birth (month/year only, no day).
        On failure: {"error": "<code>", "message": "<reason>"}
    """
    params: dict = {"items_per_page": items_per_page}
    if not include_resigned:
        params["register_view"] = "true"

    data = await _ch_request(f"/company/{company_number.upper()}/officers", params)

    if isinstance(data, dict) and "error" in data:
        return data

    return {
        "items": data.get("items", []),
        "total_results": data.get("total_results", 0),
        "active_count": data.get("active_count", 0),
        "resigned_count": data.get("resigned_count", 0),
    }


@mcp.tool()
async def get_company_filings(
    company_number: str,
    category: str = "",
    items_per_page: int = 25,
) -> dict:
    """Retrieve the filing history for a UK company from Companies House.

    Covers accounts, confirmation statements, director changes, mortgages/charges,
    and other statutory filings. Useful for identifying late filings, dormant status,
    or recent structural changes.

    Args:
        company_number: Companies House company number.
        category: Optional filing category filter. Common values:
            'accounts' — annual accounts
            'confirmation-statement' — annual confirmation statements
            'mortgage' — charges and mortgages
            'officers' — director/secretary appointments and resignations
            Leave empty to return all filing types.
        items_per_page: Number of filings to return (default 25).

    Returns:
        On success: {"items": [...], "total_count": N}
            Each item includes: type, date, description, transaction_id,
            links (to view the document).
        On failure: {"error": "<code>", "message": "<reason>"}
    """
    params: dict = {"items_per_page": items_per_page}
    if category:
        params["category"] = category

    data = await _ch_request(f"/company/{company_number.upper()}/filing-history", params)

    if isinstance(data, dict) and "error" in data:
        return data

    return {
        "items": data.get("items", []),
        "total_count": data.get("total_count", 0),
        "filing_history_status": data.get("filing_history_status", ""),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
