"""
Rich terminal report — Task 2.9.1.

Renders a Phase-2 due-diligence summary to the terminal with ``rich``:
entity header, colour-coded data-sufficiency badge, risk signals grouped by
category (severity-coloured), a strengths section (POSITIVE signals), sources
consulted vs failed, and run metadata. Pure presentation — no LLM/network.
"""

from __future__ import annotations

import time
from typing import Iterable, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.models.documents import DataSufficiency
from src.models.entities import ResolvedEntity
from src.models.report import Action, ActionPriority, DueDiligenceReport
from src.models.signals import RiskCategory, RiskSignal, Severity, SignalPolarity

# Severity → display style + ordering (higher first).
_SEVERITY_STYLE = {
    Severity.CRITICAL: "bold white on red",
    Severity.HIGH: "bold red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}
_SEVERITY_RANK = {
    Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1, Severity.INFO: 0,
}
_SUFFICIENCY_STYLE = {
    DataSufficiency.RICH: "bold green",
    DataSufficiency.ADEQUATE: "yellow",
    DataSufficiency.LIMITED: "dark_orange",
    DataSufficiency.SPARSE: "bold red",
}
_PRIORITY_STYLE = {
    ActionPriority.IMMEDIATE: "bold red",
    ActionPriority.SHORT_TERM: "dark_orange",
    ActionPriority.MONITOR: "cyan",
}
# Stable category display order.
_CATEGORY_ORDER = list(RiskCategory)


def _domain(url: str) -> str:
    s = url.split("://", 1)[-1]
    return s.split("/", 1)[0].removeprefix("www.")


def sufficiency_badge(suf: Optional[DataSufficiency]) -> Text:
    if suf is None:
        return Text("UNKNOWN", style="dim")
    return Text(f" {suf.value} ", style=_SUFFICIENCY_STYLE.get(suf, "white"))


def _entity_panel(entity: ResolvedEntity, suf: Optional[DataSufficiency]) -> Panel:
    aliases = ", ".join(entity.aliases[:5]) or "—"
    body = Text()
    body.append("Jurisdiction  ", style="dim"); body.append(f"{entity.jurisdiction or 'unknown'}\n")
    body.append("Public        ", style="dim"); body.append(f"{'yes' if entity.is_public else 'no'}\n")
    body.append("Aliases       ", style="dim"); body.append(f"{aliases}\n")
    body.append("Data quality  ", style="dim"); body.append_text(sufficiency_badge(suf))
    return Panel(body, title=f"[bold]{entity.canonical_name}[/]", border_style="blue", box=box.ROUNDED)


def _severity_cell(sev: Severity) -> Text:
    return Text(f" {sev.value} ", style=_SEVERITY_STYLE.get(sev, "white"))


def _review_cell(sig: RiskSignal) -> str:
    if sig.human_verdict is not None:
        return sig.human_verdict.value
    if sig.requires_human_review:
        return "PENDING_REVIEW"
    return ""


def _signals_table(signals: list[RiskSignal]) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, expand=True, show_edge=False, pad_edge=False)
    table.add_column("Sev", no_wrap=True)
    table.add_column("Signal", ratio=3)
    table.add_column("Source", no_wrap=True)
    table.add_column("Conf", justify="right", no_wrap=True)
    table.add_column("Wt", justify="right", no_wrap=True)
    table.add_column("Review", no_wrap=True)
    ordered = sorted(signals, key=lambda s: (_SEVERITY_RANK.get(s.severity, 0), s.confidence_score), reverse=True)
    for s in ordered:
        cell = Text(s.text)
        if s.is_corroborated:
            cell.append("  ✓corrob", style="green")
        if s.is_contradictory:
            cell.append("  ⚠contradict", style="magenta")
        table.add_row(
            _severity_cell(s.severity),
            cell,
            _domain(s.source_url),
            f"{s.confidence_score:.2f}",
            f"{s.temporal_weight:.2f}",
            _review_cell(s),
        )
    return table


def _sources_table(consulted: Iterable[str], failed: Iterable[str]) -> Table:
    t = Table(box=box.MINIMAL, show_header=True, expand=True)
    t.add_column("Sources consulted", style="green")
    t.add_column("Sources failed", style="red")
    consulted, failed = list(consulted), list(failed)
    for i in range(max(len(consulted), len(failed), 1)):
        t.add_row(consulted[i] if i < len(consulted) else "",
                  failed[i] if i < len(failed) else "")
    return t


def _metadata_panel(meta: dict) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim"); t.add_column(justify="right")
    t.add_row("Run time", f"{meta.get('elapsed_seconds', 0.0):.1f}s")
    t.add_row("LLM calls", str(meta.get("llm_call_count", 0)))
    t.add_row("Cost", f"${meta.get('total_cost', 0.0):.4f}")
    t.add_row("Signals extracted", str(meta.get("signals_extracted", 0)))
    if "signals_rejected" in meta:
        t.add_row("Signals rejected", str(meta["signals_rejected"]))
    t.add_row("Signals (final)", str(meta.get("signals_final", 0)))
    return Panel(t, title="Run metadata", border_style="dim", box=box.ROUNDED)


def _category_scores_table(scores: dict[RiskCategory, float], overall: float) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, expand=False, show_edge=False)
    t.add_column("Category", style="bold")
    t.add_column("Score (0–10)", justify="right", no_wrap=True)
    for cat in _CATEGORY_ORDER:
        score = scores.get(cat)
        if score is None:
            continue
        colour = "bold red" if score >= 7 else "yellow" if score >= 4 else "green"
        t.add_row(cat.value, Text(f"{score:.1f}", style=colour))
    if overall:
        t.add_row("[dim]Overall[/]", Text(f"{overall:.1f}", style="bold"))
    return t


def _actions_table(actions: list[Action]) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, expand=True, show_edge=False)
    t.add_column("Priority", no_wrap=True)
    t.add_column("Action")
    for a in sorted(actions, key=lambda x: list(ActionPriority).index(x.priority)):
        style = _PRIORITY_STYLE.get(a.priority, "white")
        t.add_row(Text(a.priority.value, style=style), a.description)
    return t


def render_report(
    *,
    console: Console,
    entity: Optional[ResolvedEntity],
    data_sufficiency: Optional[DataSufficiency],
    signals: list[RiskSignal],
    sources_consulted: Iterable[str] = (),
    sources_failed: Iterable[str] = (),
    metadata: Optional[dict] = None,
    report: Optional[DueDiligenceReport] = None,
) -> None:
    """Render the full Phase-2/3 report to *console*."""
    metadata = metadata or {}
    if entity is None:
        console.print(Panel(
            "[bold red]ERROR[/]: entity could not be resolved. Re-run with --verbose for details.",
            border_style="red",
        ))
        return

    console.rule(f"[bold]Due Diligence Report — {entity.canonical_name}[/]")
    console.print(_entity_panel(entity, data_sufficiency))

    # ── Executive summary (3.5.3) — from synthesis report when available ──────
    exec_summary = report.executive_summary if report else ""
    if exec_summary:
        console.print(Panel(exec_summary, title="[bold]Executive Summary[/]", border_style="blue", box=box.ROUNDED))

    risks = [s for s in signals if s.signal_polarity is not SignalPolarity.POSITIVE]
    strengths = [s for s in signals if s.signal_polarity is SignalPolarity.POSITIVE]

    if risks:
        console.print(f"\n[bold]Risk Signals[/] ({len(risks)})")
        by_cat: dict[RiskCategory, list[RiskSignal]] = {}
        for s in risks:
            by_cat.setdefault(s.risk_category, []).append(s)
        for cat in _CATEGORY_ORDER:
            group = by_cat.get(cat)
            if group:
                console.print(f"[bold underline]{cat.value}[/] ({len(group)})")
                console.print(_signals_table(group))
    else:
        console.print("\n[dim]No risk signals detected.[/]")

    if strengths:
        console.print(f"\n[bold green]Strengths & Positive Indicators[/] ({len(strengths)})")
        console.print(_signals_table(strengths))

    # ── Synthesis sections — only rendered when synthesis ran (3.5.3) ─────────
    if report and report.category_scores:
        console.print("\n[bold]Category Risk Scores[/]")
        console.print(_category_scores_table(report.category_scores, report.overall_risk_score))

    if report and report.detailed_sections:
        console.print("\n[bold]Detailed Analysis[/]")
        for cat in _CATEGORY_ORDER:
            text = report.detailed_sections.get(cat, "")
            if text:
                console.print(f"[bold underline]{cat.value}[/]")
                console.print(text)

    if report and report.recommended_actions:
        console.print(f"\n[bold]Recommended Actions[/] ({len(report.recommended_actions)})")
        console.print(_actions_table(report.recommended_actions))

    console.print("\n[bold]Sources[/]")
    console.print(_sources_table(sources_consulted, sources_failed))
    console.print(_metadata_panel(metadata))


def render_from_state(state: dict, console: Optional[Console] = None) -> None:
    """Render directly from an AgentState-like dict (used by the supervisor)."""
    console = console or Console()
    report: Optional[DueDiligenceReport] = state.get("report")

    # Prefer the full synthesis report signals when available; fall back to raw.
    if report and (report.risk_signals or report.positive_signals):
        signals = list(report.risk_signals) + list(report.positive_signals)
    else:
        signals = list(state.get("scored_signals") or state.get("raw_signals") or [])

    start = state.get("start_time")
    meta: dict = {
        "elapsed_seconds": (time.monotonic() - start) if start else 0.0,
        "llm_call_count": state.get("llm_call_count", 0),
        "total_cost": state.get("total_cost", 0.0),
        "signals_extracted": len(state.get("raw_signals") or []),
        "signals_final": len(signals),
    }
    if report:
        meta["signals_rejected"] = report.metadata.signals_rejected
    render_report(
        console=console,
        entity=state.get("resolved_entity"),
        data_sufficiency=state.get("data_sufficiency"),
        signals=signals,
        sources_consulted=state.get("sources_consulted") or [],
        sources_failed=state.get("sources_failed") or [],
        metadata=meta,
        report=report,
    )


__all__ = ["render_report", "render_from_state", "sufficiency_badge"]
