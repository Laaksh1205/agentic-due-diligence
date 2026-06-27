"""
Tests for the Human-in-the-Loop gate — Task 2.8.5.

No network/LLM. CLI input is injected via input_fn; the timeout path uses a
slow input_fn to force asyncio.wait_for to fire deterministically.
"""

import asyncio

import pytest
from rich.console import Console

from src.agents import hitl
from src.agents.hitl import (
    apply_verdicts,
    build_review_payload,
    cli_collect_verdicts,
    run_human_review,
    signals_for_review,
)
from src.agents.supervisor import _node_hitl_gate
from src.models.signals import (
    HumanVerdict,
    RiskCategory,
    RiskSignal,
    Severity,
    SignalPolarity,
    SourceType,
)

_QUIET = Console(quiet=True)  # swallow rich output in tests


def _sig(text, *, review=True, sev=Severity.CRITICAL, conf=0.9):
    return RiskSignal(
        text=text, source_url="https://x/a", source_type=SourceType.NEWS_ARTICLE,
        source_snippet="snippet anchoring the signal text for the record here please",
        confidence_score=conf, risk_category=RiskCategory.LEGAL, severity=sev,
        signal_polarity=SignalPolarity.NEGATIVE, entity_name="Acme Corp",
        requires_human_review=review,
    )


def _seq_input(answers):
    it = iter(answers)
    return lambda prompt="": next(it)


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_signals_for_review_filters():
    a, b = _sig("flagged", review=True), _sig("clean", review=False)
    assert signals_for_review([a, b]) == [a]


def test_apply_verdicts_dismiss_removes_confirm_keeps_investigate_keeps():
    a, b, c = _sig("a"), _sig("b"), _sig("c")
    verdicts = {str(a.id): "confirm", str(b.id): "dismiss", str(c.id): "investigate"}
    out = apply_verdicts([a, b, c], verdicts)
    assert len(out) == 2                                    # b dismissed → dropped
    kept = {s.text: s for s in out}
    assert kept["a"].human_verdict is HumanVerdict.CONFIRMED
    assert kept["a"].requires_human_review is False
    assert kept["c"].human_verdict is HumanVerdict.NEEDS_INVESTIGATION


def test_apply_verdicts_pending_when_no_verdict():
    a = _sig("a")
    out = apply_verdicts([a], {})                           # timeout / no response
    assert len(out) == 1
    assert out[0].human_verdict is None                     # PENDING_REVIEW
    assert out[0].requires_human_review is True


def test_apply_verdicts_does_not_mutate_input():
    a = _sig("a")
    apply_verdicts([a], {str(a.id): "confirm"})
    assert a.human_verdict is None and a.requires_human_review is True


# ── run_human_review (the gate) ───────────────────────────────────────────────

async def test_auto_mode_skips_and_leaves_pending():
    sigs = [_sig("critical thing"), _sig("clean", review=False)]
    out = await run_human_review(sigs, auto_mode=True, timeout=60)
    assert out == sigs                                       # unchanged
    assert out[0].human_verdict is None and out[0].requires_human_review is True


async def test_no_review_signals_returns_unchanged():
    sigs = [_sig("clean", review=False)]
    out = await run_human_review(sigs, auto_mode=False, timeout=60,
                                 verdict_provider=lambda p: _fail("should not be called"))
    assert out == sigs


def _fail(msg):
    raise AssertionError(msg)


async def test_manual_mode_applies_injected_verdicts():
    a, b = _sig("dismiss me"), _sig("keep me")

    async def provider(payload):
        return {payload[0]["id"]: "dismiss", payload[1]["id"]: "confirm"}

    out = await run_human_review([a, b], auto_mode=False, timeout=60, verdict_provider=provider)
    assert [s.text for s in out] == ["keep me"]
    assert out[0].human_verdict is HumanVerdict.CONFIRMED


# ── CLI collection + timeout ──────────────────────────────────────────────────

async def test_cli_collect_verdicts_reads_inputs():
    a, b = _sig("a"), _sig("b")
    payload = build_review_payload([a, b])
    verdicts = await cli_collect_verdicts(
        payload, timeout=10, input_fn=_seq_input(["c", "d"]), console=_QUIET,
    )
    assert verdicts == {payload[0]["id"]: "c", payload[1]["id"]: "d"}


async def test_cli_collect_verdicts_reprompts_on_bad_input():
    a = _sig("a")
    payload = build_review_payload([a])
    verdicts = await cli_collect_verdicts(
        payload, timeout=10, input_fn=_seq_input(["x", "?", "i"]), console=_QUIET,
    )
    assert verdicts == {payload[0]["id"]: "i"}


async def test_timeout_returns_empty_and_leaves_pending():
    a = _sig("slow review")
    payload = build_review_payload([a])

    def _slow(prompt=""):
        import time
        time.sleep(1.0)        # longer than the timeout below
        return "c"

    verdicts = await cli_collect_verdicts(payload, timeout=0.2, input_fn=_slow, console=_QUIET)
    assert verdicts == {}                                   # never auto-confirmed
    assert apply_verdicts([a], verdicts)[0].requires_human_review is True


async def test_cli_collect_verdicts_handles_eof_stdin():
    # Non-interactive / closed stdin → input() raises EOFError → leave pending.
    a = _sig("a")
    payload = build_review_payload([a])

    def _eof(prompt=""):
        raise EOFError

    verdicts = await cli_collect_verdicts(payload, timeout=10, input_fn=_eof, console=_QUIET)
    assert verdicts == {}
    assert apply_verdicts([a], verdicts)[0].requires_human_review is True


async def test_run_human_review_timeout_path_leaves_pending():
    a = _sig("critical")

    async def slow_provider(payload):
        # Simulate cli_collect_verdicts timing out → returns {}
        return await cli_collect_verdicts(
            payload, timeout=0.2, input_fn=lambda p="": __import__("time").sleep(1) or "c",
            console=_QUIET,
        )

    out = await run_human_review([a], auto_mode=False, timeout=0.2, verdict_provider=slow_provider)
    assert out[0].human_verdict is None and out[0].requires_human_review is True


# ── Supervisor node integration ───────────────────────────────────────────────

async def test_node_auto_mode_passes_through():
    state = {"scored_signals": [_sig("crit")], "auto_mode": True, "hitl_timeout": 60}
    out = await _node_hitl_gate(state)
    assert "scored_signals" in out
    assert out["scored_signals"][0].requires_human_review is True


async def test_node_manual_mode_applies_verdicts(monkeypatch):
    a, b = _sig("dismiss"), _sig("confirm")

    async def fake_review(payload):
        return {payload[0]["id"]: "dismiss", payload[1]["id"]: "confirm"}

    # patch the verdict source used by the node's run_human_review
    monkeypatch.setattr(
        hitl, "cli_collect_verdicts",
        lambda payload, **kw: fake_review(payload),
    )
    state = {"scored_signals": [a, b], "auto_mode": False, "hitl_timeout": 60}
    out = await _node_hitl_gate(state)
    assert [s.text for s in out["scored_signals"]] == ["confirm"]
