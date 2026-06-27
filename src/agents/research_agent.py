"""
Research Agent — Task 1.5 / 3.1.4

Gathers raw documents from five concurrent sources:
  1. web_search       — Tavily search (risk/lawsuit/regulatory queries)
  2. website          — company's own website (heuristic URL, httpx)
  3. registry_lookup  — custom Registry Lookup MCP tools (direct call)
  4. companies_house  — custom Companies House MCP tools (UK entities only)
  5. sec_edgar        — custom SEC EDGAR MCP tools (US public companies only)

All sources run concurrently via asyncio.gather(return_exceptions=True).
A failing source is recorded in sources_failed and never crashes the pipeline.
"""

import asyncio
import json
import logging
import re
from html.parser import HTMLParser
from typing import TypedDict

import httpx

from src.config import settings
from src.models.documents import RawDocument
from src.models.entities import ResolvedEntity
from src.models.signals import SourceType

logger = logging.getLogger(__name__)

_EDGAR_UA = f"DueDiligencePlatform/1.0 {__import__('src.config', fromlist=['settings']).settings.contact_email}"


# ── State schema (1.5.1) ───────────────────────────────────────────────────────

class ResearchState(TypedDict):
    resolved_entity: ResolvedEntity
    documents: list[RawDocument]
    sources_consulted: list[str]
    sources_failed: list[str]
    iteration_counts: dict[str, int]


# ── HTML → plain text ─────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    _SKIP_TAGS = {"script", "style", "head", "nav", "footer", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in self._SKIP_TAGS:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag in self._SKIP_TAGS and self._depth > 0:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        text = parser.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    return " ".join(text.split())[:50_000]


# ── Source 1: web search (Tavily) — 1.5.2 ────────────────────────────────────

def _extract_domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url.lower())
    return m.group(1) if m else ""


def _classify_source_type(url: str, entity: ResolvedEntity) -> SourceType:
    """Classify a URL into the correct SourceType based on the domain.

    Priority order: SEC_FILING → COURT_RECORD (gov non-SEC) → COMPANY_WEBSITE → NEWS_ARTICLE
    """
    domain = _extract_domain(url)
    # SEC EDGAR filings
    if "sec.gov" in domain:
        return SourceType.SEC_FILING
    # Government agencies → court/regulatory records
    if domain.endswith(".gov") or domain.endswith(".gov.uk"):
        return SourceType.COURT_RECORD
    # Sanctions/enforcement lists
    if any(kw in domain for kw in ("ofac", "sanctions", "interpol")):
        return SourceType.SANCTIONS_LIST
    # COMPANY_WEBSITE: domain must be the company's own domain (not third-party coverage)
    base_name = re.sub(r"[^a-z0-9]", "", entity.canonical_name.lower().split(",")[0])
    if base_name and len(base_name) > 3 and domain.startswith(base_name):
        return SourceType.COMPANY_WEBSITE
    return SourceType.NEWS_ARTICLE


async def _search_web(entity: ResolvedEntity) -> list[RawDocument]:
    """Tavily searches across canonical name + aliases. Classifies results by URL."""
    try:
        from tavily import TavilyClient
    except ImportError:
        logger.warning("tavily-python not installed — skipping web search")
        return []

    max_docs = settings.max_docs_per_source
    queries = [
        f'"{entity.canonical_name}" risks OR lawsuit OR fine OR investigation',
        f'"{entity.canonical_name}" regulatory action OR compliance OR penalty',
        f'"{entity.canonical_name}" site:sec.gov OR site:nhtsa.gov OR site:ftc.gov OR site:doj.gov',
        f'"{entity.canonical_name}" annual report OR 10-K filing',
    ]
    for alias in entity.aliases[1:3]:
        if alias.upper() != entity.canonical_name.upper():
            queries.append(f'"{alias}" lawsuit OR fine OR regulatory')

    client = TavilyClient(api_key=settings.tavily_api_key)
    docs: list[RawDocument] = []
    seen_urls: set[str] = set()

    for query in queries:
        if len(docs) >= max_docs:
            break
        try:
            batch = await asyncio.to_thread(
                client.search,
                query,
                max_results=min(max_docs - len(docs), 5),
                include_raw_content=True,
                search_depth="advanced",
            )
            for r in batch.get("results", []):
                url = r.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                # Prefer raw_content (full page text) over snippet when available
                content = r.get("raw_content") or r.get("content") or r.get("snippet", "")
                docs.append(RawDocument(
                    source_url=url,
                    source_type=_classify_source_type(url, entity),
                    raw_text=content[:50_000],
                    entity_name=entity.canonical_name,
                    metadata={
                        "title": r.get("title", ""),
                        "query": query,
                        "score": r.get("score", 0),
                    },
                ))
        except Exception as exc:
            logger.warning("Tavily query failed ('%s'): %s", query, exc)

    return docs[:max_docs]


# ── Source 2: company website (httpx) — 1.5.3 ────────────────────────────────

async def _fetch_url(url: str, entity_name: str, source_type: SourceType) -> RawDocument | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            r = await client.get(url, headers={"User-Agent": _EDGAR_UA})
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "")
        text = _html_to_text(r.text) if ("html" in ct or not ct) else r.text[:50_000]
        if len(text.strip()) < 100:
            return None
        return RawDocument(
            source_url=str(r.url),
            source_type=source_type,
            raw_text=text,
            entity_name=entity_name,
            metadata={"content_type": ct},
        )
    except Exception as exc:
        logger.debug("URL fetch failed (%s): %s", url, exc)
        return None


async def _fetch_website(entity: ResolvedEntity) -> list[RawDocument]:
    """Fetch company's own website using a heuristic URL from the canonical name."""
    base = re.sub(r"[^a-z0-9]", "", entity.canonical_name.lower().split(",")[0])
    urls = [f"https://www.{base}.com", f"https://{base}.com/about"]
    for url in urls:
        doc = await _fetch_url(url, entity.canonical_name, SourceType.COMPANY_WEBSITE)
        if doc:
            return [doc]
    return []


# ── Source 3: Registry Lookup MCP — 1.5.4 ────────────────────────────────────

async def _fetch_registry_lookup(entity: ResolvedEntity) -> list[RawDocument]:
    """Query Registry Lookup MCP tools directly; returns [] on geo-block."""
    from src.mcp_servers.registry_lookup.server import (
        get_company_by_id,
        search_company,
    )

    docs: list[RawDocument] = []
    result = await search_company(entity.canonical_name)

    if "error" in result:
        logger.info(
            "Registry Lookup unavailable (%s) for '%s'",
            result["error"], entity.canonical_name,
        )
        return []

    items = result.get("results", [])
    if not items:
        return []

    docs.append(RawDocument(
        source_url="https://registry-lookup.com/search",
        source_type=SourceType.COMPANY_REGISTRY,
        raw_text=json.dumps(items[:5], indent=2),
        entity_name=entity.canonical_name,
        metadata={"result_count": len(items), "source": "registry_lookup"},
    ))

    if top_id := items[0].get("id"):
        detail = await get_company_by_id(str(top_id))
        if "error" not in detail:
            docs.append(RawDocument(
                source_url=f"https://registry-lookup.com/company/{top_id}",
                source_type=SourceType.COMPANY_REGISTRY,
                raw_text=json.dumps(detail, indent=2),
                entity_name=entity.canonical_name,
                metadata={"source": "registry_lookup_detail", "id": top_id},
            ))

    return docs[:settings.max_docs_per_source]


# ── Source 4: Companies House MCP — 1.5.4 (UK only) ─────────────────────────

async def _fetch_companies_house(entity: ResolvedEntity) -> list[RawDocument]:
    """Fetch officers + filings from Companies House. Silently skips non-UK entities."""
    jurisdiction = (entity.jurisdiction or "").lower()
    is_uk = "gb" in jurisdiction or bool(entity.companies_house_number)
    if not is_uk:
        return []

    from src.mcp_servers.companies_house.server import (
        get_company_filings,
        get_company_officers,
        search_company,
    )

    ch_number = entity.companies_house_number
    if not ch_number:
        sr = await search_company(entity.canonical_name, items_per_page=3)
        if "error" in sr or not sr.get("items"):
            return []
        ch_number = sr["items"][0]["company_number"]

    officers_result, filings_result = await asyncio.gather(
        get_company_officers(ch_number),
        get_company_filings(ch_number, items_per_page=20),
        return_exceptions=True,
    )

    docs: list[RawDocument] = []
    base_url = "https://find-and-update.company-information.service.gov.uk/company"

    for label, result in (("officers", officers_result), ("filings", filings_result)):
        if isinstance(result, Exception) or (isinstance(result, dict) and "error" in result):
            logger.debug("Companies House %s fetch failed for %s", label, ch_number)
            continue
        docs.append(RawDocument(
            source_url=f"{base_url}/{ch_number}/{label}",
            source_type=SourceType.COMPANY_REGISTRY,
            raw_text=json.dumps(result, indent=2),
            entity_name=entity.canonical_name,
            metadata={"source": f"companies_house_{label}", "company_number": ch_number},
        ))

    return docs


# ── Source 5: SEC EDGAR MCP — 3.1.4 (US public companies only) ───────────────

async def _fetch_sec_edgar(entity: ResolvedEntity) -> list[RawDocument]:
    """Fetch 10-K, 10-Q, and recent 8-K filings from SEC EDGAR.

    Silently skips entities that are not public US companies or have no CIK.
    Searches for 10-K, 10-Q, and 8-K filings concurrently, then fetches
    the primary document text for the most recent filing of each type.
    """
    if not entity.is_public or not entity.sec_cik:
        return []

    from src.mcp_servers.sec_edgar.server import get_filing_text, search_filings

    # Search for three filing types concurrently
    search_results = await asyncio.gather(
        search_filings(entity.sec_cik, filing_type="10-K"),
        search_filings(entity.sec_cik, filing_type="10-Q"),
        search_filings(entity.sec_cik, filing_type="8-K"),
        return_exceptions=True,
    )

    # Pick the most recent: 1 × 10-K, 1 × 10-Q, 2 × 8-K
    _LIMITS: dict[str, int] = {"10-K": 1, "10-Q": 1, "8-K": 2}
    targets: list[tuple[str, str, str]] = []  # (accession_number, form_type, filing_date)

    for form_type, result in zip(["10-K", "10-Q", "8-K"], search_results):
        if isinstance(result, Exception) or not isinstance(result, dict) or "error" in result:
            logger.debug("SEC EDGAR search failed for %s/%s: %s", entity.sec_cik, form_type, result)
            continue
        for filing in result.get("filings", [])[: _LIMITS[form_type]]:
            targets.append((filing["accession_number"], form_type, filing.get("filing_date", "")))

    if not targets:
        return []

    # Fetch all filing texts concurrently. Pass the subject CIK so agent-filed
    # documents resolve to the correct EDGAR archive path (else they 404).
    texts = await asyncio.gather(
        *[get_filing_text(acc, cik=entity.sec_cik) for acc, _, _ in targets],
        return_exceptions=True,
    )

    docs: list[RawDocument] = []
    for (acc_num, form_type, filing_date), text_result in zip(targets, texts):
        if isinstance(text_result, Exception) or not isinstance(text_result, dict) or "error" in text_result:
            logger.debug("EDGAR filing text fetch failed for %s: %s", acc_num, text_result)
            continue
        text = text_result.get("text", "")
        if not text:
            continue

        acc_no_dash = acc_num.replace("-", "")
        cik_int = int(str(entity.sec_cik).lstrip("0") or "0")

        docs.append(RawDocument(
            source_url=text_result.get(
                "source_url",
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dash}/",
            ),
            source_type=SourceType.SEC_FILING,
            raw_text=text[:50_000],
            entity_name=entity.canonical_name,
            metadata={
                "source": "sec_edgar",
                "filing_type": form_type,
                "filing_date": filing_date,
                "accession_number": acc_num,
                "cik": entity.sec_cik,
            },
        ))

    return docs[:settings.max_docs_per_source]


# ── Research Agent node (1.5.5, 1.5.6) ───────────────────────────────────────

async def research_agent_node(state: ResearchState) -> dict:
    """LangGraph node — fetch documents from all sources concurrently.

    Each source runs in a single asyncio.gather with return_exceptions=True.
    A source that raises is logged and added to sources_failed without affecting
    the others. Empty results count as consulted (not failed).

    Functions are resolved from the module at call time so unit tests can patch
    individual sources without replacing the node itself.
    """
    import src.agents.research_agent as _m
    from src.llm import tracing as _tracing

    sources = {
        "web_search": _m._search_web,
        "website": _m._fetch_website,
        "registry_lookup": _m._fetch_registry_lookup,
        "companies_house": _m._fetch_companies_house,
        "sec_edgar": _m._fetch_sec_edgar,
    }

    entity = state["resolved_entity"]
    max_docs = settings.max_docs_per_source

    async def _traced_source(name: str, fn, ent: "ResolvedEntity") -> "list[RawDocument]":
        """Wrap each source in an MCP span so Langfuse shows per-source latency."""
        async with _tracing.mcp_span(name, input_data={"entity": ent.canonical_name}):
            return await fn(ent)

    names = list(sources.keys())
    results = await asyncio.gather(
        *[_traced_source(name, fn, entity) for name, fn in sources.items()],
        return_exceptions=True,
    )

    new_docs: list[RawDocument] = []
    consulted: list[str] = []
    failed: list[str] = []
    iter_counts: dict[str, int] = dict(state.get("iteration_counts") or {})

    for name, result in zip(names, results):
        iter_counts[name] = iter_counts.get(name, 0) + 1

        if isinstance(result, Exception):
            logger.warning("Source '%s' raised: %s", name, result)
            failed.append(name)
            continue

        if not isinstance(result, list):
            logger.warning("Source '%s' returned unexpected type %s", name, type(result))
            failed.append(name)
            continue

        capped = result[:max_docs]
        new_docs.extend(capped)
        consulted.append(name)
        logger.info("Source '%s' → %d document(s)", name, len(capped))

    logger.info(
        "Research done: %d new docs | consulted=%s | failed=%s",
        len(new_docs), consulted, failed,
    )

    return {
        "documents": list(state.get("documents") or []) + new_docs,
        "sources_consulted": list(state.get("sources_consulted") or []) + consulted,
        "sources_failed": list(state.get("sources_failed") or []) + failed,
        "iteration_counts": iter_counts,
    }
