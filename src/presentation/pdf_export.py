"""
PDF Report Export — Task 3.4.

Renders a DueDiligenceReport to a formatted PDF at output/{entity}_{ts}.pdf.
Severity colours are handled entirely through inline style attributes — no
external resources required.

Renderer selection (best-fidelity first, with a pure-Python fallback):
  1. weasyprint  — high-fidelity HTML→PDF, needs native GTK libs (Linux/Docker).
  2. xhtml2pdf   — pure-Python (reportlab-backed), works everywhere incl. stock
                   Windows where weasyprint's native libraries are unavailable.
The plan sanctions "weasyprint (or reportlab)"; the fallback keeps PDF export
working on any platform while preserving weasyprint's fidelity where present.

Severity colours (3.4.2):
  CRITICAL → red      HIGH → orange     MEDIUM → amber
  LOW → blue           INFO → grey

Data-sufficiency colours:
  RICH → green    ADEQUATE → amber    LIMITED → orange    SPARSE → red
"""

from __future__ import annotations

import html as _html_mod
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.documents import DataSufficiency
from src.models.report import Action, ActionPriority, DueDiligenceReport
from src.models.signals import RiskCategory, RiskSignal, Severity, SignalPolarity

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "output"

# ── Colour maps ───────────────────────────────────────────────────────────────

_SEV_BG: dict[Severity, str] = {
    Severity.CRITICAL: "#dc2626",
    Severity.HIGH:     "#ea580c",
    Severity.MEDIUM:   "#ca8a04",
    Severity.LOW:      "#2563eb",
    Severity.INFO:     "#6b7280",
}

_SUF_BG: dict[DataSufficiency, str] = {
    DataSufficiency.RICH:     "#16a34a",
    DataSufficiency.ADEQUATE: "#ca8a04",
    DataSufficiency.LIMITED:  "#ea580c",
    DataSufficiency.SPARSE:   "#dc2626",
}

_SEV_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
_CATEGORY_ORDER = list(RiskCategory)
_PRIORITY_ORDER = {ActionPriority.IMMEDIATE: 0, ActionPriority.SHORT_TERM: 1, ActionPriority.MONITOR: 2}
_PRIORITY_COLOR = {
    ActionPriority.IMMEDIATE:  "#dc2626",
    ActionPriority.SHORT_TERM: "#ea580c",
    ActionPriority.MONITOR:    "#2563eb",
}

# ── Utilities ─────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower() or "entity"

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def _e(s: object) -> str:
    return _html_mod.escape(str(s))

def _domain(url: str) -> str:
    s = url.split("://", 1)[-1]
    return s.split("/", 1)[0].removeprefix("www.")

def _badge(text: str, bg: str) -> str:
    return (
        f'<span style="display:inline-block;padding:1pt 6pt;border-radius:3pt;'
        f'background:{bg};color:#fff;font-size:8pt;font-weight:bold;'
        f'letter-spacing:0.3pt">{_e(text)}</span>'
    )

def _sev_badge(sev: Severity) -> str:
    return _badge(sev.value, _SEV_BG.get(sev, "#6b7280"))

def _suf_badge(suf: DataSufficiency) -> str:
    return _badge(suf.value, _SUF_BG.get(suf, "#6b7280"))


# ── CSS ───────────────────────────────────────────────────────────────────────

_CSS = """\
* { box-sizing: border-box; }
body {
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 10pt;
    color: #1f2937;
    margin: 0; padding: 0;
}
.page { padding: 24pt 32pt; }
h1 { font-size: 17pt; margin: 0 0 4pt; color: #111827; }
h2 {
    font-size: 12pt; margin: 18pt 0 6pt; color: #1e3a5f;
    border-bottom: 1.5pt solid #1e3a5f; padding-bottom: 2pt;
}
h3 { font-size: 10.5pt; margin: 12pt 0 4pt; color: #374151; }
p  { margin: 4pt 0; line-height: 1.5; }
.meta { color: #6b7280; font-size: 8.5pt; margin-top: 3pt; }
.caveat {
    background: #fef3c7; border: 1pt solid #f59e0b;
    padding: 6pt 10pt; border-radius: 4pt; margin: 8pt 0;
    font-size: 9pt; color: #92400e;
}
table  { width: 100%; border-collapse: collapse; margin: 4pt 0 10pt; }
th     { background: #f3f4f6; text-align: left; padding: 4pt 6pt;
         font-size: 8.5pt; border-bottom: 1pt solid #d1d5db; }
td     { padding: 4pt 6pt; border-bottom: 0.5pt solid #e5e7eb;
         font-size: 8.5pt; vertical-align: top; }
tr:last-child td { border-bottom: none; }
.sev-col  { width: 64pt; }
.conf-col { width: 30pt; text-align: right; }
.wt-col   { width: 28pt; text-align: right; }
.src-col  { width: 90pt; }
.footer {
    margin-top: 20pt; border-top: 0.5pt solid #d1d5db;
    padding-top: 5pt; color: #9ca3af; font-size: 7.5pt; text-align: right;
}
"""


# ── Section builders ──────────────────────────────────────────────────────────

def _signals_table(signals: list[RiskSignal]) -> str:
    if not signals:
        return "<p><em>None.</em></p>"
    ordered = sorted(
        signals,
        key=lambda s: (_SEV_ORDER.index(s.severity) if s.severity in _SEV_ORDER else 99, -s.confidence_score),
    )
    rows = []
    for s in ordered:
        flags = ""
        if s.is_corroborated:
            flags += ' <em style="color:#16a34a;font-size:7.5pt">✓ corroborated</em>'
        if s.is_contradictory:
            flags += ' <em style="color:#7c3aed;font-size:7.5pt">⚠ contradicted</em>'
        src_href = f'<a href="{_e(s.source_url)}" style="color:#2563eb">{_e(_domain(s.source_url))}</a>'
        rows.append(
            f"<tr>"
            f"<td class='sev-col'>{_sev_badge(s.severity)}</td>"
            f"<td>{_e(s.text)}{flags}</td>"
            f"<td class='src-col'>{src_href}</td>"
            f"<td class='conf-col'>{s.confidence_score:.2f}</td>"
            f"<td class='wt-col'>{s.temporal_weight:.2f}</td>"
            f"</tr>"
        )
    header = (
        "<tr><th class='sev-col'>Severity</th><th>Signal</th>"
        "<th class='src-col'>Source</th>"
        "<th class='conf-col'>Conf</th><th class='wt-col'>Wt</th></tr>"
    )
    return f"<table>{header}{''.join(rows)}</table>"


def _risk_sections(signals: list[RiskSignal]) -> str:
    by_cat: dict[RiskCategory, list[RiskSignal]] = {}
    for s in signals:
        by_cat.setdefault(s.risk_category, []).append(s)
    parts = []
    for cat in _CATEGORY_ORDER:
        group = by_cat.get(cat)
        if group:
            parts.append(f"<h3>{_e(cat.value)} ({len(group)})</h3>")
            parts.append(_signals_table(group))
    return "".join(parts) if parts else "<p><em>No risk signals detected.</em></p>"


def _actions_section(actions: list[Action]) -> str:
    if not actions:
        return "<p><em>None.</em></p>"
    ordered = sorted(actions, key=lambda a: _PRIORITY_ORDER.get(a.priority, 9))
    rows = []
    for a in ordered:
        color = _PRIORITY_COLOR.get(a.priority, "#374151")
        label = a.priority.value.replace("_", " ")
        rows.append(
            f"<tr>"
            f"<td style='width:80pt;color:{color};font-weight:bold'>{_e(label)}</td>"
            f"<td>{_e(a.description)}</td>"
            f"</tr>"
        )
    return f"<table><tr><th style='width:80pt'>Priority</th><th>Action</th></tr>{''.join(rows)}</table>"


def _sources_section(consulted: list[str], failed: list[str]) -> str:
    c = "".join(
        f'<tr><td style="color:#16a34a">✓ {_e(s)}</td></tr>' for s in consulted
    ) or "<tr><td style='color:#9ca3af'>—</td></tr>"
    f = "".join(
        f'<tr><td style="color:#dc2626">✗ {_e(s)}</td></tr>' for s in failed
    ) or "<tr><td style='color:#9ca3af'>—</td></tr>"
    return (
        "<table><tr><th style='width:50%'>Consulted</th><th>Failed</th></tr>"
        f"<tr><td><table>{c}</table></td><td><table>{f}</table></td></tr></table>"
    )


def _meta_section(report: DueDiligenceReport, elapsed_seconds: float) -> str:
    m = report.metadata
    rows = [
        ("Run ID", str(m.run_id)),
        ("Generated at", datetime.now().strftime("%Y-%m-%d %H:%M UTC")),
        ("Evaluation scope", report.evaluation_scope),
        ("Estimated cost", f"${m.estimated_cost_usd:.4f}"),
        ("LLM calls", str(m.llm_call_count)),
        ("Signals extracted", str(m.signals_extracted)),
        ("Signals rejected", str(m.signals_rejected)),
        ("Run duration", f"{elapsed_seconds:.1f}s"),
    ]
    cells = "".join(
        f"<tr>"
        f"<td style='width:130pt;color:#6b7280;padding:3pt 8pt 3pt 0'>{_e(k)}</td>"
        f"<td style='padding:3pt 0'>{_e(v)}</td>"
        f"</tr>"
        for k, v in rows
    )
    return f"<table style='width:auto'>{cells}</table>"


# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(report: DueDiligenceReport, *, elapsed_seconds: float = 0.0) -> str:
    """Convert a DueDiligenceReport to a self-contained HTML string."""
    entity = report.target_entity
    now_str = datetime.now().strftime("%B %d, %Y")

    risks = [s for s in report.risk_signals if s.signal_polarity != SignalPolarity.POSITIVE]
    positives = list(report.positive_signals)

    caveat = ""
    if report.data_sufficiency in (DataSufficiency.LIMITED, DataSufficiency.SPARSE):
        caveat = (
            '<div class="caveat">⚠ This assessment is based on limited publicly available data. '
            "Findings may not be comprehensive. Manual investigation is recommended for a complete evaluation.</div>"
        )

    score_part = ""
    if report.overall_risk_score:
        score_part = f" &nbsp;·&nbsp; Overall risk: <strong>{report.overall_risk_score:.1f}/10</strong>"

    # Category scores table (only when populated by synthesis agent)
    cat_scores_html = ""
    if report.category_scores:
        rows = "".join(
            f"<tr><td>{_e(cat.value)}</td><td style='width:60pt'>{score:.1f}/10</td></tr>"
            for cat, score in sorted(report.category_scores.items(), key=lambda x: -x[1])
        )
        cat_scores_html = (
            "<h2>Category Risk Scores</h2>"
            f"<table><tr><th>Category</th><th style='width:60pt'>Score</th></tr>{rows}</table>"
        )

    # Per-category narrative sections (only when populated by synthesis agent)
    detail_html = ""
    if report.detailed_sections:
        parts = []
        for cat in _CATEGORY_ORDER:
            text = report.detailed_sections.get(cat, "")
            if text:
                parts.append(f"<h3>{_e(cat.value)}</h3><p>{_e(text)}</p>")
        if parts:
            detail_html = "<h2>Detailed Analysis</h2>" + "".join(parts)

    # Source display names
    def _src_name(s) -> str:
        if hasattr(s, "name"):
            return s.name or _domain(getattr(s, "url", ""))
        return str(s)

    consulted = [_src_name(s) for s in report.sources_consulted]
    failed = [_src_name(s) for s in report.sources_failed]

    actions_html = (
        "<h2>Recommended Actions</h2>" + _actions_section(report.recommended_actions)
        if report.recommended_actions
        else ""
    )

    body = f"""
<div class="page">
  <div style="margin-bottom:14pt">
    <h1>Due Diligence Report — {_e(entity.canonical_name)}</h1>
    <div class="meta">
      Generated {now_str}{score_part} &nbsp;·&nbsp;
      Data quality: {_suf_badge(report.data_sufficiency)} &nbsp;·&nbsp;
      Scope: {_e(report.evaluation_scope)}
    </div>
  </div>

  {caveat}

  <h2>Executive Summary</h2>
  <p>{_e(report.executive_summary) if report.executive_summary else "<em>Not available.</em>"}</p>

  {cat_scores_html}

  <h2>Risk Signals ({len(risks)})</h2>
  {_risk_sections(risks)}

  {detail_html}

  <h2>Strengths &amp; Positive Indicators ({len(positives)})</h2>
  {_signals_table(positives) if positives else "<p><em>None identified.</em></p>"}

  {actions_html}

  <h2>Sources</h2>
  {_sources_section(consulted, failed)}

  <h2>Run Metadata</h2>
  {_meta_section(report, elapsed_seconds)}

  <div class="footer">
    Due Diligence Intelligence Platform &nbsp;·&nbsp;
    Run ID {_e(str(report.metadata.run_id))}
  </div>
</div>"""

    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{body}</body></html>"


# ── Public export function ────────────────────────────────────────────────────

def _render_with_weasyprint(html_str: str, out_path: Path) -> None:
    """High-fidelity render via weasyprint. Raises if the native libs are absent."""
    from weasyprint import HTML  # type: ignore[import-untyped]

    HTML(string=html_str).write_pdf(str(out_path))


def _render_with_xhtml2pdf(html_str: str, out_path: Path) -> None:
    """Pure-Python render via xhtml2pdf (reportlab). Works without native libs."""
    from xhtml2pdf import pisa  # type: ignore[import-untyped]

    with open(out_path, "wb") as fh:
        result = pisa.CreatePDF(src=html_str, dest=fh, encoding="utf-8")
    # Success is defined by a non-empty file on disk; xhtml2pdf may report a
    # non-zero ``err`` for unsupported CSS while still producing a usable PDF.
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"xhtml2pdf produced no output (err={getattr(result, 'err', '?')})")


def export_pdf(
    report: DueDiligenceReport,
    *,
    elapsed_seconds: float = 0.0,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    timestamp: Optional[str] = None,
) -> Path:
    """Render *report* to a PDF file and return its path.

    Tries weasyprint first (best fidelity), then falls back to the pure-Python
    xhtml2pdf renderer. Raises RuntimeError only if *no* renderer succeeds —
    install a PDF backend with ``pip install 'due-diligence-platform[pdf]'``.
    """
    html_str = build_html(report, elapsed_seconds=elapsed_seconds)

    slug = _slug(report.target_entity.canonical_name)
    ts = timestamp or _ts()
    out_path = Path(output_dir) / f"{slug}_{ts}.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    for name, renderer in (
        ("weasyprint", _render_with_weasyprint),
        ("xhtml2pdf", _render_with_xhtml2pdf),
    ):
        try:
            renderer(html_str, out_path)
            logger.info("[pdf_export] PDF written to %s via %s", out_path, name)
            return out_path
        except Exception as exc:  # ImportError (missing) or render failure
            logger.debug("[pdf_export] %s renderer unavailable/failed: %s", name, exc)
            errors.append(f"{name}: {exc}")

    raise RuntimeError(
        "PDF export failed — no working renderer available. Install a PDF backend "
        "with: pip install \"due-diligence-platform[pdf]\". Details: " + " | ".join(errors)
    )


__all__ = ["build_html", "export_pdf", "DEFAULT_OUTPUT_DIR"]
