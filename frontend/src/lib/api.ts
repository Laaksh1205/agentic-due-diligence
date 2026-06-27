// Thin API client for the FastAPI backend (Phase 4.1). Requests go to same-origin
// /api/* which the Next dev server proxies to the backend (see next.config.mjs).

import type {
  Candidate,
  DueDiligenceReport,
  RiskSignal,
  RunHistoryItem,
  RunStatusPayload,
  Scope,
  Verdict,
} from "./types";

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export interface AssessInput {
  company_name: string;
  scope: Scope;
  auto_mode: boolean;
  hitl_timeout?: number;
  registry_id?: string; // set when a candidate was chosen in the picker (§8c)
  jurisdiction?: string;
}

export async function startAssessment(input: AssessInput): Promise<{ run_id: string }> {
  const res = await fetch("/api/assess", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return jsonOrThrow(res);
}

/** Fetch up to 5 registry candidates for the entity picker (§8c). */
export async function resolveCandidates(
  companyName: string,
  jurisdiction = "",
): Promise<Candidate[]> {
  const data = await jsonOrThrow<{ candidates: Candidate[] }>(
    await fetch("/api/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company_name: companyName, jurisdiction }),
    }),
  );
  return data.candidates;
}

export async function getRunStatus(runId: string): Promise<RunStatusPayload> {
  return jsonOrThrow(await fetch(`/api/runs/${runId}`, { cache: "no-store" }));
}

export async function getReport(runId: string): Promise<DueDiligenceReport> {
  return jsonOrThrow(await fetch(`/api/runs/${runId}/report`, { cache: "no-store" }));
}

export interface SignalsPage {
  total: number;
  limit: number;
  offset: number;
  signals: RiskSignal[];
}

export async function getSignals(
  runId: string,
  opts: { category?: string; severity?: string; limit?: number; offset?: number } = {},
): Promise<SignalsPage> {
  const params = new URLSearchParams();
  if (opts.category) params.set("category", opts.category);
  if (opts.severity) params.set("severity", opts.severity);
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.offset != null) params.set("offset", String(opts.offset));
  const qs = params.toString();
  return jsonOrThrow(
    await fetch(`/api/runs/${runId}/signals${qs ? `?${qs}` : ""}`, { cache: "no-store" }),
  );
}

export async function submitVerdict(
  runId: string,
  signalId: string,
  verdict: Verdict,
): Promise<{ ok: boolean; status: string }> {
  const res = await fetch(`/api/runs/${runId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ signal_id: signalId, verdict }),
  });
  return jsonOrThrow(res);
}

export async function getRunHistory(): Promise<RunHistoryItem[]> {
  const data = await jsonOrThrow<{ runs: RunHistoryItem[] }>(
    await fetch("/api/runs", { cache: "no-store" }),
  );
  return data.runs;
}

export function exportUrl(runId: string, kind: "pdf" | "json"): string {
  return `/api/runs/${runId}/export/${kind}`;
}

/** ws:// URL for live progress, derived from the current page origin. */
export function wsUrl(runId: string): string {
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/${runId}`;
}
