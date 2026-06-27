"""
Tests for PDF report export — Task 3.4.

All tests run without a live weasyprint install.  The HTML builder is tested
directly (no I/O, no external deps); export_pdf() is tested only through
mocking weasyprint to verify the write path without actually rendering.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.models.documents import DataSufficiency
from src.models.entities import EntityType, ResolvedEntity
from src.models.report import Action, ActionPriority, DueDiligenceReport, ReportMetadata
from src.models.signals import (
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
    SourceType,
)
from src.presentation.pdf_export import DEFAULT_OUTPUT_DIR, build_html, export_pdf


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _entity(name: str = "Acme Corp") -> ResolvedEntity:
    return ResolvedEntity(
        canonical_name=name,
        entity_type=EntityType.COMPANY,
        aliases=[name],
        jurisdiction="us-de",
        is_public=False,
    )


def _signal(
    text: str = "Regulatory fine imposed.",
    severity: Severity = Severity.HIGH,
    polarity: SignalPolarity = SignalPolarity.NEGATIVE,
    category: RiskCategory = RiskCategory.LEGAL,
) -> RiskSignal:
    return RiskSignal(
        text=text,
        source_url="https://example.com/news",
        source_type=SourceType.NEWS_ARTICLE,
        source_snippet="Regulatory fine imposed on the company.",
        confidence_score=0.85,
        temporal_weight=0.9,
        risk_category=category,
        severity=severity,
        signal_polarity=polarity,
        entity_name="Acme Corp",
    )


def _report(
    data_sufficiency: DataSufficiency = DataSufficiency.ADEQUATE,
    risk_signals: list[RiskSignal] | None = None,
    positive_signals: list[RiskSignal] | None = None,
    executive_summary: str = "Acme Corp presents moderate risk.",
) -> DueDiligenceReport:
    return DueDiligenceReport(
        target_entity=_entity(),
        evaluation_scope="full",
        data_sufficiency=data_sufficiency,
        risk_signals=risk_signals or [],
        positive_signals=positive_signals or [],
        executive_summary=executive_summary,
        metadata=ReportMetadata(
            run_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
            estimated_cost_usd=0.0123,
            llm_call_count=7,
            signals_extracted=5,
            signals_rejected=1,
        ),
    )


# ── 3.4.1 HTML builder — structure ────────────────────────────────────────────

class TestBuildHtml:
    def test_returns_html_string(self) -> None:
        html = build_html(_report())
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_entity_name_in_output(self) -> None:
        html = build_html(_report())
        assert "Acme Corp" in html

    def test_executive_summary_in_output(self) -> None:
        html = build_html(_report(executive_summary="Acme Corp presents moderate risk."))
        assert "Acme Corp presents moderate risk." in html

    def test_run_id_in_output(self) -> None:
        html = build_html(_report())
        assert "12345678-1234-5678-1234-567812345678" in html

    def test_scope_in_output(self) -> None:
        html = build_html(_report())
        assert "full" in html

    def test_risk_signal_text_in_output(self) -> None:
        sig = _signal("Significant lawsuit pending.")
        html = build_html(_report(risk_signals=[sig]))
        assert "Significant lawsuit pending." in html

    def test_positive_signal_in_output(self) -> None:
        pos = _signal("Strong revenue growth.", polarity=SignalPolarity.POSITIVE)
        html = build_html(_report(positive_signals=[pos]))
        assert "Strong revenue growth." in html

    def test_data_sufficiency_value_in_output(self) -> None:
        html = build_html(_report(data_sufficiency=DataSufficiency.RICH))
        assert "RICH" in html

    def test_no_empty_signals_section_when_absent(self) -> None:
        html = build_html(_report())
        assert "No risk signals detected." in html

    def test_metadata_cost_in_output(self) -> None:
        html = build_html(_report())
        assert "0.0123" in html


# ── 3.4.2 Severity colour coding ──────────────────────────────────────────────

class TestSeverityColours:
    def _html_for_severity(self, sev: Severity) -> str:
        sig = _signal("Test signal.", severity=sev)
        return build_html(_report(risk_signals=[sig]))

    def test_critical_uses_red(self) -> None:
        html = self._html_for_severity(Severity.CRITICAL)
        assert "#dc2626" in html

    def test_high_uses_orange(self) -> None:
        html = self._html_for_severity(Severity.HIGH)
        assert "#ea580c" in html

    def test_medium_uses_amber(self) -> None:
        html = self._html_for_severity(Severity.MEDIUM)
        assert "#ca8a04" in html

    def test_low_uses_blue(self) -> None:
        html = self._html_for_severity(Severity.LOW)
        assert "#2563eb" in html

    def test_info_uses_grey(self) -> None:
        html = self._html_for_severity(Severity.INFO)
        assert "#6b7280" in html

    def test_severity_value_rendered(self) -> None:
        for sev in Severity:
            html = self._html_for_severity(sev)
            assert sev.value in html


# ── Data-sufficiency caveat ───────────────────────────────────────────────────

class TestSufficiencyCaveat:
    def test_caveat_shown_for_limited(self) -> None:
        html = build_html(_report(data_sufficiency=DataSufficiency.LIMITED))
        assert "limited publicly available data" in html

    def test_caveat_shown_for_sparse(self) -> None:
        html = build_html(_report(data_sufficiency=DataSufficiency.SPARSE))
        assert "limited publicly available data" in html

    def test_no_caveat_for_rich(self) -> None:
        html = build_html(_report(data_sufficiency=DataSufficiency.RICH))
        assert "limited publicly available data" not in html

    def test_no_caveat_for_adequate(self) -> None:
        html = build_html(_report(data_sufficiency=DataSufficiency.ADEQUATE))
        assert "limited publicly available data" not in html


# ── Recommended actions ───────────────────────────────────────────────────────

class TestActions:
    def test_action_in_output(self) -> None:
        action = Action(
            description="Escalate to legal team immediately.",
            priority=ActionPriority.IMMEDIATE,
        )
        report = _report()
        report.recommended_actions = [action]
        html = build_html(report)
        assert "Escalate to legal team immediately." in html
        assert "IMMEDIATE" in html

    def test_no_actions_section_skipped(self) -> None:
        html = build_html(_report())
        assert "Recommended Actions" not in html


# ── export_pdf() ─────────────────────────────────────────────────────────────

class TestExportPdf:
    def test_falls_back_to_xhtml2pdf_when_weasyprint_missing(self, tmp_path: Path) -> None:
        # weasyprint unavailable → export_pdf falls back to the pure-Python
        # xhtml2pdf renderer and still produces a non-empty PDF on disk.
        import builtins
        real_import = builtins.__import__

        def broken_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "weasyprint":
                raise ImportError("no module named weasyprint")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=broken_import):
            out = export_pdf(_report(risk_signals=[_signal()]), output_dir=str(tmp_path))

        assert out.exists()
        assert out.stat().st_size > 0

    def test_raises_when_no_renderer_available(self, tmp_path: Path) -> None:
        # Both renderers unavailable → RuntimeError naming the missing backends.
        import builtins
        real_import = builtins.__import__

        def broken_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("weasyprint") or name.startswith("xhtml2pdf"):
                raise ImportError(f"no module named {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=broken_import):
            with pytest.raises(RuntimeError, match="no working renderer"):
                export_pdf(_report(), output_dir=str(tmp_path))

    def test_writes_pdf_to_output_dir(self, tmp_path: Path) -> None:
        import builtins
        real_import = builtins.__import__
        mock_wp = MagicMock()
        mock_html_cls = MagicMock()
        mock_html_inst = MagicMock()
        mock_html_cls.return_value = mock_html_inst
        mock_wp.HTML = mock_html_cls

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "weasyprint":
                return mock_wp
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            out = export_pdf(_report(), output_dir=str(tmp_path), timestamp="20260626_120000")

        assert out == tmp_path / "acme_corp_20260626_120000.pdf"
        mock_html_inst.write_pdf.assert_called_once_with(str(out))

    def test_slug_used_in_filename(self, tmp_path: Path) -> None:
        report = _report()
        report.target_entity = _entity("Tesla, Inc.")

        import builtins
        real_import = builtins.__import__
        mock_wp = MagicMock()
        mock_wp.HTML.return_value = MagicMock()

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "weasyprint":
                return mock_wp
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            out = export_pdf(report, output_dir=str(tmp_path), timestamp="ts")

        assert "tesla_inc" in out.name

    def test_elapsed_seconds_passed_to_html(self) -> None:
        html = build_html(_report(), elapsed_seconds=42.5)
        assert "42.5s" in html
