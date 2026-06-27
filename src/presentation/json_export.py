"""
Structured JSON export — Task 2.9.2.

Dumps a ``DueDiligenceReport`` (or, in Phase 2 before synthesis exists, a list of
scored signals) as formatted JSON to ``output/{entity}_{timestamp}.json``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.report import DueDiligenceReport
from src.models.signals import RiskSignal

DEFAULT_OUTPUT_DIR = "output"


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return s or "entity"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write(payload: dict, name: str, output_dir: str, timestamp: Optional[str]) -> Path:
    path = Path(output_dir) / f"{_slug(name)}_{timestamp or _timestamp()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def export_report(
    report: DueDiligenceReport,
    *,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    timestamp: Optional[str] = None,
) -> Path:
    """Write the full report as formatted JSON; returns the output path."""
    return _write(
        report.model_dump(mode="json"),
        report.target_entity.canonical_name,
        output_dir, timestamp,
    )


def export_signals(
    signals: list[RiskSignal],
    *,
    entity_name: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    timestamp: Optional[str] = None,
) -> Path:
    """Phase-2 fallback: dump scored signals before a full report exists."""
    payload = {
        "entity_name": entity_name,
        "generated_at": datetime.now().isoformat(),
        "signal_count": len(signals),
        "signals": [s.model_dump(mode="json") for s in signals],
    }
    return _write(payload, entity_name, output_dir, timestamp)


__all__ = ["export_report", "export_signals", "DEFAULT_OUTPUT_DIR"]
