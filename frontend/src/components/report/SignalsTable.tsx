"use client";

import { useMemo, useState } from "react";

import { SeverityDot } from "@/components/badges";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import type { RiskCategory, RiskSignal, Severity } from "@/lib/types";
import { SignalDetail } from "./SignalDetail";

const CATEGORIES: RiskCategory[] = [
  "FINANCIAL",
  "LEGAL",
  "REGULATORY",
  "REPUTATIONAL",
  "OPERATIONAL",
  "CYBERSECURITY",
  "ESG",
];
const SEVERITIES: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
const SEV_RANK: Record<Severity, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
  INFO: 4,
};

function domain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function SignalsTable({ signals }: { signals: RiskSignal[] }) {
  const [category, setCategory] = useState<string>("");
  const [severity, setSeverity] = useState<string>("");
  const [selected, setSelected] = useState<RiskSignal | null>(null);

  const filtered = useMemo(() => {
    return signals
      .filter((s) => (category ? s.risk_category === category : true))
      .filter((s) => (severity ? s.severity === severity : true))
      .sort(
        (a, b) =>
          SEV_RANK[a.severity] - SEV_RANK[b.severity] || b.confidence_score - a.confidence_score,
      );
  }, [signals, category, severity]);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3 space-y-0">
        <CardTitle>Risk Signals ({filtered.length})</CardTitle>
        <div className="flex gap-2">
          <Select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="h-9 w-auto text-xs"
            aria-label="Filter by category"
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
          <Select
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            className="h-9 w-auto text-xs"
            aria-label="Filter by severity"
          >
            <option value="">All severities</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </div>
      </CardHeader>
      <CardContent>
        {filtered.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No signals match the filters.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="py-2 pr-2 font-medium">Sev</th>
                  <th className="py-2 pr-2 font-medium">Category</th>
                  <th className="py-2 pr-2 font-medium">Signal</th>
                  <th className="py-2 pr-2 font-medium">Source</th>
                  <th className="py-2 pr-2 text-right font-medium">Conf</th>
                  <th className="py-2 text-right font-medium">Wt</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((s) => (
                  <tr
                    key={s.id}
                    onClick={() => setSelected(s)}
                    className="cursor-pointer border-b border-border/50 transition-colors hover:bg-muted/50"
                  >
                    <td className="py-2.5 pr-2">
                      <SeverityDot severity={s.severity} />
                    </td>
                    <td className="py-2.5 pr-2 text-xs text-muted-foreground">{s.risk_category}</td>
                    <td className="max-w-md py-2.5 pr-2">
                      <span className="line-clamp-2">{s.text}</span>
                      {s.is_contradictory && (
                        <span className="ml-1 text-xs text-purple-300">⚠ contradicted</span>
                      )}
                    </td>
                    <td className="py-2.5 pr-2 text-xs text-primary">{domain(s.source_url)}</td>
                    <td className="py-2.5 pr-2 text-right tabular-nums">
                      {(s.confidence_score * 100).toFixed(0)}%
                    </td>
                    <td className="py-2.5 text-right tabular-nums">{s.temporal_weight.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
      <SignalDetail signal={selected} open={selected !== null} onClose={() => setSelected(null)} />
    </Card>
  );
}
