"""Tests for src/agents/data_sufficiency.py — Task 1.6.2"""

import pytest

from src.agents.data_sufficiency import assess_sufficiency
from src.models.documents import DataSufficiency, RawDocument
from src.models.signals import SourceType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _doc(source_type: SourceType, n: int = 1) -> list[RawDocument]:
    return [
        RawDocument(
            source_url=f"https://example.com/{source_type.value}/{i}",
            source_type=source_type,
            raw_text="sample text",
            entity_name="Acme Corp",
        )
        for i in range(n)
    ]


def _docs_of_types(*pairs: tuple[SourceType, int]) -> list[RawDocument]:
    """Build a document list from (source_type, count) pairs."""
    out: list[RawDocument] = []
    for source_type, count in pairs:
        out.extend(_doc(source_type, count))
    return out


# ── SPARSE ────────────────────────────────────────────────────────────────────

class TestSparse:
    def test_zero_documents(self):
        assert assess_sufficiency([]) == DataSufficiency.SPARSE

    def test_one_document_single_type(self):
        docs = _doc(SourceType.NEWS_ARTICLE, 1)
        assert assess_sufficiency(docs) == DataSufficiency.SPARSE

    def test_three_documents_single_type(self):
        """3 docs is below 4 — SPARSE even with 1 type."""
        docs = _doc(SourceType.NEWS_ARTICLE, 3)
        assert assess_sufficiency(docs) == DataSufficiency.SPARSE

    def test_three_documents_two_types(self):
        """Volume < 4 → SPARSE regardless of diversity."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 2),
            (SourceType.COMPANY_REGISTRY, 1),
        )
        assert assess_sufficiency(docs) == DataSufficiency.SPARSE

    def test_four_documents_single_type(self):
        """Volume meets LIMITED threshold but diversity (1) does not → SPARSE."""
        docs = _doc(SourceType.NEWS_ARTICLE, 4)
        assert assess_sufficiency(docs) == DataSufficiency.SPARSE

    def test_seven_documents_single_type(self):
        """Volume meets LIMITED threshold but diversity (1) does not → SPARSE."""
        docs = _doc(SourceType.SEC_FILING, 7)
        assert assess_sufficiency(docs) == DataSufficiency.SPARSE

    def test_twenty_documents_single_type(self):
        """15 docs, 1 source type — volume alone does not make RICH."""
        docs = _doc(SourceType.NEWS_ARTICLE, 20)
        assert assess_sufficiency(docs) == DataSufficiency.SPARSE

    def test_fifteen_documents_single_type(self):
        """Boundary: 15 docs but only 1 source type → SPARSE (design plan spec)."""
        docs = _doc(SourceType.NEWS_ARTICLE, 15)
        assert assess_sufficiency(docs) == DataSufficiency.SPARSE


# ── LIMITED ───────────────────────────────────────────────────────────────────

class TestLimited:
    def test_four_docs_two_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 2),
            (SourceType.COMPANY_REGISTRY, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.LIMITED

    def test_seven_docs_two_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 4),
            (SourceType.COMPANY_WEBSITE, 3),
        )
        assert assess_sufficiency(docs) == DataSufficiency.LIMITED

    def test_seven_docs_three_types(self):
        """Volume (7) < 8 → doesn't reach ADEQUATE even with 3 types."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 3),
            (SourceType.COMPANY_REGISTRY, 2),
            (SourceType.COMPANY_WEBSITE, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.LIMITED

    def test_five_docs_two_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 3),
            (SourceType.SEC_FILING, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.LIMITED

    def test_boundary_four_docs_exactly(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 2),
            (SourceType.COURT_RECORD, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.LIMITED


# ── ADEQUATE ──────────────────────────────────────────────────────────────────

class TestAdequate:
    def test_eight_docs_three_types(self):
        """Spec edge case from plan: 8 docs from 3 types → ADEQUATE."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 3),
            (SourceType.COMPANY_REGISTRY, 3),
            (SourceType.COMPANY_WEBSITE, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.ADEQUATE

    def test_fourteen_docs_three_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 6),
            (SourceType.SEC_FILING, 4),
            (SourceType.COMPANY_REGISTRY, 4),
        )
        assert assess_sufficiency(docs) == DataSufficiency.ADEQUATE

    def test_ten_docs_three_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 4),
            (SourceType.COMPANY_REGISTRY, 3),
            (SourceType.SEC_FILING, 3),
        )
        assert assess_sufficiency(docs) == DataSufficiency.ADEQUATE

    def test_fourteen_docs_two_types(self):
        """Volume meets ADEQUATE but diversity (2) does not → falls to LIMITED."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 7),
            (SourceType.SEC_FILING, 7),
        )
        assert assess_sufficiency(docs) == DataSufficiency.LIMITED

    def test_eight_docs_four_types(self):
        """Volume (8) < 15 → doesn't reach RICH even with 4 types."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 2),
            (SourceType.COMPANY_REGISTRY, 2),
            (SourceType.SEC_FILING, 2),
            (SourceType.COMPANY_WEBSITE, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.ADEQUATE

    def test_boundary_eight_docs_exactly(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 3),
            (SourceType.COMPANY_REGISTRY, 3),
            (SourceType.COURT_RECORD, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.ADEQUATE


# ── RICH ──────────────────────────────────────────────────────────────────────

class TestRich:
    def test_fifteen_docs_four_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 5),
            (SourceType.COMPANY_REGISTRY, 4),
            (SourceType.SEC_FILING, 3),
            (SourceType.COMPANY_WEBSITE, 3),
        )
        assert assess_sufficiency(docs) == DataSufficiency.RICH

    def test_twenty_docs_five_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 5),
            (SourceType.COMPANY_REGISTRY, 4),
            (SourceType.SEC_FILING, 4),
            (SourceType.COMPANY_WEBSITE, 4),
            (SourceType.COURT_RECORD, 3),
        )
        assert assess_sufficiency(docs) == DataSufficiency.RICH

    def test_boundary_fifteen_docs_exactly_four_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 4),
            (SourceType.COMPANY_REGISTRY, 4),
            (SourceType.SEC_FILING, 4),
            (SourceType.COMPANY_WEBSITE, 3),
        )
        assert assess_sufficiency(docs) == DataSufficiency.RICH

    def test_fifteen_docs_three_types(self):
        """Volume (15) meets RICH threshold but diversity (3) does not → ADEQUATE."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 7),
            (SourceType.SEC_FILING, 5),
            (SourceType.COMPANY_REGISTRY, 3),
        )
        assert assess_sufficiency(docs) == DataSufficiency.ADEQUATE

    def test_all_seven_source_types(self):
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 3),
            (SourceType.COMPANY_REGISTRY, 3),
            (SourceType.SEC_FILING, 3),
            (SourceType.COMPANY_WEBSITE, 2),
            (SourceType.COURT_RECORD, 2),
            (SourceType.SANCTIONS_LIST, 1),
            (SourceType.INTERNAL_DOC, 1),
        )
        assert assess_sufficiency(docs) == DataSufficiency.RICH


# ── Boundary / cross-tier edge cases ─────────────────────────────────────────

class TestBoundaries:
    def test_diversity_is_only_unique_types(self):
        """100 docs of 1 type should still be SPARSE."""
        docs = _doc(SourceType.NEWS_ARTICLE, 100)
        assert assess_sufficiency(docs) == DataSufficiency.SPARSE

    def test_exactly_at_sparse_limited_boundary(self):
        """4 docs, 2 types = minimum for LIMITED."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 2),
            (SourceType.SEC_FILING, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.LIMITED

    def test_exactly_at_limited_adequate_boundary(self):
        """8 docs, 3 types = minimum for ADEQUATE."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 3),
            (SourceType.SEC_FILING, 3),
            (SourceType.COMPANY_WEBSITE, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.ADEQUATE

    def test_exactly_at_adequate_rich_boundary(self):
        """15 docs, 4 types = minimum for RICH."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 4),
            (SourceType.SEC_FILING, 4),
            (SourceType.COMPANY_WEBSITE, 4),
            (SourceType.COMPANY_REGISTRY, 3),
        )
        assert assess_sufficiency(docs) == DataSufficiency.RICH

    def test_one_below_adequate_volume(self):
        """7 docs with 3 types → LIMITED, not ADEQUATE."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 3),
            (SourceType.SEC_FILING, 2),
            (SourceType.COMPANY_WEBSITE, 2),
        )
        assert assess_sufficiency(docs) == DataSufficiency.LIMITED

    def test_one_below_rich_volume(self):
        """14 docs with 4 types → ADEQUATE, not RICH."""
        docs = _docs_of_types(
            (SourceType.NEWS_ARTICLE, 4),
            (SourceType.SEC_FILING, 4),
            (SourceType.COMPANY_WEBSITE, 3),
            (SourceType.COMPANY_REGISTRY, 3),
        )
        assert assess_sufficiency(docs) == DataSufficiency.ADEQUATE
