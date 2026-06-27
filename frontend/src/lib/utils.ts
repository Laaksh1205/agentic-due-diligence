import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import type { DataSufficiency, RunStatus, Severity } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Severity → Tailwind colour classes (matches the PDF/CLI colour scheme).
export const SEVERITY_COLOR: Record<Severity, { dot: string; text: string; bg: string }> = {
  CRITICAL: { dot: "bg-red-500", text: "text-red-400", bg: "bg-red-500/15 text-red-300 border-red-500/30" },
  HIGH: { dot: "bg-orange-500", text: "text-orange-400", bg: "bg-orange-500/15 text-orange-300 border-orange-500/30" },
  MEDIUM: { dot: "bg-amber-500", text: "text-amber-400", bg: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  LOW: { dot: "bg-blue-500", text: "text-blue-400", bg: "bg-blue-500/15 text-blue-300 border-blue-500/30" },
  INFO: { dot: "bg-gray-500", text: "text-gray-400", bg: "bg-gray-500/15 text-gray-300 border-gray-500/30" },
};

export const SUFFICIENCY_COLOR: Record<DataSufficiency, string> = {
  RICH: "bg-green-500/15 text-green-300 border-green-500/30",
  ADEQUATE: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  LIMITED: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  SPARSE: "bg-red-500/15 text-red-300 border-red-500/30",
};

export const SUFFICIENCY_HELP: Record<DataSufficiency, string> = {
  RICH: "15+ documents across 4+ source types — comprehensive coverage.",
  ADEQUATE: "8–14 documents across 3+ source types — solid coverage.",
  LIMITED: "4–7 documents or only 2 source types — partial coverage.",
  SPARSE: "Fewer than 4 documents or a single source type — thin data; treat findings as indicative.",
};

// The visible pipeline steps (4.2.4) and which run statuses light each one.
export const PIPELINE_STEPS = [
  { key: "entity", label: "Entity Resolution", statuses: ["researching"] },
  { key: "research", label: "Research", statuses: ["researching"] },
  { key: "extraction", label: "Extraction", statuses: ["extracting"] },
  { key: "analysis", label: "Analysis", statuses: ["analyzing"] },
  { key: "review", label: "Review", statuses: ["reviewing"] },
  { key: "synthesis", label: "Synthesis", statuses: ["synthesizing"] },
] as const;

const STATUS_ORDER: RunStatus[] = [
  "queued",
  "researching",
  "extracting",
  "analyzing",
  "reviewing",
  "synthesizing",
  "complete",
];

/** Index of the currently-active pipeline step for a given run status. */
export function activeStepIndex(status: RunStatus): number {
  const map: Partial<Record<RunStatus, number>> = {
    queued: 0,
    researching: 1,
    extracting: 2,
    analyzing: 3,
    reviewing: 4,
    synthesizing: 5,
    complete: 6,
    error: -1,
  };
  return map[status] ?? 0;
}

export function statusRank(status: RunStatus): number {
  const i = STATUS_ORDER.indexOf(status);
  return i === -1 ? STATUS_ORDER.length : i;
}

export function isTerminal(status: RunStatus): boolean {
  return status === "complete" || status === "error";
}

export function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}
