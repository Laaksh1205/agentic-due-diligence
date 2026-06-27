"use client";

import { useState } from "react";
import { CheckCircle2, ChevronDown, XCircle } from "lucide-react";

import { SufficiencyBadge } from "@/components/badges";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DueDiligenceReport } from "@/lib/types";
import { cn, SUFFICIENCY_HELP } from "@/lib/utils";

export function SourcesPanel({ report }: { report: DueDiligenceReport }) {
  const [open, setOpen] = useState(false);
  const consulted = report.sources_consulted;
  const failed = report.sources_failed;

  return (
    <Card>
      <CardHeader
        className="cursor-pointer select-none flex-row items-center justify-between space-y-0"
        onClick={() => setOpen((o) => !o)}
      >
        <CardTitle className="text-base">
          Sources &amp; Data Transparency
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            {consulted.length} consulted · {failed.length} failed
          </span>
        </CardTitle>
        <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} />
      </CardHeader>
      {open && (
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2 rounded-md border border-border bg-background p-3 text-sm">
            <SufficiencyBadge value={report.data_sufficiency} />
            <span className="text-muted-foreground">{SUFFICIENCY_HELP[report.data_sufficiency]}</span>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Consulted
              </h4>
              <ul className="space-y-1.5">
                {consulted.length === 0 && <li className="text-sm text-muted-foreground">—</li>}
                {consulted.map((s, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm">
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-green-400" />
                    <span>{s.name || s.url || s.source_type}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Failed
              </h4>
              <ul className="space-y-1.5">
                {failed.length === 0 && <li className="text-sm text-muted-foreground">—</li>}
                {failed.map((s, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <XCircle className="h-4 w-4 shrink-0 text-red-400" />
                    <span>
                      {s.name || s.url || s.source_type}
                      {s.error && <span className="block text-xs text-muted-foreground">{s.error}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  );
}
