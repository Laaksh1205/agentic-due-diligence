import { expect, test, type Route } from "@playwright/test";

import {
  forcePolling,
  mockAssess,
  reportFixture,
  reviewSignalFixture,
  RUN_ID,
  statusPayload,
} from "./fixtures";

const STATUS_RE = new RegExp(`/api/runs/${RUN_ID}$`);
const REPORT_RE = new RegExp(`/api/runs/${RUN_ID}/report$`);
const REVIEW_RE = new RegExp(`/api/runs/${RUN_ID}/review$`);

function fulfillJson(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

// 4.3.1 — Full flow: search → confirm → progress → report → export.
test("full journey: search to report with live WebSocket progress", async ({ page }, testInfo) => {
  await mockAssess(page);
  await page.route(STATUS_RE, (r) => fulfillJson(r, statusPayload()));
  await page.route(REPORT_RE, (r) => fulfillJson(r, reportFixture()));

  // Live progress via WebSocket frames (the primary 4.2.4 path).
  await page.routeWebSocket(/\/ws\//, (ws) => {
    const send = (o: object) => ws.send(JSON.stringify(o));
    send(statusPayload({ status: "researching", progress_pct: 15 }));
    setTimeout(() => send(statusPayload({ status: "extracting", progress_pct: 50, current_agent: "Extracting risk signals" })), 200);
    setTimeout(() => send(statusPayload({ status: "analyzing", progress_pct: 78, current_agent: "Scoring risks" })), 400);
    setTimeout(() => send(statusPayload({ status: "complete", progress_pct: 100, current_agent: "Complete" })), 600);
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /due diligence in minutes/i })).toBeVisible();

  // Search (4.2.2)
  await page.getByPlaceholder(/Boeing, Stripe/i).fill("Acme Corp");
  await page.getByRole("button", { name: /assess/i }).click();

  // Confirm modal (4.2.3)
  await expect(page.getByRole("heading", { name: /confirm assessment/i })).toBeVisible();
  await page.getByRole("button", { name: /start assessment/i }).click();

  // Progress page (4.2.4)
  await expect(page).toHaveURL(new RegExp(`/runs/${RUN_ID}`));
  await expect(page.getByText(/Assessing Acme Corp/i)).toBeVisible();

  // Report dashboard (4.2.5–4.2.8)
  await expect(page.getByRole("heading", { name: "ACME CORPORATION" })).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("5.4")).toBeVisible(); // overall risk score
  await expect(page.getByText(/Class-action lawsuit/i)).toBeVisible(); // signals table
  await expect(page.getByText(/ISO 27001/i)).toBeVisible(); // strengths

  // Checkpoint: data-sufficiency badge visible + color-coded (amber for ADEQUATE).
  const badge = page.locator("span", { hasText: /^ADEQUATE$/ }).first();
  await expect(badge).toBeVisible();
  await expect(badge).toHaveClass(/amber/);

  // Checkpoint: radar chart renders category scores (recharts emits an <svg>).
  await expect(page.locator("svg.recharts-surface").first()).toBeVisible();

  // Export buttons (4.2.11) point at the right endpoints.
  await expect(page.getByRole("link", { name: /PDF/i })).toHaveAttribute("href", `/api/runs/${RUN_ID}/export/pdf`);
  await expect(page.getByRole("link", { name: /JSON/i })).toHaveAttribute("href", `/api/runs/${RUN_ID}/export/json`);

  // Capture a dashboard screenshot as portfolio-quality evidence (checkpoint).
  if (testInfo.project.name === "desktop") {
    await page.screenshot({ path: testInfo.outputPath("dashboard.png"), fullPage: true });
  }
});

// 4.3.2 — HITL flow: review a critical signal in the browser, pipeline resumes.
test("HITL flow: confirm a critical signal and resume to report", async ({ page }) => {
  await forcePolling(page); // exercise the polling fallback deterministically
  await mockAssess(page);

  let reviewed = false;
  await page.route(STATUS_RE, (r) =>
    fulfillJson(
      r,
      reviewed
        ? statusPayload({ status: "complete", progress_pct: 100, current_agent: "Complete" })
        : statusPayload({ status: "reviewing", progress_pct: 80, review_signals: [reviewSignalFixture()] }),
    ),
  );
  await page.route(REVIEW_RE, (r) => {
    reviewed = true;
    return fulfillJson(r, { ok: true, status: "synthesizing" });
  });
  await page.route(REPORT_RE, (r) => fulfillJson(r, reportFixture()));

  await page.goto(`/runs/${RUN_ID}`);

  // HITL card visible (4.2.9)
  await expect(page.getByText(/Human review required/i)).toBeVisible({ timeout: 15000 });
  await expect(page.getByText(/sanctions watchlist/i)).toBeVisible();

  await page.getByRole("button", { name: /^Confirm$/ }).click();

  // Pipeline resumes → report renders.
  await expect(page.getByRole("heading", { name: "ACME CORPORATION" })).toBeVisible({ timeout: 15000 });
});

// 4.3.3 — Error state: a failed run shows a friendly message, not a raw stack.
test("error state: failed run shows graceful message", async ({ page }) => {
  await forcePolling(page);
  await page.route(STATUS_RE, (r) =>
    fulfillJson(
      r,
      statusPayload({
        status: "error",
        progress_pct: 100,
        error: "entity_resolution: no match found for 'Zzzzz Nonexistent Co'",
      }),
    ),
  );

  await page.goto(`/runs/${RUN_ID}`);

  await expect(page.getByText(/Assessment failed/i)).toBeVisible({ timeout: 15000 });
  await expect(page.getByText(/no match found/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /try another company/i })).toBeVisible();
  // No unhandled React error overlay / raw traceback leaked to the user.
  await expect(page.locator("text=Traceback")).toHaveCount(0);
});

// 4.3.4 — Mobile responsiveness: landing usable and submittable at 360px.
test("landing page is usable on a narrow viewport", async ({ page }) => {
  await mockAssess(page);
  await page.goto("/");

  const input = page.getByPlaceholder(/Boeing, Stripe/i);
  await expect(input).toBeVisible();
  await input.fill("Acme Corp");

  const assess = page.getByRole("button", { name: /assess/i });
  await expect(assess).toBeVisible();
  await assess.click();
  await expect(page.getByRole("heading", { name: /confirm assessment/i })).toBeVisible();

  // No horizontal overflow (content fits the viewport width).
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(2);
});
