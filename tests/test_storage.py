"""
Tests for src/storage/database.py and src/storage/cache.py — Task 1.7

Uses a temporary SQLite file per test (monkeypatched via settings.database_path)
so tests are isolated and leave no side-effects.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.config import settings


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Point every test at a fresh temporary database."""
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "use_cache", True)


# ── database.py ───────────────────────────────────────────────────────────────

class TestInitDb:
    async def test_init_creates_tables(self):
        from src.storage.database import init_db
        import aiosqlite

        await init_db()
        async with aiosqlite.connect(settings.database_path) as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                names = {r[0] for r in await cur.fetchall()}
        for table in ("entities", "documents", "signals", "reports", "audit_log"):
            assert table in names

    async def test_init_is_idempotent(self):
        from src.storage.database import init_db

        await init_db()
        await init_db()  # second call must not raise


class TestEntities:
    async def test_upsert_and_get(self):
        from src.storage.database import get_entity, init_db, upsert_entity

        await init_db()
        entity = {"canonical_name": "Acme Corp", "aliases": ["Acme"], "is_public": False}
        await upsert_entity(entity)
        result = await get_entity("Acme Corp")
        assert result is not None
        assert result["canonical_name"] == "Acme Corp"

    async def test_get_missing_returns_none(self):
        from src.storage.database import get_entity, init_db

        await init_db()
        assert await get_entity("Nonexistent Co") is None

    async def test_get_expired_returns_none(self):
        from src.storage.database import get_entity, init_db, upsert_entity
        import aiosqlite

        await init_db()
        entity = {"canonical_name": "OldCo", "aliases": []}
        await upsert_entity(entity, ttl_hours=1)

        # Manually backdate the expires_at to the past
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        async with aiosqlite.connect(settings.database_path) as conn:
            await conn.execute(
                "UPDATE entities SET expires_at = ? WHERE canonical_name = ?",
                (past, "OldCo"),
            )
            await conn.commit()

        assert await get_entity("OldCo") is None

    async def test_upsert_replaces_existing(self):
        from src.storage.database import get_entity, init_db, upsert_entity

        await init_db()
        await upsert_entity({"canonical_name": "Acme Corp", "is_public": False})
        await upsert_entity({"canonical_name": "Acme Corp", "is_public": True})
        result = await get_entity("Acme Corp")
        assert result["is_public"] is True


class TestDocuments:
    async def test_insert_and_retrieve(self):
        from src.storage.database import get_documents_for_run, init_db, insert_document

        await init_db()
        run_id = str(uuid.uuid4())
        doc = {
            "entity_name": "Acme",
            "source_url": "https://example.com/news",
            "source_type": "NEWS_ARTICLE",
            "raw_text": "Acme faces lawsuit.",
            "metadata": {"title": "Acme News"},
        }
        doc_id = await insert_document(run_id, doc)
        assert doc_id

        rows = await get_documents_for_run(run_id)
        assert len(rows) == 1
        assert rows[0]["source_url"] == "https://example.com/news"

    async def test_documents_isolated_by_run(self):
        from src.storage.database import get_documents_for_run, init_db, insert_document

        await init_db()
        run_a, run_b = str(uuid.uuid4()), str(uuid.uuid4())
        doc = {"entity_name": "X", "source_url": "u", "source_type": "NEWS_ARTICLE", "raw_text": "t"}
        await insert_document(run_a, doc)
        await insert_document(run_b, doc)

        assert len(await get_documents_for_run(run_a)) == 1
        assert len(await get_documents_for_run(run_b)) == 1

    async def test_multiple_documents_same_run(self):
        from src.storage.database import get_documents_for_run, init_db, insert_document

        await init_db()
        run_id = str(uuid.uuid4())
        for i in range(5):
            await insert_document(run_id, {
                "entity_name": "Acme",
                "source_url": f"https://example.com/{i}",
                "source_type": "NEWS_ARTICLE",
                "raw_text": f"doc {i}",
            })
        rows = await get_documents_for_run(run_id)
        assert len(rows) == 5


class TestSignals:
    def _signal(self, entity="Acme"):
        return {
            "id": str(uuid.uuid4()),
            "entity_name": entity,
            "text": "Acme faces fine",
            "source_url": "https://example.com",
            "source_type": "NEWS_ARTICLE",
            "risk_category": "LEGAL",
            "severity": "HIGH",
            "signal_polarity": "NEGATIVE",
            "confidence_score": 0.9,
            "source_snippet": "Acme faces fine for violations",
        }

    async def test_insert_and_retrieve(self):
        from src.storage.database import get_signals_for_run, init_db, insert_signal

        await init_db()
        run_id = str(uuid.uuid4())
        sig = self._signal()
        await insert_signal(run_id, sig)

        rows = await get_signals_for_run(run_id)
        assert len(rows) == 1
        assert rows[0]["text"] == "Acme faces fine"

    async def test_signals_isolated_by_run(self):
        from src.storage.database import get_signals_for_run, init_db, insert_signal

        await init_db()
        run_a, run_b = str(uuid.uuid4()), str(uuid.uuid4())
        await insert_signal(run_a, self._signal())
        await insert_signal(run_b, self._signal())
        assert len(await get_signals_for_run(run_a)) == 1

    async def test_signal_json_roundtrip(self):
        from src.storage.database import get_signals_for_run, init_db, insert_signal

        await init_db()
        run_id = str(uuid.uuid4())
        sig = self._signal()
        sig["confidence_score"] = 0.75
        await insert_signal(run_id, sig)

        rows = await get_signals_for_run(run_id)
        assert rows[0]["confidence_score"] == 0.75


class TestReports:
    async def test_save_and_get(self):
        from src.storage.database import get_report, init_db, save_report

        await init_db()
        run_id = str(uuid.uuid4())
        report = {"entity": "Acme", "score": 7.5}
        await save_report(run_id, "Acme", report)

        result = await get_report(run_id)
        assert result is not None
        assert result["score"] == 7.5

    async def test_get_missing_report_returns_none(self):
        from src.storage.database import get_report, init_db

        await init_db()
        assert await get_report(str(uuid.uuid4())) is None

    async def test_list_reports(self):
        from src.storage.database import init_db, list_reports, save_report

        await init_db()
        for i in range(3):
            await save_report(str(uuid.uuid4()), f"Corp{i}", {"n": i})

        rows = await list_reports()
        assert len(rows) == 3
        for row in rows:
            assert "id" in row
            assert "entity_name" in row
            assert "created_at" in row

    async def test_upsert_overwrites_existing_report(self):
        from src.storage.database import get_report, init_db, save_report

        await init_db()
        run_id = str(uuid.uuid4())
        await save_report(run_id, "Acme", {"v": 1})
        await save_report(run_id, "Acme", {"v": 2})
        assert (await get_report(run_id))["v"] == 2


class TestAuditLog:
    async def test_audit_writes_entry(self):
        from src.storage.database import audit, get_audit_log, init_db

        await init_db()
        run_id = str(uuid.uuid4())
        await audit("signal_rejected", "score < 70", run_id=run_id)

        entries = await get_audit_log(run_id=run_id)
        assert len(entries) == 1
        assert entries[0]["event"] == "signal_rejected"
        assert entries[0]["detail"] == "score < 70"

    async def test_audit_without_run_id(self):
        from src.storage.database import audit, get_audit_log, init_db

        await init_db()
        await audit("startup", "system initialised")
        entries = await get_audit_log()
        assert any(e["event"] == "startup" for e in entries)

    async def test_audit_log_filtered_by_run(self):
        from src.storage.database import audit, get_audit_log, init_db

        await init_db()
        run_a, run_b = str(uuid.uuid4()), str(uuid.uuid4())
        await audit("ev_a", run_id=run_a)
        await audit("ev_b", run_id=run_b)

        entries = await get_audit_log(run_id=run_a)
        assert all(e["event"] == "ev_a" for e in entries)

    async def test_audit_never_raises(self):
        """audit() must swallow errors — broken DB path should not crash."""
        from src.storage import database as db_mod
        import unittest.mock as mock

        with mock.patch.object(db_mod, "_db", side_effect=Exception("db down")):
            await db_mod.audit("test_event", "should not raise")  # no exception


# ── cache.py ──────────────────────────────────────────────────────────────────

class TestDocumentCache:
    async def test_miss_returns_none(self):
        from src.storage.cache import get_cached

        result = await get_cached("https://example.com", "Acme")
        assert result is None

    async def test_set_and_get(self):
        from src.storage.cache import get_cached, set_cached

        doc = {"source_url": "https://example.com", "raw_text": "hello"}
        await set_cached("https://example.com", "Acme", doc)
        result = await get_cached("https://example.com", "Acme")
        assert result is not None
        assert result["raw_text"] == "hello"

    async def test_expired_returns_none(self):
        from src.storage.cache import get_cached, set_cached
        import aiosqlite

        doc = {"source_url": "u", "raw_text": "t"}
        await set_cached("u", "Acme", doc, ttl_hours=1)

        # Backdate expiry
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        async with aiosqlite.connect(settings.database_path) as conn:
            await conn.execute("UPDATE document_cache SET expires_at = ?", (past,))
            await conn.commit()

        assert await get_cached("u", "Acme") is None

    async def test_cache_key_includes_entity(self):
        from src.storage.cache import get_cached, set_cached

        doc_a = {"raw_text": "for Acme"}
        doc_b = {"raw_text": "for Beta"}
        await set_cached("https://example.com", "Acme", doc_a)
        await set_cached("https://example.com", "Beta Corp", doc_b)

        assert (await get_cached("https://example.com", "Acme"))["raw_text"] == "for Acme"
        assert (await get_cached("https://example.com", "Beta Corp"))["raw_text"] == "for Beta"

    async def test_cache_disabled_get_returns_none(self, monkeypatch):
        from src.storage.cache import get_cached, set_cached

        doc = {"raw_text": "cached"}
        await set_cached("https://example.com", "X", doc)

        monkeypatch.setattr(settings, "use_cache", False)
        assert await get_cached("https://example.com", "X") is None

    async def test_cache_disabled_set_is_noop(self, monkeypatch):
        from src.storage.cache import get_cached, set_cached

        monkeypatch.setattr(settings, "use_cache", False)
        await set_cached("https://example.com", "X", {"raw_text": "data"})

        monkeypatch.setattr(settings, "use_cache", True)
        assert await get_cached("https://example.com", "X") is None

    async def test_invalidate_removes_entry(self):
        from src.storage.cache import get_cached, invalidate, set_cached

        await set_cached("https://u.com", "Acme", {"raw_text": "data"})
        await invalidate("https://u.com", "Acme")
        assert await get_cached("https://u.com", "Acme") is None

    async def test_purge_expired(self):
        from src.storage.cache import get_cached, purge_expired, set_cached
        import aiosqlite

        await set_cached("https://a.com", "A", {"raw_text": "a"})
        await set_cached("https://b.com", "B", {"raw_text": "b"})

        # Expire first entry
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with aiosqlite.connect(settings.database_path) as conn:
            await conn.execute(
                "UPDATE document_cache SET expires_at = ? WHERE cache_key IN "
                "(SELECT cache_key FROM document_cache LIMIT 1)",
                (past,),
            )
            await conn.commit()

        deleted = await purge_expired()
        assert deleted == 1

    async def test_set_overwrites_existing(self):
        from src.storage.cache import get_cached, set_cached

        await set_cached("https://u.com", "Acme", {"v": 1})
        await set_cached("https://u.com", "Acme", {"v": 2})
        assert (await get_cached("https://u.com", "Acme"))["v"] == 2
