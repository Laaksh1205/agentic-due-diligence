// Types mirroring the backend Pydantic models (src/models/*) and API shapes.

export type Scope = "full" | "financial" | "compliance";

// One registry match shown in the entity picker (design §8c).
export interface Candidate {
  registry_id: string;
  name: string;
  jurisdiction: string;
  status: string;
  company_type: string;
  address: string;
  is_public: boolean;
}

export type RunStatus =
  | "queued"
  | "researching"
  | "extracting"
  | "analyzing"
  | "reviewing"
  | "synthesizing"
  | "complete"
  | "error";

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO";

export type RiskCategory =
  | "FINANCIAL"
  | "LEGAL"
  | "REGULATORY"
  | "REPUTATIONAL"
  | "OPERATIONAL"
  | "CYBERSECURITY"
  | "ESG";

export type SignalPolarity = "NEGATIVE" | "POSITIVE" | "NEUTRAL";

export type DataSufficiency = "RICH" | "ADEQUATE" | "LIMITED" | "SPARSE";

export type HumanVerdict = "CONFIRMED" | "DISMISSED" | "NEEDS_INVESTIGATION";

export type Verdict = "confirm" | "dismiss" | "investigate";

export interface ReviewSignal {
  id: string;
  text: string;
  category: string;
  severity: string;
  source_url: string;
  source_snippet: string;
}

export interface RunStatusPayload {
  run_id: string;
  company_name: string;
  scope: Scope;
  status: RunStatus;
  progress_pct: number;
  current_agent: string;
  error: string | null;
  review_signals: ReviewSignal[];
}

export interface RiskSignal {
  id: string;
  text: string;
  source_url: string;
  source_type: string;
  source_snippet: string;
  data_date: string | null;
  confidence_score: number;
  temporal_weight: number;
  risk_category: RiskCategory;
  severity: Severity;
  signal_polarity: SignalPolarity;
  entity_name: string;
  is_corroborated: boolean;
  corroborating_signals: string[];
  requires_human_review: boolean;
  human_verdict: HumanVerdict | null;
  is_contradictory: boolean;
}

export interface ResolvedEntity {
  canonical_name: string;
  aliases: string[];
  jurisdiction: string | null;
  industry: string | null;
  is_public: boolean;
  registry_lookup_id?: string | null;
  companies_house_number?: string | null;
  sec_cik?: string | null;
}

export interface ReportSource {
  url: string;
  source_type: string;
  name: string;
  error: string | null;
}

export interface ReportAction {
  description: string;
  priority: "IMMEDIATE" | "SHORT_TERM" | "MONITOR";
  related_signals: string[];
}

export interface ReportMetadata {
  run_id: string;
  created_at: string;
  estimated_cost_usd: number;
  latency_seconds: number;
  llm_call_count: number;
  signals_extracted: number;
  signals_rejected: number;
}

export interface DueDiligenceReport {
  target_entity: ResolvedEntity;
  evaluation_scope: string;
  data_sufficiency: DataSufficiency;
  risk_signals: RiskSignal[];
  positive_signals: RiskSignal[];
  category_scores: Partial<Record<RiskCategory, number>>;
  overall_risk_score: number;
  executive_summary: string;
  strengths_section: string;
  detailed_sections: Partial<Record<RiskCategory, string>>;
  recommended_actions: ReportAction[];
  sources_consulted: ReportSource[];
  sources_failed: ReportSource[];
  metadata: ReportMetadata;
}

export interface RunHistoryItem {
  run_id: string;
  entity_name: string;
  created_at: string | null;
  status: string;
}
