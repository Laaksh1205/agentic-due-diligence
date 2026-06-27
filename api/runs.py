"""In-memory run manager — Phase 4.1.

Owns the lifecycle of each assessment: launches the LangGraph pipeline as a
background task, tracks live status/progress, fans status updates out to
WebSocket subscribers, and bridges browser HITL verdicts into the pipeline's
human-review gate.

Runs live in process memory (the report itself is also persisted to SQLite by
the synthesis node). A single-process MVP backend; swap this for Redis/a task
queue if you ever need multiple workers.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# Verdict strings accepted by POST /review, mapped 1:1 to hitl.parse_verdict.
VALID_VERDICTS = {"confirm", "dismiss", "investigate"}

# node name → (status, progress_pct, human label) as each node *completes*.
# Statuses match the design doc 4.1.3 vocabulary; "synthesizing"/"resolving"
# extend it for finer-grained UI feedback.
_NODE_STAGE: dict[str, tuple[str, int, str]] = {
    "entity_resolution":      ("researching",  15, "Entity resolved"),
    "research":               ("researching",  35, "Gathering documents"),
    "data_sufficiency_check": ("extracting",   45, "Assessing data sufficiency"),
    "extraction":             ("analyzing",    65, "Extracting risk signals"),
    "risk_analysis":          ("analyzing",    78, "Scoring & deduplicating risks"),
    "hitl_gate":              ("synthesizing", 84, "Review complete"),
    "synthesis":              ("synthesizing", 94, "Synthesizing report"),
    "presentation":           ("synthesizing", 98, "Rendering report"),
}

# Default seconds to wait for browser review before auto-proceeding
# (PENDING_REVIEW). Shorter than the 24h CLI prod default so a web run never
# hangs a connection indefinitely.
DEFAULT_WEB_HITL_TIMEOUT = 1800


class Run:
    """Mutable state for one assessment run."""

    def __init__(self, run_id: str, company_name: str, scope: str, auto_mode: bool):
        self.run_id = run_id
        self.company_name = company_name
        self.scope = scope
        self.auto_mode = auto_mode
        self.registry_id: str = ""    # set when the user picked a candidate (§8c)
        self.jurisdiction: str = ""

        self.status: str = "queued"
        self.progress_pct: int = 0
        self.current_agent: str = "Queued"
        self.created_at: float = time.time()
        self.error: Optional[str] = None

        # Results (populated when status == "complete").
        self.report: Optional[dict] = None
        self.signals: list[dict] = []

        # HITL bridge.
        self.hitl_timeout: int = DEFAULT_WEB_HITL_TIMEOUT
        self.review_payload: list[dict] = []
        self._expected_ids: set[str] = set()
        self._collected: dict[str, str] = {}
        self._review_event: Optional[asyncio.Event] = None

        # Background task + WS subscriber queues.
        self.task: Optional[asyncio.Task] = None
        self._subscribers: set[asyncio.Queue] = set()

    # ── Public status snapshot ────────────────────────────────────────────────

    def status_payload(self) -> dict:
        """The shape returned by GET /runs/{id} and pushed over the WebSocket."""
        return {
            "run_id": self.run_id,
            "company_name": self.company_name,
            "scope": self.scope,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "current_agent": self.current_agent,
            "error": self.error,
            # Surface pending review items so the UI can render the HITL gate
            # without a second round-trip.
            "review_signals": self.review_payload if self.status == "reviewing" else [],
        }


class RunManager:
    """Tracks all runs and brokers HITL + progress for the FastAPI app."""

    def __init__(self):
        self._runs: dict[str, Run] = {}

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, run_id: str) -> Optional[Run]:
        return self._runs.get(run_id)

    # ── Launch ────────────────────────────────────────────────────────────────

    def start(
        self,
        company_name: str,
        *,
        scope: str = "full",
        auto_mode: bool = False,
        hitl_timeout: Optional[int] = None,
        registry_id: str = "",
        jurisdiction: str = "",
    ) -> Run:
        """Create a run and schedule the pipeline as a background task."""
        run_id = str(uuid.uuid4())
        run = Run(run_id, company_name, scope, auto_mode)
        run.registry_id = registry_id
        run.jurisdiction = jurisdiction
        if hitl_timeout is not None:
            run.hitl_timeout = hitl_timeout
        self._runs[run_id] = run
        run.task = asyncio.create_task(self._execute(run))
        return run

    async def _execute(self, run: Run) -> None:
        """Background driver: register HITL bridge, run pipeline, capture results."""
        from src.agents import hitl
        from src.agents.supervisor import run_pipeline

        run.status = "researching"
        run.progress_pct = 5
        run.current_agent = "Resolving entity"
        self._broadcast(run)

        # Browser-driven verdict provider for this run's HITL gate (4.1.6).
        async def web_verdict_provider(payload: list[dict]) -> dict[str, str]:
            return await self._await_review(run, payload)

        if not run.auto_mode:
            hitl.register_web_verdict_provider(run.run_id, web_verdict_provider)

        def progress_cb(node_name: str) -> None:
            stage = _NODE_STAGE.get(node_name)
            if not stage:
                return
            status, pct, label = stage
            # Don't override an active "reviewing" pause from the HITL provider.
            if run.status != "reviewing":
                run.status = status
            run.progress_pct = max(run.progress_pct, pct)
            run.current_agent = label
            self._broadcast(run)

        try:
            final_state = await run_pipeline(
                run.company_name,
                scope=run.scope,
                auto_mode=run.auto_mode,
                hitl_timeout=run.hitl_timeout,
                run_id=run.run_id,
                progress_cb=progress_cb,
                registry_id=run.registry_id,
                jurisdiction=run.jurisdiction,
            )
            self._finalize(run, final_state)
        except Exception as exc:  # pipeline already guards itself; this is a backstop
            logger.exception("[run %s] pipeline crashed: %s", run.run_id, exc)
            run.status = "error"
            run.error = str(exc)
        finally:
            hitl.unregister_web_verdict_provider(run.run_id)
            self._broadcast(run)

    def _finalize(self, run: Run, final_state: dict) -> None:
        """Capture report/signals from the final pipeline state."""
        errors = final_state.get("errors") or []
        report = final_state.get("report")

        if report is not None:
            run.report = report.model_dump(mode="json")
            run.signals = [
                s.model_dump(mode="json")
                for s in (list(report.risk_signals) + list(report.positive_signals))
            ]
        else:
            # Guardrail-halted or entity-not-found run: still surface any signals.
            scored = final_state.get("scored_signals") or final_state.get("raw_signals") or []
            run.signals = [s.model_dump(mode="json") for s in scored]

        run.review_payload = []
        if errors and report is None:
            run.status = "error"
            run.error = "; ".join(str(e) for e in errors)
            run.progress_pct = 100
        else:
            run.status = "complete"
            run.progress_pct = 100
            run.current_agent = "Complete"

    # ── HITL bridge (4.1.6) ───────────────────────────────────────────────────

    async def _await_review(self, run: Run, payload: list[dict]) -> dict[str, str]:
        """Pause the pipeline until verdicts arrive from POST /review or timeout."""
        run.status = "reviewing"
        run.current_agent = "Awaiting human review"
        run.progress_pct = max(run.progress_pct, 80)
        run.review_payload = payload
        run._expected_ids = {p["id"] for p in payload}
        run._collected = {}
        run._review_event = asyncio.Event()
        self._broadcast(run)
        logger.info("[run %s] HITL: awaiting %d verdict(s)", run.run_id, len(payload))

        try:
            await asyncio.wait_for(run._review_event.wait(), timeout=run.hitl_timeout)
        except asyncio.TimeoutError:
            logger.info(
                "[run %s] HITL timed out after %ss — %d/%d reviewed",
                run.run_id, run.hitl_timeout, len(run._collected), len(payload),
            )

        verdicts = dict(run._collected)
        run.review_payload = []
        run._review_event = None
        return verdicts

    def submit_verdict(self, run: Run, signal_id: str, verdict: str) -> None:
        """Record one verdict; resume the pipeline once all are in. Raises on bad input."""
        if run.status != "reviewing" or run._review_event is None:
            raise ValueError("run is not awaiting review")
        if signal_id not in run._expected_ids:
            raise KeyError(f"signal {signal_id} is not under review")
        verdict = (verdict or "").strip().lower()
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"verdict must be one of {sorted(VALID_VERDICTS)}")

        run._collected[signal_id] = verdict
        self._broadcast(run)
        if run._expected_ids <= set(run._collected):
            run._review_event.set()  # all reviewed → resume

    # ── WebSocket fan-out (4.1.9) ─────────────────────────────────────────────

    def subscribe(self, run: Run) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers_for(run).add(q)
        return q

    def unsubscribe(self, run: Run, q: asyncio.Queue) -> None:
        self._subscribers_for(run).discard(q)

    def _subscribers_for(self, run: Run) -> set:
        return run._subscribers

    def _broadcast(self, run: Run) -> None:
        """Push the current status snapshot to every subscriber (non-blocking)."""
        payload = run.status_payload()
        for q in list(run._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover - unbounded queues
                pass


# Module-level singleton used by the FastAPI app.
manager = RunManager()
