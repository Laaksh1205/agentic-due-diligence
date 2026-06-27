"""
Document Cache — Task 1.7.2

Thin TTL cache for RawDocument objects backed by the same SQLite database.
Stored in a separate `document_cache` table for keyed lookups.

Cache key = sha256(source_url + "::" + entity_name)
Default TTL = settings.cache_ttl_hours (168 h = 7 days)

Respects settings.use_cache: when False, get_cached() always returns None
and set_cached() is a no-op, so no special-casing is needed in callers.
"""

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from src.config import settings

logger = logging.getLogger(__name__)

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS document_cache (
    cache_key  TEXT PRIMARY KEY,
    json_blob  TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_key(source_url: str, entity_name: str) -> str:
    raw = f"{source_url}::{entity_name}"
    return hashlib.sha256(raw.encode()).hexdigest()


@asynccontextmanager
async def _db():
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_CACHE_DDL)
        await conn.commit()
        yield conn


# ── Public API ────────────────────────────────────────────────────────────────

async def get_cached(source_url: str, entity_name: str) -> Optional[dict]:
    """Return the cached RawDocument dict, or None on miss/expiry/cache-disabled."""
    if not settings.use_cache:
        return None

    key = _make_key(source_url, entity_name)
    now = datetime.now(timezone.utc).isoformat()

    try:
        async with _db() as conn:
            async with conn.execute(
                "SELECT json_blob FROM document_cache WHERE cache_key = ? AND expires_at > ?",
                (key, now),
            ) as cur:
                row = await cur.fetchone()
        if row:
            logger.debug("Cache hit for %s / %s", entity_name, source_url[:60])
            return json.loads(row["json_blob"])
        return None
    except Exception as exc:
        logger.warning("Cache read error: %s", exc)
        return None


async def set_cached(
    source_url: str,
    entity_name: str,
    document: dict,
    ttl_hours: Optional[int] = None,
) -> None:
    """Store a RawDocument dict in the cache. No-op when cache is disabled."""
    if not settings.use_cache:
        return

    key = _make_key(source_url, entity_name)
    hours = ttl_hours if ttl_hours is not None else settings.cache_ttl_hours
    expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    try:
        async with _db() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO document_cache(cache_key, json_blob, expires_at)
                VALUES (?, ?, ?)
                """,
                (key, json.dumps(document), expires),
            )
            await conn.commit()
        logger.debug("Cached %s / %s (TTL %dh)", entity_name, source_url[:60], hours)
    except Exception as exc:
        logger.warning("Cache write error: %s", exc)


async def invalidate(source_url: str, entity_name: str) -> None:
    """Remove a specific cache entry."""
    key = _make_key(source_url, entity_name)
    try:
        async with _db() as conn:
            await conn.execute(
                "DELETE FROM document_cache WHERE cache_key = ?", (key,)
            )
            await conn.commit()
    except Exception as exc:
        logger.warning("Cache invalidation error: %s", exc)


async def purge_expired() -> int:
    """Delete all expired entries; returns the number of rows removed."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with _db() as conn:
            async with conn.execute(
                "DELETE FROM document_cache WHERE expires_at <= ?", (now,)
            ) as cur:
                deleted = cur.rowcount
            await conn.commit()
        logger.info("Cache purged %d expired entries", deleted)
        return deleted
    except Exception as exc:
        logger.warning("Cache purge error: %s", exc)
        return 0
