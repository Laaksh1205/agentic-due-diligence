"""
SQLite Storage Layer — Task 1.7.1

Five tables:
  entities   — resolved entity cache (7-day TTL)
  documents  — raw documents fetched during research
  signals    — extracted risk signals
  reports    — final DueDiligenceReport blobs
  audit_log  — immutable record of every significant action

All operations are async via aiosqlite.
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from src.config import settings

logger = logging.getLogger(__name__)

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS entities (
    canonical_name TEXT PRIMARY KEY,
    json_blob      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    entity_name     TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    raw_text        TEXT NOT NULL,
    fetch_timestamp TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_documents_run    ON documents(run_id);
CREATE INDEX IF NOT EXISTS idx_documents_entity ON documents(entity_name);

CREATE TABLE IF NOT EXISTS signals (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    json_blob   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_run ON signals(run_id);

CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    entity_name TEXT NOT NULL,
    json_blob   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT,
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id);
"""


# ── Connection context manager ────────────────────────────────────────────────

@asynccontextmanager
async def _db():
    """Open a connection, yield it, close it — row_factory pre-set."""
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


# ── Schema init ───────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables (idempotent — safe to call on every startup)."""
    async with _db() as conn:
        await conn.executescript(_DDL)
        await conn.commit()
    logger.info("Database initialised at %s", settings.database_path)


# ── Entities ──────────────────────────────────────────────────────────────────

async def upsert_entity(entity_json: dict, ttl_hours: int = 168) -> None:
    """Insert or replace a resolved entity cache entry."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl_hours)

    async with _db() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO entities(canonical_name, json_blob, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                entity_json.get("canonical_name", ""),
                json.dumps(entity_json),
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        await conn.commit()


async def get_entity(canonical_name: str) -> Optional[dict]:
    """Return a cached entity dict, or None if missing/expired."""
    now = datetime.now(timezone.utc).isoformat()
    async with _db() as conn:
        async with conn.execute(
            "SELECT json_blob FROM entities WHERE canonical_name = ? AND expires_at > ? LIMIT 1",
            (canonical_name, now),
        ) as cur:
            row = await cur.fetchone()
    return json.loads(row["json_blob"]) if row else None


# ── Documents ─────────────────────────────────────────────────────────────────

async def insert_document(run_id: str, doc_json: dict) -> str:
    """Persist a RawDocument; returns the inserted row id."""
    doc_id = str(uuid.uuid4())
    async with _db() as conn:
        await conn.execute(
            """
            INSERT INTO documents(id, run_id, entity_name, source_url, source_type,
                                  raw_text, fetch_timestamp, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                run_id,
                doc_json.get("entity_name", ""),
                doc_json.get("source_url", ""),
                doc_json.get("source_type", ""),
                doc_json.get("raw_text", ""),
                doc_json.get("fetch_timestamp", datetime.now(timezone.utc).isoformat()),
                json.dumps(doc_json.get("metadata", {})),
            ),
        )
        await conn.commit()
    return doc_id


async def get_documents_for_run(run_id: str) -> list[dict]:
    async with _db() as conn:
        async with conn.execute(
            "SELECT * FROM documents WHERE run_id = ?", (run_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Signals ───────────────────────────────────────────────────────────────────

async def insert_signal(run_id: str, signal_json: dict) -> str:
    signal_id = signal_json.get("id") or str(uuid.uuid4())
    async with _db() as conn:
        await conn.execute(
            """
            INSERT INTO signals(id, run_id, entity_name, json_blob, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(signal_id),
                run_id,
                signal_json.get("entity_name", ""),
                json.dumps(signal_json),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await conn.commit()
    return str(signal_id)


async def get_signals_for_run(run_id: str) -> list[dict]:
    async with _db() as conn:
        async with conn.execute(
            "SELECT json_blob FROM signals WHERE run_id = ?", (run_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [json.loads(r["json_blob"]) for r in rows]


# ── Reports ───────────────────────────────────────────────────────────────────

async def save_report(run_id: str, entity_name: str, report_json: dict) -> None:
    async with _db() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO reports(id, entity_name, json_blob, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                run_id,
                entity_name,
                json.dumps(report_json),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await conn.commit()


async def get_report(run_id: str) -> Optional[dict]:
    async with _db() as conn:
        async with conn.execute(
            "SELECT json_blob FROM reports WHERE id = ?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
    return json.loads(row["json_blob"]) if row else None


async def list_reports() -> list[dict]:
    """Return summary rows for the run history page."""
    async with _db() as conn:
        async with conn.execute(
            "SELECT id, entity_name, created_at FROM reports ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Audit log ─────────────────────────────────────────────────────────────────

async def audit(event: str, detail: str = "", run_id: Optional[str] = None) -> None:
    """Append an immutable audit entry. Swallows errors so it never breaks callers."""
    try:
        async with _db() as conn:
            await conn.execute(
                "INSERT INTO audit_log(run_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
                (run_id, event, detail, datetime.now(timezone.utc).isoformat()),
            )
            await conn.commit()
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)


async def get_audit_log(run_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    """Retrieve audit entries, optionally filtered by run_id."""
    async with _db() as conn:
        if run_id:
            async with conn.execute(
                "SELECT * FROM audit_log WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                (run_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
    return [dict(r) for r in rows]
