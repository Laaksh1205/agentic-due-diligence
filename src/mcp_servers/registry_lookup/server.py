"""
Custom Registry Lookup MCP Server — Task 1.4

Exposes 3 tools over the Model Context Protocol:
  • search_company          — search by name, optional jurisdiction / status filter
  • get_company_by_id       — fetch full entity details by Registry Lookup ID
  • get_company_by_jurisdiction — fetch entity by official registry number + jurisdiction

Rate limiting: 5 000 calls/month, 1 000/day, 10 req/sec (free tier).
Geo-block: 403/520/521 from non-US IPs — tool returns a structured error dict;
           the resolution pipeline falls back to EDGAR / Companies House automatically.
"""

import asyncio
import logging

import httpx
from mcp.server.fastmcp import FastMCP

from src.config import settings

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "registry-lookup",
    instructions=(
        "Registry Lookup gives access to 521 million+ company entities across "
        "309 jurisdictions. Use search_company to find companies by name, "
        "get_company_by_id to retrieve full details once you have an ID, and "
        "get_company_by_jurisdiction to look up a company by its official registry "
        "number. If the server returns {\"error\": \"geo_blocked\"}, the API is not "
        "accessible from the current server location — use SEC EDGAR or Companies "
        "House instead."
    ),
)

_BASE_URL = "https://api.registry-lookup.com/v1"
_MAX_RETRIES = 3
_GEO_BLOCK_CODES = {403, 520, 521}


# ── Core HTTP helper ──────────────────────────────────────────────────────────

async def _request(endpoint: str, params: dict) -> dict | list:
    """Make a rate-limit-aware GET request to the Registry Lookup API.

    Retries up to _MAX_RETRIES times with exponential backoff on 429 / timeout.
    Returns a structured error dict on all non-200 outcomes so callers never
    receive an exception from the network layer.
    """
    url = f"{_BASE_URL}{endpoint}"
    headers = {
        "X-API-Key": settings.registry_lookup_api_key,
        "Accept": "application/json",
    }

    async with httpx.AsyncClient() as client:
        for attempt in range(_MAX_RETRIES):
            try:
                r = await client.get(url, params=params, headers=headers, timeout=10)

                if r.status_code == 200:
                    return r.json()

                if r.status_code == 429:
                    if attempt < _MAX_RETRIES - 1:
                        wait = 2 ** attempt  # 1 s → 2 s → 4 s
                        logger.warning(
                            "Registry Lookup rate-limited — retrying in %ds (attempt %d/%d)",
                            wait, attempt + 1, _MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue
                    return {
                        "error": "rate_limited",
                        "message": (
                            "Rate limit exceeded after retries. "
                            "Free tier: 1 000 calls/day, 10 req/sec. Try again in 60 s."
                        ),
                    }

                if r.status_code in _GEO_BLOCK_CODES:
                    logger.info(
                        "Registry Lookup geo-blocked (HTTP %s) — "
                        "falling back to EDGAR / Companies House",
                        r.status_code,
                    )
                    return {
                        "error": "geo_blocked",
                        "message": (
                            "Registry Lookup is not accessible from this location "
                            f"(HTTP {r.status_code}). Use SEC EDGAR or Companies House instead."
                        ),
                    }

                if r.status_code == 404:
                    return {"error": "not_found", "message": "Company not found"}

                return {
                    "error": "api_error",
                    "message": f"HTTP {r.status_code}: {r.text[:300]}",
                }

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Registry Lookup timed out — retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue
                return {
                    "error": "timeout",
                    "message": "Request timed out after retries. Check network or try later.",
                }

            except Exception as exc:
                logger.error("Registry Lookup request failed: %s", exc)
                return {"error": "request_failed", "message": str(exc)}

    return {"error": "max_retries", "message": "Max retries exceeded"}


def _extract_results(data: dict | list) -> list[dict]:
    """Normalise the various response shapes the API may return."""
    if isinstance(data, list):
        return data
    return (
        data.get("data")
        or data.get("companies")
        or data.get("results")
        or []
    )


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_company(
    name: str,
    jurisdiction: str = "",
    status: str = "",
) -> dict:
    """Search for companies by name across 521M+ entities in 309 jurisdictions.

    Args:
        name: Company name to search (required). Partial names work.
        jurisdiction: Optional jurisdiction code filter, e.g. 'us-de', 'gb', 'us-ca'.
        status: Optional status filter — 'active', 'inactive', or 'dissolved'.

    Returns:
        On success: {"results": [...], "count": N}
        On failure: {"error": "<code>", "message": "<reason>"}

        Error codes: geo_blocked | rate_limited | api_error | timeout | request_failed
    """
    params: dict = {"q": name, "limit": 10}
    if jurisdiction:
        params["jurisdiction_code"] = jurisdiction
    if status:
        params["status"] = status

    data = await _request("/companies/search", params)

    if isinstance(data, dict) and "error" in data:
        return data

    results = _extract_results(data)
    return {"results": results, "count": len(results)}


@mcp.tool()
async def get_company_by_id(id: str) -> dict:
    """Retrieve full company details by Registry Lookup entity ID.

    Use this after search_company to get the complete company profile,
    including officers, filings, registered address, and incorporation date.

    Args:
        id: Registry Lookup entity ID returned by search_company (e.g. 'abc123').

    Returns:
        On success: full company detail dict from Registry Lookup.
        On failure: {"error": "<code>", "message": "<reason>"}
    """
    data = await _request(f"/companies/{id}", {})
    # If the API returns a list for this endpoint (unexpected), wrap it
    if isinstance(data, list):
        return {"company": data[0]} if data else {"error": "not_found", "message": "No data returned"}
    return data


@mcp.tool()
async def get_company_by_jurisdiction(
    jurisdiction_code: str,
    registry_number: str,
) -> dict:
    """Retrieve a company by its official registry number within a specific jurisdiction.

    This is useful when you already know the jurisdiction and local registry number
    (e.g. Companies House number for UK, Delaware file number for US-DE).

    Args:
        jurisdiction_code: Jurisdiction code, e.g. 'gb', 'us-de', 'us-ca', 'sg'.
        registry_number: Official registry number in that jurisdiction.

    Returns:
        On success: company detail dict (first matching result).
        On failure: {"error": "<code>", "message": "<reason>"}
    """
    params = {
        "jurisdiction_code": jurisdiction_code,
        "registry_number": registry_number,
        "limit": 1,
    }
    data = await _request("/companies/search", params)

    if isinstance(data, dict) and "error" in data:
        return data

    results = _extract_results(data)
    if not results:
        return {
            "error": "not_found",
            "message": (
                f"No company found with registry number {registry_number!r} "
                f"in jurisdiction {jurisdiction_code!r}"
            ),
        }
    return results[0]


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
