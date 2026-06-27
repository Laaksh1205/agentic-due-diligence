import { Building2, Globe, Hash } from "lucide-react";

import { SufficiencyBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DueDiligenceReport } from "@/lib/types";
import { cn, SUFFICIENCY_HELP } from "@/lib/utils";
import { CategoryRadar } from "./CategoryRadar";

function riskColor(score: number): string {
  if (score >= 7) return "text-red-400";
  if (score >= 4) return "text-amber-400";
  return "text-green-400";
}

export function ReportOverview({ report }: { report: DueDiligenceReport }) {
  const entity = report.target_entity;
  const isThin = report.data_sufficiency === "LIMITED" || report.data_sufficiency === "SPARSE";

  return (
    <div className="space-y-6">
      {/* Entity header */}
      <Card>
        <CardContent className="flex flex-wrap items-start justify-between gap-4 pt-6">
          <div className="space-y-2">
            <h1 className="text-2xl font-bold">{entity.canonical_name}</h1>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <Badge className="border-border bg-muted text-foreground">
                {entity.is_public ? "Public" : "Private"}
              </Badge>
              {entity.jurisdiction && (
                <span className="inline-flex items-center gap-1">
                  <Globe className="h-3.5 w-3.5" /> {entity.jurisdiction}
                </span>
              )}
              {entity.industry && (
                <span className="inline-flex items-center gap-1">
                  <Building2 className="h-3.5 w-3.5" /> {entity.industry}
                </span>
              )}
              {entity.sec_cik && (
                <span className="inline-flex items-center gap-1">
                  <Hash className="h-3.5 w-3.5" /> CIK {entity.sec_cik}
                </span>
              )}
            </div>
            {entity.aliases.length > 0 && (
              <p className="text-xs text-muted-foreground">
                Also known as: {entity.aliases.join(", ")}
              </p>
            )}
          </div>
          <div className="text-right">
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Overall risk</div>
            <div className={cn("text-4xl font-bold", riskColor(report.overall_risk_score))}>
              {report.overall_risk_score.toFixed(1)}
              <span className="text-lg text-muted-foreground">/10</span>
            </div>
            <div className="mt-1">
              <SufficiencyBadge value={report.data_sufficiency} />
            </div>
          </div>
        </CardContent>
      </Card>

      {isThin && (
        <div className="rounded-lg border border-orange-500/30 bg-orange-500/10 px-4 py-3 text-sm text-orange-200">
          <strong>Limited data.</strong> {SUFFICIENCY_HELP[report.data_sufficiency]} Findings may not
          be comprehensive — manual investigation is recommended.
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        {/* Executive summary */}
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Executive Summary</CardTitle>
          </CardHeader>
          <CardContent className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
            {report.executive_summary || "No summary available."}
          </CardContent>
        </Card>

        {/* Category radar */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Category Risk Scores</CardTitle>
          </CardHeader>
          <CardContent>
            {Object.keys(report.category_scores).length > 0 ? (
              <CategoryRadar scores={report.category_scores} />
            ) : (
              <p className="py-12 text-center text-sm text-muted-foreground">No scored categories.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
