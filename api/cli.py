"""Launcher for the FastAPI backend — Task 4.1.1.

Usage:
    ddp-api                       # serve on 0.0.0.0:8000
    ddp-api --port 9000 --reload  # dev mode with auto-reload
    python -m api.cli
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="ddp-api", description="Due Diligence Platform API server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("api.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
