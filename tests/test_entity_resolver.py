"""
Task 1.3.4 — Unit tests for EntityResolver.

Registry Lookup is geo-blocked outside the US, so all tests mock _rl_search.
EDGAR and Companies House tests include both mocked units and one live
integration test (requires internet access; no VPN needed).
Run: pytest tests/test_entity_resolver.py -v
Run live tests only: pytest tests/test_entity_resolver.py -v -m integration
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.models.entities import ResolvedEntity
from src.resolution.entity_resolver import (
    EntityAmbiguityError,
    EntityNotFoundError,
    EntityResolver,
    _cache_key,
    _generate_aliases,
    _strip_suffixes,
    _get_cached,
    _set_cached,
    _cache_key,
)


# ── Utility tests ──────────────────────────────────────────────────────────────

class TestHelpers:
    def test_strip_suffixes_inc(self):
        assert _strip_suffixes("Tesla, Inc.") == "Tesla"

    def test_strip_suffixes_llc(self):
        assert _strip_suffixes("Acme LLC") == "Acme"

    def test_strip_suffixes_ltd(self):
        assert _strip_suffixes("Revolut Ltd.") == "Revolut"

    def test_strip_suffixes_corp(self):
        assert _strip_suffixes("Boeing Corp") == "Boeing"

    def test_strip_suffixes_no_suffix(self):
        assert _strip_suffixes("Stripe") == "Stripe"

    def test_generate_aliases_deduplicates(self):
        aliases = _generate_aliases("Tesla, Inc.", "Tesla Inc", ["TSLA"])
        assert "Tesla, Inc." in aliases
        assert "Tesla Inc" in aliases
        assert "TSLA" in aliases
        # No duplicates
        assert len(aliases) == len(set(aliases))

    def test_generate_aliases_stripped_variants(self):
        aliases = _generate_aliases("TESLA, INC.", "Tesla Inc.", ["TSLA"])
        # Stripped canonical ("TESLA") and stripped raw ("Tesla Inc") should appear
        assert "TESLA" in aliases

    def test_cache_key_stable(self):
        assert _cache_key("Tesla Inc") == _cache_key("Tesla Inc")

    def test_cache_key_case_insensitive(self):
        assert _cache_key("TESLA") == _cache_key("tesla") == _cache_key("Tesla")

    def test_cache_key_different_inputs(self):
        assert _cache_key("Tesla") != _cache_key("Stripe")


# ── EntityResolver unit tests (all external calls mocked) ────────────────────

MOCK_TESLA_EDGAR = {
    "cik": "0001318605",
    "canonical_name": "Tesla, Inc.",
    "tickers": ["TSLA"],
    "state": "TX",
}

MOCK_STRIPE_RL = [
    {
        "id": "abc123",
        "legal_name": "Stripe, Inc.",
        "jurisdiction_code": "us-de",
        "company_type": "Private",
    }
]

MOCK_REVOLUT_RL = [
    {
        "id": "uk001",
        "legal_name": "REVOLUT LTD",
        "jurisdiction_code": "gb",
        "company_type": "Private Limited Company",
    }
]

MOCK_REVOLUT_CH = [
    {
        "title": "REVOLUT LTD",
        "company_number": "08804411",
        "company_status": "active",
    }
]

MOCK_AMBIGUOUS_RL = [
    {"id": "1", "legal_name": "Mercury Corp A", "jurisdiction_code": "us-de"},
    {"id": "2", "legal_name": "Mercury Corp B", "jurisdiction_code": "us-ca"},
    {"id": "3", "legal_name": "Mercury Corp C", "jurisdiction_code": "us-ny"},
    {"id": "4", "legal_name": "Mercury Corp D", "jurisdiction_code": "gb"},
]


@pytest.fixture
def resolver():
    return EntityResolver()


class TestEntityResolverUnit:
    """All Registry Lookup + EDGAR + Companies House calls are mocked."""

    async def test_resolve_known_public_company(self, resolver, tmp_path, monkeypatch):
        """Tesla: RL geo-blocked (returns []), EDGAR returns CIK → is_public=True."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=([], True)), \
             patch("src.resolution.entity_resolver._edgar_lookup", new_callable=AsyncMock, return_value=MOCK_TESLA_EDGAR):
            entity = await resolver.resolve("Tesla Inc")

        assert entity.canonical_name == "Tesla, Inc."
        assert entity.is_public is True
        assert entity.sec_cik == "0001318605"
        assert "TSLA" in entity.aliases
        assert "Tesla" in entity.aliases or "Tesla Inc" in entity.aliases

    async def test_resolve_known_private_company(self, resolver, tmp_path, monkeypatch):
        """Stripe: RL returns result, EDGAR returns nothing → is_public=False."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=(MOCK_STRIPE_RL, False)), \
             patch("src.resolution.entity_resolver._edgar_lookup", new_callable=AsyncMock, return_value={}):
            entity = await resolver.resolve("Stripe")

        assert "Stripe" in entity.canonical_name
        assert entity.is_public is False
        assert entity.sec_cik is None
        assert entity.jurisdiction == "us-de"

    async def test_resolve_uk_company_enriched(self, resolver, tmp_path, monkeypatch):
        """Revolut: RL returns gb jurisdiction → Companies House enriched."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=(MOCK_REVOLUT_RL, False)), \
             patch("src.resolution.entity_resolver._ch_search", new_callable=AsyncMock, return_value=MOCK_REVOLUT_CH), \
             patch("src.resolution.entity_resolver._edgar_lookup", new_callable=AsyncMock, return_value={}):
            entity = await resolver.resolve("Revolut")

        assert entity.companies_house_number == "08804411"
        assert "REVOLUT" in entity.canonical_name.upper()

    async def test_resolve_ambiguous_name(self, resolver, tmp_path, monkeypatch):
        """4 RL results → EntityAmbiguityError with 3 candidates."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=(MOCK_AMBIGUOUS_RL, False)), \
             pytest.raises(EntityAmbiguityError) as exc_info:
            await resolver.resolve("Mercury Corp")

        err = exc_info.value
        assert len(err.candidates) == 3
        assert all(isinstance(c, ResolvedEntity) for c in err.candidates)

    async def test_resolve_not_found(self, resolver, tmp_path, monkeypatch):
        """RL empty, EDGAR empty → EntityNotFoundError."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=([], False)), \
             patch("src.resolution.entity_resolver._edgar_lookup", new_callable=AsyncMock, return_value={}), \
             pytest.raises(EntityNotFoundError):
            await resolver.resolve("XYZ_DOES_NOT_EXIST_12345")

    async def test_cache_hit(self, resolver, tmp_path, monkeypatch):
        """Second resolve call uses cache — external APIs not called again."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", True)

        rl_mock = AsyncMock(return_value=(MOCK_STRIPE_RL, False))
        edgar_mock = AsyncMock(return_value={})

        with patch("src.resolution.entity_resolver._rl_search", rl_mock), \
             patch("src.resolution.entity_resolver._edgar_lookup", edgar_mock):
            e1 = await resolver.resolve("Stripe")
            e2 = await resolver.resolve("Stripe")

        assert e1.canonical_name == e2.canonical_name
        # RL should have been called only once (second call served from cache)
        assert rl_mock.call_count == 1

    async def test_edgar_fallback_when_rl_blocked(self, resolver, tmp_path, monkeypatch):
        """RL returns empty (geo-blocked), EDGAR finds the company."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=([], True)), \
             patch("src.resolution.entity_resolver._edgar_lookup", new_callable=AsyncMock, return_value=MOCK_TESLA_EDGAR):
            entity = await resolver.resolve("Tesla")

        assert entity.is_public is True
        assert entity.sec_cik == "0001318605"

    async def test_ambiguity_returns_exactly_3_candidates(self, resolver, tmp_path, monkeypatch):
        """Always cap ambiguity candidates at 3 even when more results exist."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        many = [{"id": str(i), "legal_name": f"Acme #{i}", "jurisdiction_code": "us-de"} for i in range(10)]
        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=(many, False)), \
             pytest.raises(EntityAmbiguityError) as exc_info:
            await resolver.resolve("Acme")

        assert len(exc_info.value.candidates) == 3


# ── Integration tests (live API — no VPN needed) ──────────────────────────────

@pytest.mark.integration
class TestEntityResolverIntegration:
    """These tests call real APIs. Registry Lookup will return [] (geo-blocked)
    but EDGAR and Companies House are accessible from any country."""

    async def test_resolve_tesla_live(self, tmp_path, monkeypatch):
        """Tesla resolves via EDGAR (RL geo-blocked from India)."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        resolver = EntityResolver()
        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=([], True)):
            entity = await resolver.resolve("Tesla")

        assert entity.is_public is True
        assert "1318605" in entity.sec_cik
        assert "TSLA" in entity.aliases
        assert "Tesla" in entity.canonical_name

    async def test_resolve_stripe_via_rl_mock_and_edgar(self, tmp_path, monkeypatch):
        """Stripe: RL mocked (geo-blocked), EDGAR should return no CIK (private)."""
        monkeypatch.setattr("src.config.settings.database_path", str(tmp_path / "test.db"))
        monkeypatch.setattr("src.config.settings.use_cache", False)

        resolver = EntityResolver()
        with patch("src.resolution.entity_resolver._rl_search", new_callable=AsyncMock, return_value=(MOCK_STRIPE_RL, False)):
            entity = await resolver.resolve("Stripe")

        assert entity.is_public is False
        assert entity.sec_cik is None
