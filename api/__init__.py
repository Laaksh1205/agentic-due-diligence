"""FastAPI backend for the Due Diligence platform — Phase 4.1.

Exposes the LangGraph pipeline over HTTP/WebSocket so the Next.js frontend
(Phase 4.2) can launch assessments, stream live progress, drive human-in-the-loop
review from the browser, and download PDF/JSON reports.
"""
