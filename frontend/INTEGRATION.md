# Phase 4.3 — Web Integration Testing

End-to-end coverage of the full web journey. Two layers, both deterministic
(no API keys, network, or LLM calls — the pipeline is faked / the network mocked):

| Layer | Where | Drives |
|---|---|---|
| Browser E2E (Playwright) | `frontend/e2e/journey.spec.ts` | The real built UI in headless Chromium, `/api` + `/ws` mocked |
| Backend integration (pytest) | `tests/test_integration_web.py` | The real FastAPI app via `TestClient`, pipeline faked |

## Task coverage

| Task | Scenario | Browser E2E | Backend |
|---|---|---|---|
| 4.3.1 | Full flow: search → confirm → progress → report → export | ✓ `full journey…` | ✓ `test_full_journey_assess_to_export` |
| 4.3.2 | HITL: submit, review in browser, pipeline resumes | ✓ `HITL flow…` | ✓ (see `tests/test_api.py::test_review_gate_resumes_pipeline`) |
| 4.3.3 | Error states: source down, empty results, not-ready, unknown run | ✓ `error state…` | ✓ `test_pipeline_crash_surfaces_as_error_status`, `test_empty_results_complete_without_report`, `test_report_not_ready_returns_409`, `test_unknown_run_returns_404` |
| 4.3.4 | Mobile responsiveness at 360px (no horizontal overflow) | ✓ `landing page is usable…` + `mobile` project (every test) | — |

The Playwright `mobile` project re-runs the entire suite at a 360px Pixel-5
viewport, so the full flow, HITL, and error states are all verified on mobile too.

## Running

```bash
# Browser E2E (builds artifacts first, then runs both desktop + mobile projects)
cd frontend
npm run build
npm run test:e2e            # 8 tests (4 scenarios × 2 viewports)

# Backend integration
cd ..
.venv/Scripts/python -m pytest tests/test_integration_web.py tests/test_api.py
```

Playwright launches the production server itself (`next start` on `E2E_PORT`,
default 3100) via its `webServer` config — no manual server needed.

## Phase 4 checkpoint mapping

- Non-technical user can go start→finish → `full journey` test
- Dashboard renders with real data (radar, signals, strengths) → `full journey`
- HITL review works in browser → `HITL flow`
- PDF & JSON export wired → export-link assertions in `full journey`
- Data-sufficiency badge + radar render → asserted in `full journey`
- Graceful errors, no raw tracebacks → `error state`
