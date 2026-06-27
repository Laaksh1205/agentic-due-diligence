"""
CLI entry point — Task 1.8.4

Usage:
    python src/main.py "Tesla Inc"
    python src/main.py "Tesla Inc" --scope full --auto --no-cache --verbose
    python src/main.py "Stripe" --scope financial --auto
"""

import argparse
import asyncio
import logging
import sys

# Windows consoles default to cp1252 which can't encode box-drawing chars.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="due-diligence",
        description="Agentic Due Diligence Intelligence Platform",
    )
    p.add_argument(
        "entity",
        help='Company or entity name to assess (e.g. "Tesla Inc")',
    )
    p.add_argument(
        "--scope",
        choices=["full", "financial", "compliance"],
        default="full",
        metavar="SCOPE",
        help="Evaluation scope: full (default), financial, or compliance",
    )
    p.add_argument(
        "--auto",
        action="store_true",
        help="Skip human-in-the-loop review gate (auto-proceed through CRITICAL findings)",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        dest="no_cache",
        help="Bypass entity resolution and document caches for this run",
    )
    p.add_argument(
        "--hitl-timeout",
        type=int,
        default=None,
        dest="hitl_timeout",
        metavar="SECONDS",
        help="Seconds to wait for human review before auto-proceeding (PENDING_REVIEW). "
             "Default 24h; use e.g. 60 in dev.",
    )
    p.add_argument(
        "--pdf",
        action="store_true",
        help="Export a PDF report to output/ after the pipeline completes (requires weasyprint)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return p


async def _run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.no_cache:
        from src.config import settings
        settings.use_cache = False
        logging.getLogger(__name__).info("Cache disabled for this run.")

    from src.agents.supervisor import run_pipeline

    final_state = await run_pipeline(
        args.entity,
        scope=args.scope,
        auto_mode=args.auto,
        hitl_timeout=args.hitl_timeout,
    )

    # Structured JSON export (Task 2.9.2): full report once synthesised,
    # otherwise the scored signals (e.g. when guardrails halt before synthesis).
    try:
        report = final_state.get("report")
        signals = final_state.get("scored_signals") or final_state.get("raw_signals") or []
        if report is not None and getattr(report, "risk_signals", None):
            from src.presentation.json_export import export_report
            path = export_report(report)
        else:
            from src.presentation.json_export import export_signals
            entity = final_state.get("resolved_entity")
            path = export_signals(
                signals,
                entity_name=entity.canonical_name if entity else args.entity,
            )
        logging.getLogger(__name__).info("JSON report written to %s", path)
    except Exception as exc:  # never fail the run on export issues
        logging.getLogger(__name__).warning("JSON export failed: %s", exc)

    # PDF export (Task 3.4) — only attempted when --pdf flag is set.
    if args.pdf:
        try:
            import time as _time
            from src.presentation.pdf_export import export_pdf
            _report = final_state.get("report")
            if _report is None:
                logging.getLogger(__name__).warning(
                    "PDF export skipped — no report produced for this run"
                )
            else:
                _start = final_state.get("start_time")
                _elapsed = (_time.monotonic() - _start) if _start else 0.0
                pdf_path = export_pdf(_report, elapsed_seconds=_elapsed)
                logging.getLogger(__name__).info("PDF report written to %s", pdf_path)
        except RuntimeError as exc:
            logging.getLogger(__name__).warning("PDF export failed: %s", exc)
        except Exception as exc:
            logging.getLogger(__name__).warning("PDF export failed: %s", exc)

    errors = final_state.get("errors") or []
    return 1 if errors else 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
