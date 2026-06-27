"""
Custom News API MCP Server — Task 5.6

The fourth custom MCP server. Demonstrates the platform's core extensibility
thesis: a new data source plugs in as a self-contained MCP server with its own
auth and rate-limiting, and the research agent picks it up with zero agent-code
changes beyond one entry in the concurrent gather.

Exposes 1 tool over the Model Context Protocol:
  • search_news  — recent news articles mentioning a query (company name)

Auth: API key via the ``X-Api-Key`` header (NewsAPI.org).
Rate limit: free "Developer" tier — 100 requests/day, ~50 req/12h burst.
            Articles are limited to the last month and 100 results per query.
Base URL: https://newsapi.org/v2
"""

import asyncio
import logging

import httpx
from mcp.server.fastmcp import FastMCP

from src.config import settings

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "news",
    instructions=(
        "News gives access to recent news articles from 150,000+ sources via "
        "NewsAPI.org. Use search_news(query) to find articles mentioning a "
        "company — useful for surfacing lawsuits, investigations, fines, "
        "leadership changes, and reputational events that may not appear in "
        "registry data or filings. Optionally narrow by from_date / to_date "
        "(ISO 'YYYY-MM-DD') and language. If the server returns "
        "{\"error\": \"missing_key\"}, no NEWS_API_KEY is configured and the "
        "source is skipped — other sources still run."
    ),
)

_BASE_URL = "https://newsapi.org/v2"
_MAX_RETRIES = 3
_RETRY_CODES = {429, 503}
_MAX_PAGE_SIZE = 100


# ── Core HTTP helper ──────────────────────────────────────────────────────────

async def _request(endpoint: str, params: dict) -> dict:
    """Make a rate-limit-aware GET request to the NewsAPI.org API.

    Retries up to _MAX_RETRIES times with exponential backoff on 429/503/timeout.
    Returns a structured error dict on all non-200 outcomes so callers never
    receive an exception from the network layer.
    """
    if not settings.news_api_key:
        return {
            "error": "missing_key",
            "message": (
                "NEWS_API_KEY is not configured. Set it in .env to enable the "
                "News source. Get a free key at https://newsapi.org."
            ),
        }

    url = f"{_BASE_URL}{endpoint}"
    headers = {
        "X-Api-Key": settings.news_api_key,
        "Accept": "application/json",
    }

    async with httpx.AsyncClient() as client:
        for attempt in range(_MAX_RETRIES):
            try:
                r = await client.get(url, params=params, headers=headers, timeout=15)

                if r.status_code == 200:
                    return r.json()

                if r.status_code in _RETRY_CODES:
                    if attempt < _MAX_RETRIES - 1:
                        wait = 2 ** attempt  # 1 s → 2 s → 4 s
                        logger.warning(
                            "NewsAPI HTTP %s — retrying in %ds (attempt %d/%d)",
                            r.status_code, wait, attempt + 1, _MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue
                    return {
                        "error": "rate_limited",
                        "message": (
                            f"NewsAPI returned HTTP {r.status_code} after {_MAX_RETRIES} retries. "
                            "Free tier: 100 requests/day. Try again later."
                        ),
                    }

                if r.status_code == 401:
                    return {
                        "error": "unauthorized",
                        "message": "NewsAPI rejected the API key (HTTP 401). Check NEWS_API_KEY.",
                    }

                if r.status_code == 426:
                    # Free tier rejects requests for articles older than ~1 month.
                    return {
                        "error": "upgrade_required",
                        "message": (
                            "NewsAPI returned HTTP 426 — the free tier only serves articles "
                            "from the last month. Narrow from_date to a recent range."
                        ),
                    }

                if r.status_code == 404:
                    return {"error": "not_found", "message": "No matching news found"}

                return {
                    "error": "api_error",
                    "message": f"HTTP {r.status_code}: {r.text[:300]}",
                }

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "NewsAPI timed out — retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue
                return {
                    "error": "timeout",
                    "message": "Request timed out after retries. Check network or try later.",
                }

            except Exception as exc:
                logger.error("NewsAPI request failed: %s", exc)
                return {"error": "request_failed", "message": str(exc)}

    return {"error": "max_retries", "message": "Max retries exceeded"}


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_news(
    query: str,
    from_date: str = "",
    to_date: str = "",
    language: str = "en",
) -> dict:
    """Search recent news articles mentioning a query across 150,000+ sources.

    Useful for surfacing lawsuits, regulatory actions, investigations, fines,
    data breaches, leadership changes, and reputational events for a company.
    Results are sorted newest-first.

    Args:
        query: Search query, e.g. a company name (required). Phrases work — the
            query is sent to NewsAPI verbatim, so '"Acme Corp"' forces an exact
            phrase match.
        from_date: Optional earliest article date, ISO 'YYYY-MM-DD'. The free
            tier only serves articles from roughly the last month.
        to_date:   Optional latest article date, ISO 'YYYY-MM-DD'.
        language:  Optional ISO-639-1 language code (default 'en').

    Returns:
        On success: {
            "query": "Tesla",
            "total_results": 1234,
            "articles": [
                {
                    "title": "...",
                    "source": "Reuters",
                    "author": "...",
                    "description": "...",
                    "url": "https://...",
                    "published_at": "2026-06-20T14:03:00Z",
                    "content": "..."
                },
                ...
            ],
            "returned": N
        }
        On failure: {"error": "<code>", "message": "<reason>"}

        Error codes: missing_key | unauthorized | rate_limited | upgrade_required
                     | not_found | api_error | timeout | request_failed
    """
    if not query or not query.strip():
        return {"error": "bad_request", "message": "query must not be empty"}

    params: dict = {
        "q": query,
        "language": language,
        "sortBy": "publishedAt",
        "pageSize": min(settings.max_docs_per_source, _MAX_PAGE_SIZE),
    }
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    data = await _request("/everything", params)

    if isinstance(data, dict) and "error" in data:
        return data

    # NewsAPI signals logical errors with status="error" even on some 200s.
    if isinstance(data, dict) and data.get("status") == "error":
        return {
            "error": data.get("code", "api_error"),
            "message": data.get("message", "NewsAPI returned an error"),
        }

    raw_articles = data.get("articles", []) if isinstance(data, dict) else []
    articles = [
        {
            "title": a.get("title", ""),
            "source": (a.get("source") or {}).get("name", ""),
            "author": a.get("author", ""),
            "description": a.get("description", ""),
            "url": a.get("url", ""),
            "published_at": a.get("publishedAt", ""),
            "content": a.get("content", ""),
        }
        for a in raw_articles
    ]

    return {
        "query": query,
        "total_results": data.get("totalResults", len(articles)) if isinstance(data, dict) else len(articles),
        "articles": articles,
        "returned": len(articles),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
