// Shared fixtures + network-mock helpers for the E2E journey tests.

import type { Page, Route } from "@playwright/test";

export const RUN_ID = "e2e-run-0001";

/** A complete, realistic DueDiligenceReport the /report endpoint returns. */
export function reportFixture() {
  return {
    target_entity: {
      canonical_name: "ACME CORPORATION",
      aliases: ["Acme", "Acme Corp"],
      jurisdiction: "us-de",
      industry: "Manufacturing",
      is_public: true,
      sec_cik: "0000012345",
    },
    evaluation_scope: "full",
    data_sufficiency: "ADEQUATE",
    risk_signals: [
      {
        id: "sig-legal-1",
        text: "Class-action lawsuit filed over defective product line.",
        source_url: "https://news.example.com/acme-lawsuit",
        source_type: "NEWS_ARTICLE",
        source_snippet: "plaintiffs allege Acme knowingly shipped defective units",
        data_date: "2025-02-01",
        confidence_score: 0.91,
        temporal_weight: 0.95,
        risk_category: "LEGAL",
        severity: "HIGH",
        signal_polarity: "NEGATIVE",
        entity_name: "ACME CORPORATION",
        is_corroborated: true,
        corroborating_signals: ["sig-legal-2"],
        requires_human_review: false,
        human_verdict: null,
        is_contradictory: false,
      },
      {
        id: "sig-fin-1",
        text: "Operating margin declined for three consecutive quarters.",
        source_url: "https://sec.gov/acme-10q",
        source_type: "SEC_FILING",
        source_snippet: "operating margin fell to 4.2% from 9.1% year-over-year",
        data_date: "2025-03-15",
        confidence_score: 0.84,
        temporal_weight: 0.98,
        risk_category: "FINANCIAL",
        severity: "MEDIUM",
        signal_polarity: "NEGATIVE",
        entity_name: "ACME CORPORATION",
        is_corroborated: false,
        corroborating_signals: [],
        requires_human_review: false,
        human_verdict: null,
        is_contradictory: false,
      },
    ],
    positive_signals: [
      {
        id: "sig-pos-1",
        text: "Awarded ISO 27001 certification for information security.",
        source_url: "https://acme.example.com/press/iso",
        source_type: "COMPANY_WEBSITE",
        source_snippet: "Acme achieved ISO/IEC 27001 certification across all data centers",
        data_date: "2025-01-10",
        confidence_score: 0.88,
        temporal_weight: 0.92,
        risk_category: "CYBERSECURITY",
        severity: "INFO",
        signal_polarity: "POSITIVE",
        entity_name: "ACME CORPORATION",
        is_corroborated: false,
        corroborating_signals: [],
        requires_human_review: false,
        human_verdict: null,
        is_contradictory: false,
      },
    ],
    category_scores: { LEGAL: 6.5, FINANCIAL: 5.2, CYBERSECURITY: 2.0 },
    overall_risk_score: 5.4,
    executive_summary:
      "Acme Corporation presents a moderate risk profile driven by active litigation and a softening financial position, partially offset by strong security posture.",
    strengths_section: "Strong information-security certifications.",
    detailed_sections: { LEGAL: "An active class action concerns product defects." },
    recommended_actions: [
      { description: "Obtain outside counsel assessment of the class action.", priority: "IMMEDIATE", related_signals: [] },
      { description: "Monitor quarterly margin trend.", priority: "MONITOR", related_signals: [] },
    ],
    sources_consulted: [
      { url: "https://sec.gov", source_type: "SEC_FILING", name: "SEC EDGAR", error: null },
      { url: "https://news.example.com", source_type: "NEWS_ARTICLE", name: "Web search", error: null },
    ],
    sources_failed: [
      { url: "https://registry.example.com", source_type: "COMPANY_REGISTRY", name: "Registry Lookup", error: "geo_blocked" },
    ],
    metadata: {
      run_id: RUN_ID,
      created_at: "2026-06-26T12:00:00Z",
      estimated_cost_usd: 0.18,
      latency_seconds: 92.3,
      llm_call_count: 14,
      signals_extracted: 3,
      signals_rejected: 1,
    },
  };
}

export function reviewSignalFixture() {
  return {
    id: "sig-critical-1",
    text: "Entity appears on a sanctions watchlist under a close-name match.",
    category: "REGULATORY",
    severity: "CRITICAL",
    source_url: "https://sanctions.example.com/match",
    source_snippet: "a close name match to a designated entity was identified",
  };
}

function statusPayload(overrides: Record<string, unknown> = {}) {
  return {
    run_id: RUN_ID,
    company_name: "Acme Corp",
    scope: "full",
    status: "researching",
    progress_pct: 10,
    current_agent: "Resolving entity",
    error: null,
    review_signals: [],
    ...overrides,
  };
}

/** Mock POST /api/assess → returns our fixed run id. */
export async function mockAssess(page: Page) {
  await page.route("**/api/assess", (route: Route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ run_id: RUN_ID }) }),
  );
}

/** Mock POST /api/resolve → the entity picker candidate list (design §8c). */
export async function mockResolve(page: Page, candidates?: object[]) {
  const body = {
    candidates: candidates ?? [
      {
        registry_id: "rl-acme-1",
        name: "ACME CORPORATION",
        jurisdiction: "us-de",
        status: "active",
        company_type: "corporation",
        address: "Delaware, USA",
        is_public: true,
      },
    ],
  };
  await page.route("**/api/resolve", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) }),
  );
}

/** Force the WebSocket to close so the UI falls back to polling (deterministic). */
export async function forcePolling(page: Page) {
  await page.routeWebSocket(/\/ws\//, (ws) => ws.close());
}

export { statusPayload };
