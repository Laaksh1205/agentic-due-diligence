"""
Data Sufficiency Check — Task 1.6

Classifies the document collection for an entity into one of four tiers:

    RICH     — 15+ docs AND 4+ distinct source types
    ADEQUATE — 8–14 docs AND 3+ distinct source types
    LIMITED  — 4–7 docs AND 2+ distinct source types
    SPARSE   — fewer than 4 docs OR only 1 distinct source type

A volume threshold AND a diversity threshold must both be met to reach a tier.
If only one is satisfied, the lower tier is returned.
"""

from typing import List

from src.models.documents import DataSufficiency, RawDocument


def assess_sufficiency(documents: List[RawDocument]) -> DataSufficiency:
    """Return a DataSufficiency rating for the given document collection.

    Both document count and source-type diversity must satisfy a tier's
    thresholds. If either falls short, the next lower tier is returned.
    """
    count = len(documents)
    diversity = len({doc.source_type for doc in documents})

    if count >= 15 and diversity >= 4:
        return DataSufficiency.RICH
    if count >= 8 and diversity >= 3:
        return DataSufficiency.ADEQUATE
    if count >= 4 and diversity >= 2:
        return DataSufficiency.LIMITED
    return DataSufficiency.SPARSE
