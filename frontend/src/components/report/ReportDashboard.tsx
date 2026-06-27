import { AlertTriangle, ArrowRight } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DueDiligenceReport, ReportAction } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ExportButtons } from "./ExportButtons";
import { ReportOverview } from "./ReportOverview";
import { SignalsTable } from "./SignalsTable";
import { SourcesPanel } from "./SourcesPanel";
import { StrengthsSection } from "./StrengthsSection";

const PRIORITY_COLOR: Record<ReportAction["priority"], string> = {
  IMMEDIATE: "text-red-400",
  SHORT_TERM: "text-orange-400",
  MONITOR: "text-blue-400",
};

function ActionsSection({ actions }: { actions: ReportAction[] }) {
  if (actions.length === 0) return null;
  const order = { IMMEDIATE: 0, SHORT_TERM: 1, MONITOR: 2 };
  const sorted = [...actions].sort((a, b) => order[a.priority] - order[b.priority]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-400" />
          Recommended Actions ({actions.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {sorted.map((a, i) => (
          <div key={i} className="flex items-start gap-3 rounded-md border border-border bg-background p-3 text-sm">
            <ArrowRight className={cn("mt-0.5 h-4 w-4 shrink-0", PRIORITY_COLOR[a.priority])} />
            <div>
              <span className={cn("mr-2 text-xs font-semibold uppercase", PRIORITY_COLOR[a.priority])}>
                {a.priority.replace("_", " ")}
              </span>
              <span>{a.description}</span>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export function ReportDashboard({
  runId,
  report,
}: {
  runId: string;
  report: DueDiligenceReport;
}) {
  // Risk signals exclude positives (those live in the strengths section).
  const riskSignals = report.risk_signals.filter((s) => s.signal_polarity !== "POSITIVE");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">Assessment complete</span>
        <ExportButtons runId={runId} />
      </div>

      <ReportOverview report={report} />
      <ActionsSection actions={report.recommended_actions} />
      <SignalsTable signals={riskSignals} />
      <StrengthsSection signals={report.positive_signals} summary={report.strengths_section} />
      <SourcesPanel report={report} />
    </div>
  );
}
