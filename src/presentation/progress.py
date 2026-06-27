"""
Real-time progress indicators — Task 2.9.3.

A small ``rich``-status wrapper that shows a spinner with the current agent
("Researching…", "Extracting risk signals…", …) while the pipeline runs. Used as
a context manager; advance with ``.stage(name)``. Disables itself when output is
not a TTY (or ``enabled=False``) so logs/tests aren't cluttered.
"""

from __future__ import annotations

from typing import Optional

from rich.console import Console

# Friendly labels per pipeline stage.
STAGE_LABELS = {
    "entity_resolution": "Resolving entity…",
    "research": "Researching across sources…",
    "data_sufficiency": "Assessing data sufficiency…",
    "extraction": "Extracting risk signals…",
    "risk_analysis": "Analyzing & scoring risks…",
    "hitl": "Awaiting human review…",
    "synthesis": "Synthesizing report…",
    "presentation": "Rendering report…",
}


class PipelineProgress:
    """Context manager showing a live spinner for the current pipeline stage."""

    def __init__(self, console: Optional[Console] = None, *, enabled: bool = True):
        self.console = console or Console()
        self.enabled = enabled and self.console.is_terminal
        self._status = None

    def __enter__(self) -> "PipelineProgress":
        if self.enabled:
            self._status = self.console.status("Starting…", spinner="dots")
            self._status.__enter__()
        return self

    def stage(self, name: str) -> None:
        """Update the spinner to the given stage (key or free text)."""
        label = STAGE_LABELS.get(name, name)
        if self._status is not None:
            self._status.update(label)
        elif self.enabled:
            self.console.log(label)

    def __exit__(self, *exc) -> None:
        if self._status is not None:
            self._status.__exit__(*exc)
            self._status = None


__all__ = ["PipelineProgress", "STAGE_LABELS"]
