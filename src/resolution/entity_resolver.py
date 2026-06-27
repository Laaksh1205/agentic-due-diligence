"""
Entity resolution: raw company name → ResolvedEntity.

Flow:
  Registry Lookup → (Companies House enrichment if UK jurisdiction)
                  → (SEC EDGAR enrichment if US / unknown)
                  → SQLite cache (7-day TTL)

Registry Lookup is geo-blocked outside the US. When blocked the resolver
falls back to EDGAR for public companies and Companies House for UK ones.
"""

import base64
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
import httpx

from src.config import settings
from src.models.entities import ResolvedEntity

logger = logging.getLogger(__name__)

_EDGAR_UA = f"DueDiligencePlatform/1.0 {settings.contact_email}"
_CORP_SUFFIX_RE = re.compile(
    r"[\s,]*(Inc\.?|LLC\.?|Ltd\.?|Corp\.?|Co\.?|L\.?P\.?|LLP|PLC"
    r"|Limited|Incorporated|Corporation|Holdings|Group|S\.?A\.?)[\s,]*$",
    re.IGNORECASE,
)


# ── Exceptions ────────────────────────────────────────────────────────────────

class EntityNotFoundError(Exception):
    pass


class EntityAmbiguityError(Exception):
    """Raised when >3 Registry Lookup matches exist; caller must let user pick."""

    def __init__(self, candidates: list[ResolvedEntity]) -> None:
        self.candidates = candidates
        super().__init__(f"{len(candidates)} candidates — user must select one")


# ── Alias helpers ──────────────────────────────────────────────────────────────

def _strip_suffixes(name: str) -> str:
    return _CORP_SUFFIX_RE.sub("", name).strip().strip(",").strip()


def _generate_aliases(canonical: str, raw_input: str, tickers: list[str]) -> list[str]:
    candidates = [
        canonical,
        raw_input.strip(),
        _strip_suffixes(canonical),
        _strip_suffixes(raw_input),
    ] + tickers
    seen: set[str] = set()
    aliases: list[str] = []
    for name in candidates:
        n = name.strip()
        if n and n not in seen:
            seen.add(n)
            aliases.append(n)
    return aliases


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _ch_auth_header() -> str:
    token = base64.b64encode(f"{settings.companies_house_api_key}:".encode()).decode()
    return f"Basic {token}"


# ── Cache key ─────────────────────────────────────────────────────────────────

def _cache_key(raw_input: str) -> str:
    return hashlib.sha256(raw_input.strip().casefold().encode()).hexdigest()


# ── SQLite cache operations ───────────────────────────────────────────────────

async def _open_db() -> aiosqlite.Connection:
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    await db.execute("""
        CREATE TABLE IF NOT EXISTS entity_cache (
            cache_key  TEXT PRIMARY KEY,
            entity_json TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        )
    """)
    await db.commit()
    return db


async def _get_cached(key: str) -> Optional[ResolvedEntity]:
    try:
        db = await _open_db()
        async with db.execute(
            "SELECT entity_json FROM entity_cache WHERE cache_key = ? AND expires_at > ?",
            (key, datetime.now(timezone.utc).isoformat()),
        ) as cur:
            row = await cur.fetchone()
        await db.close()
        if row:
            return ResolvedEntity.model_validate_json(row[0])
    except Exception as exc:
        logger.warning("Entity cache read failed: %s", exc)
    return None


async def _set_cached(key: str, entity: ResolvedEntity) -> None:
    try:
        db = await _open_db()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=settings.cache_ttl_hours)
        ).isoformat()
        await db.execute(
            "INSERT OR REPLACE INTO entity_cache (cache_key, entity_json, expires_at) "
            "VALUES (?, ?, ?)",
            (key, entity.model_dump_json(), expires_at),
        )
        await db.commit()
        await db.close()
    except Exception as exc:
        logger.warning("Entity cache write failed: %s", exc)


# ── Registry Lookup ───────────────────────────────────────────────────────────

async def _rl_search(client: httpx.AsyncClient, query: str) -> tuple[list[dict], bool]:
    """Query Registry Lookup. Returns (results, is_blocked)."""
    try:
        r = await client.get(
            "https://api.registry-lookup.com/v1/companies/search",
            params={"q": query, "limit": 5},
            headers={"X-API-Key": settings.registry_lookup_api_key, "Accept": "application/json"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            results = (
                data.get("data")
                or data.get("companies")
                or data.get("results")
                or (data if isinstance(data, list) else [])
            )
            return results, False
        if r.status_code in (403, 520, 521):
            logger.info("Registry Lookup geo-blocked — falling back to EDGAR / Companies House")
            return [], True
        logger.warning("Registry Lookup HTTP %s", r.status_code)
        return [], False
    except Exception as exc:
        logger.info("Registry Lookup unreachable (%s) — using fallback sources", exc)
        return [], True


def _parse_rl_result(result: dict, raw_input: str) -> ResolvedEntity:
    canonical = (
        result.get("legal_name")
        or result.get("name")
        or result.get("company_name")
        or raw_input.upper()
    )
    jurisdiction = (
        result.get("jurisdiction_code")
        or result.get("country_code")
        or result.get("jurisdiction")
        or result.get("country")
        or ""
    ).lower().replace("_", "-")

    company_type = (result.get("company_type") or "").lower()
    is_public = False
    if jurisdiction.startswith("us"):
        # Check if company type suggests public filing
        if "public" in company_type or "stock" in company_type or "plc" in company_type:
            is_public = True
    elif "plc" in company_type or "public limited" in company_type:
        is_public = True

    return ResolvedEntity(
        canonical_name=canonical,
        aliases=_generate_aliases(canonical, raw_input, []),
        jurisdiction=jurisdiction or None,
        is_public=is_public,
        registry_lookup_id=str(result.get("id", "")) or None,
        industry=result.get("industry") or result.get("sic_description"),
    )


# ── SEC EDGAR ─────────────────────────────────────────────────────────────────

async def _edgar_lookup(client: httpx.AsyncClient, company_name: str) -> dict:
    """Returns {'cik', 'canonical_name', 'tickers', 'state'} or {}.

    Uses company_tickers.json (fuzzy name→CIK) + submissions/{CIK}.json.
    The old CGI browse-edgar endpoint is Akamai-blocked; this replaces it.
    """
    from rapidfuzz import process, fuzz

    headers = {"User-Agent": _EDGAR_UA}
    try:
        # Step 1: download the SEC company tickers index (name→CIK map)
        r = await client.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=headers,
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning("EDGAR company_tickers.json HTTP %s", r.status_code)
            return {}

        tickers_data = r.json()  # {str: {cik_str, ticker, title}}

        def _norm(s: str) -> str:
            # Strip punctuation so "Tesla, Inc." → "TESLA INC" for clean fuzzy match
            return re.sub(r"[^\w\s]", "", s).upper()

        # Build {cik_str → normalized_title} for fuzzy matching
        choices: dict[str, str] = {
            str(v["cik_str"]): _norm(v["title"]) for v in tickers_data.values()
        }

        # Step 2: fuzzy-match the query against normalized company titles
        match = process.extractOne(
            _norm(company_name),
            choices,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=70,
        )
        if not match:
            logger.info("EDGAR: no fuzzy match for '%s' (score < 70)", company_name)
            return {}

        _matched_title, _score, matched_cik = match
        cik_padded = str(matched_cik).zfill(10)
        logger.info("EDGAR fuzzy match: '%s' → CIK %s (score=%d)", company_name, cik_padded, _score)

        # Step 3: fetch full submissions metadata
        r2 = await client.get(
            f"https://data.sec.gov/submissions/CIK{cik_padded}.json",
            headers=headers,
            timeout=10,
        )
        if r2.status_code != 200:
            return {"cik": cik_padded, "canonical_name": _matched_title}

        data = r2.json()
        return {
            "cik": str(data.get("cik", matched_cik)).zfill(10),
            "canonical_name": data.get("name", _matched_title),
            "tickers": data.get("tickers", []),
            "state": data.get("stateOfIncorporation", ""),
        }
    except Exception as exc:
        logger.warning("EDGAR lookup failed for '%s': %s", company_name, exc)
        return {}


# ── Companies House ───────────────────────────────────────────────────────────

async def _ch_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Search Companies House. Returns [] on error."""
    try:
        r = await client.get(
            "https://api.company-information.service.gov.uk/search/companies",
            params={"q": query, "items_per_page": 5},
            headers={"Authorization": _ch_auth_header()},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("items", [])
        logger.warning("Companies House HTTP %s", r.status_code)
    except Exception as exc:
        logger.warning("Companies House unreachable: %s", exc)
    return []


# ── Entity Resolver ───────────────────────────────────────────────────────────

class EntityResolver:
    """Resolves a raw company name to a canonical ResolvedEntity.

    Usage::

        resolver = EntityResolver()
        entity = await resolver.resolve("Tesla Inc")
    """

    async def resolve(self, raw_input: str) -> ResolvedEntity:
        """Resolve *raw_input* to a ``ResolvedEntity``.

        Raises:
            EntityNotFoundError: if no entity found from any source.
            EntityAmbiguityError: if >3 Registry Lookup matches returned;
                                  inspect ``.candidates`` and call again with
                                  the chosen canonical name.
        """
        key = _cache_key(raw_input)

        if settings.use_cache:
            cached = await _get_cached(key)
            if cached:
                logger.info("Entity cache hit for '%s'", raw_input)
                return cached

        async with httpx.AsyncClient() as client:
            entity = await self._resolve_uncached(client, raw_input)

        if settings.use_cache:
            await _set_cached(key, entity)

        return entity

    async def _resolve_uncached(
        self, client: httpx.AsyncClient, raw_input: str
    ) -> ResolvedEntity:
        rl_results, rl_blocked = await _rl_search(client, raw_input)

        # Ambiguity: >3 matches from Registry Lookup
        if len(rl_results) > 3:
            raise EntityAmbiguityError(
                [_parse_rl_result(r, raw_input) for r in rl_results[:3]]
            )

        if rl_results:
            entity = _parse_rl_result(rl_results[0], raw_input)
        else:
            # Build minimal stub — may be enriched by EDGAR / Companies House below
            canonical = raw_input.strip().upper()
            entity = ResolvedEntity(
                canonical_name=canonical,
                aliases=_generate_aliases(canonical, raw_input, []),
            )

        # UK enrichment via Companies House
        if entity.jurisdiction and "gb" in entity.jurisdiction:
            entity = await self._enrich_companies_house(client, entity, raw_input)

        # US / unknown: try SEC EDGAR for public company enrichment
        if not entity.sec_cik and (entity.is_public or not entity.registry_lookup_id):
            edgar = await _edgar_lookup(client, entity.canonical_name)
            if edgar.get("cik"):
                edg_canonical = edgar.get("canonical_name") or entity.canonical_name
                entity = entity.model_copy(update={
                    "canonical_name": edg_canonical,
                    "aliases": _generate_aliases(edg_canonical, raw_input, edgar.get("tickers", [])),
                    "sec_cik": edgar["cik"],
                    "is_public": True,
                })

        # Nothing found from any authoritative source
        if not rl_results and not entity.sec_cik:
            if rl_blocked:
                # Registry Lookup is geo-blocked; we can't confirm non-existence.
                # Return a best-effort stub so private companies still flow through.
                logger.warning(
                    "Degraded resolution for '%s': RL geo-blocked, not in EDGAR "
                    "(likely private company). Returning stub entity.",
                    raw_input,
                )
                return entity
            raise EntityNotFoundError(
                f"No entity found for '{raw_input}' in Registry Lookup or SEC EDGAR."
            )

        return entity

    async def _enrich_companies_house(
        self,
        client: httpx.AsyncClient,
        entity: ResolvedEntity,
        raw_input: str,
    ) -> ResolvedEntity:
        items = await _ch_search(client, entity.canonical_name)
        if not items:
            return entity
        top = items[0]
        ch_name = top.get("title", entity.canonical_name)
        return entity.model_copy(update={
            "canonical_name": ch_name,
            "aliases": _generate_aliases(ch_name, raw_input, []),
            "companies_house_number": top.get("company_number"),
        })
