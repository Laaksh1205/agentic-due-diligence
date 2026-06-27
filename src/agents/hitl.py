"""
Human-in-the-Loop (HITL) gate — Task 2.8.

Surfaces signals flagged ``requires_human_review`` (CRITICAL severity or low
confidence, set by the Risk Analysis Agent) for a human verdict before synthesis:

  - ``--auto``  : skip review; leave each flagged signal at ``human_verdict=None``
    (PENDING_REVIEW). CRITICAL findings are never auto-confirmed.
  - interactive : show each flagged signal with ``rich`` and prompt
    ``[C]onfirm / [D]ismiss / [I]nvestigate``; apply verdicts — dismissed signals
    are dropped (excluded from synthesis), confirmed/investigate are kept.
  - timeout     : if the reviewer does not respond within ``hitl_timeout`` seconds
    (default 24h prod, 60s dev), auto-proceed leaving all pending as
    PENDING_REVIEW (never CONFIRMED).

> Note: the installed langgraph (0.2.22) predates the dynamic ``interrupt()`` /
> ``Command(resume=...)`` API the design references (added in 0.3+). The gate is
> therefore implemented as an in-node review step with a timeout. The verdict
> source is injectable (``verdict_provider``) so an upgrade can drop in
> ``interrupt()`` without touching this module's logic or tests.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from src.models.signals import HumanVerdict, RiskSignal

logger = logging.getLogger(__name__)

DEV_HITL_TIMEOUT = 60  # seconds — recommended for --hitl-timeout in dev/demos

# Accepted single-key and word inputs → verdict.
_VERDICT_BY_INPUT = {
    "c": HumanVerdict.CONFIRMED, "confirm": HumanVerdict.CONFIRMED,
    "d": HumanVerdict.DISMISSED, "dismiss": HumanVerdict.DISMISSED,
    "i": HumanVerdict.NEEDS_INVESTIGATION, "investigate": HumanVerdict.NEEDS_INVESTIGATION,
}

# A verdict provider takes the review payload and returns {signal_id: verdict_str}.
VerdictProvider = Callable[[list[dict]], Awaitable[dict[str, str]]]

# ── Web verdict provider registry (Task 4.1.6) ───────────────────────────────
# The FastAPI backend drives HITL from the browser instead of the terminal. It
# registers a per-run provider here before launching the pipeline; the gate looks
# it up by run_id so concurrent web runs each get their own provider. Keyed by
# run_id (rather than a single global) so simultaneous reviews don't clobber.
_web_verdict_providers: dict[str, VerdictProvider] = {}


def register_web_verdict_provider(run_id: str, provider: VerdictProvider) -> None:
    """Register a browser-driven verdict provider for *run_id* (Task 4.1.6)."""
    _web_verdict_providers[run_id] = provider


def unregister_web_verdict_provider(run_id: str) -> None:
    """Remove the provider for *run_id* (no-op if absent)."""
    _web_verdict_providers.pop(run_id, None)


def parse_verdict(value: str) -> Optional[HumanVerdict]:
    return _VERDICT_BY_INPUT.get((value or "").strip().lower())


def signals_for_review(signals: list[RiskSignal]) -> list[RiskSignal]:
    """Signals the Risk Analysis Agent flagged for human review (Task 2.8.1)."""
    return [s for s in signals if s.requires_human_review]


def build_review_payload(signals: list[RiskSignal]) -> list[dict]:
    """Serializable summary of each flagged signal for the review UI / interrupt."""
    return [
        {
            "id": str(s.id),
            "text": s.text,
            "category": s.risk_category.value,
            "severity": s.severity.value,
            "source_url": s.source_url,
            "source_snippet": s.source_snippet,
        }
        for s in signals
    ]


def apply_verdicts(signals: list[RiskSignal], verdicts: dict[str, str]) -> list[RiskSignal]:
    """Apply human verdicts (Task 2.8.3).

    - DISMISSED → signal dropped (excluded from synthesis).
    - CONFIRMED / NEEDS_INVESTIGATION → kept, ``human_verdict`` set, review flag cleared.
    - no verdict → kept unchanged (PENDING_REVIEW: human_verdict stays None).
    Inputs are not mutated.
    """
    out: list[RiskSignal] = []
    for s in signals:
        verdict = parse_verdict(verdicts.get(str(s.id), "")) if verdicts else None
        if verdict is None:
            out.append(s)
            continue
        if verdict is HumanVerdict.DISMISSED:
            continue  # exclude from synthesis
        out.append(s.model_copy(update={"human_verdict": verdict, "requires_human_review": False}))
    return out


async def cli_collect_verdicts(
    payload: list[dict],
    *,
    timeout: float,
    input_fn: Optional[Callable[[str], str]] = None,
    console=None,
) -> dict[str, str]:
    """Interactive CLI review with a hard timeout (Tasks 2.8.2, 2.8.4).

    Returns {signal_id: verdict_str}. On timeout returns ``{}`` (all left
    PENDING_REVIEW — never auto-confirmed). ``input_fn`` is injectable for tests.
    """
    import threading

    from rich.console import Console
    from rich.panel import Panel

    console = console or Console()
    input_fn = input_fn or input

    async def _read(prompt: str) -> str:
        """Read one line on a DAEMON thread so a timeout never blocks process exit."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def worker() -> None:
            try:
                value = input_fn(prompt)
                loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(value))
            except BaseException as exc:  # EOFError, KeyboardInterrupt, OSError (closed stdin)
                loop.call_soon_threadsafe(lambda: fut.done() or fut.set_exception(exc))

        threading.Thread(target=worker, daemon=True).start()
        return await fut

    async def _interact() -> dict[str, str]:
        verdicts: dict[str, str] = {}
        for i, item in enumerate(payload, 1):
            console.print(Panel(
                f"[bold]{item['severity']}[/] · {item['category']}\n\n{item['text']}\n\n"
                f"[dim]“{item['source_snippet']}”\n{item['source_url']}[/]",
                title=f"Signal {i}/{len(payload)} — requires review",
                border_style="red",
            ))
            while True:
                try:
                    ans = (await _read(
                        "[C]onfirm / [D]ismiss / [I]nvestigate further? "
                    )).strip().lower()
                except (EOFError, KeyboardInterrupt, OSError):
                    # Non-interactive / aborted stdin → leave the rest PENDING_REVIEW.
                    logger.warning("[hitl] input unavailable — leaving remaining signals PENDING_REVIEW")
                    return verdicts
                if ans in _VERDICT_BY_INPUT:
                    verdicts[item["id"]] = ans
                    break
                console.print("Please enter c, d, or i.")
        return verdicts

    try:
        return await asyncio.wait_for(_interact(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "[hitl] review timed out after %ss — %d signal(s) left PENDING_REVIEW",
            timeout, len(payload),
        )
        return {}


async def run_human_review(
    scored_signals: list[RiskSignal],
    *,
    auto_mode: bool,
    timeout: float,
    verdict_provider: Optional[VerdictProvider] = None,
    run_id: Optional[str] = None,
) -> list[RiskSignal]:
    """The HITL gate (Task 2.8.1). Returns the (possibly filtered) signal list.

    Verdict source precedence: an explicit ``verdict_provider`` arg, then a
    browser-driven provider registered for ``run_id`` (Task 4.1.6), then the
    interactive CLI prompt.
    """
    review = signals_for_review(scored_signals)
    if not review:
        return scored_signals

    if auto_mode:
        logger.info("[hitl] auto mode — %d signal(s) tagged PENDING_REVIEW", len(review))
        return scored_signals  # human_verdict stays None == PENDING_REVIEW

    payload = build_review_payload(review)
    provider = (
        verdict_provider
        or (run_id and _web_verdict_providers.get(run_id))
        or (lambda p: cli_collect_verdicts(p, timeout=timeout))
    )
    verdicts = await provider(payload)
    updated = apply_verdicts(scored_signals, verdicts or {})
    logger.info(
        "[hitl] review complete — %d confirmed/kept, %d dismissed, %d pending",
        sum(1 for s in updated if s.human_verdict is HumanVerdict.CONFIRMED),
        len(scored_signals) - len(updated),
        sum(1 for s in updated if s.requires_human_review and s.human_verdict is None),
    )
    return updated


__all__ = [
    "DEV_HITL_TIMEOUT",
    "VerdictProvider",
    "register_web_verdict_provider",
    "unregister_web_verdict_provider",
    "parse_verdict",
    "signals_for_review",
    "build_review_payload",
    "apply_verdicts",
    "cli_collect_verdicts",
    "run_human_review",
]
