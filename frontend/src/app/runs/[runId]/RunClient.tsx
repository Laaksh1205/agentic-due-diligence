"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowLeft } from "lucide-react";

import { HitlReview } from "@/components/HitlReview";
import { ProgressPipeline } from "@/components/ProgressPipeline";
import { ReportDashboard } from "@/components/report/ReportDashboard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { getReport } from "@/lib/api";
import type { DueDiligenceReport } from "@/lib/types";
import { useRunStatus } from "@/lib/useRunStatus";

export function RunClient() {
  // Read the run id straight from the URL path. Under static export
  // (`output: 'export'`) the single exported shell is built for the placeholder
  // id "live" and served by the backend for every /runs/<id> path — so
  // `useParams()` returns the build-time "live", not the real id. Parsing
  // window.location.pathname is the only reliable source of the actual run id.
  const [runId] = useState(() => {
    if (typeof window === "undefined") return "";
    const m = window.location.pathname.match(/\/runs\/([^/]+)\/?$/);
    return m ? decodeURIComponent(m[1]) : "";
  });
  const { status, connected } = useRunStatus(runId);
  const [report, setReport] = useState<DueDiligenceReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);

  // Fetch the report once the run completes.
  useEffect(() => {
    if (status?.status === "complete" && !report) {
      getReport(runId)
        .then(setReport)
        .catch((e) => setReportError(e instanceof Error ? e.message : "Failed to load report"));
    }
  }, [status?.status, runId, report]);

  if (!status) {
    return (
      <div className="flex items-center justify-center gap-2 py-24 text-muted-foreground">
        <Spinner /> Loading run…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Link href="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> New assessment
        </Link>
        <span className="text-xs text-muted-foreground">
          {connected ? "● live" : "○ polling"} · run {runId.slice(0, 8)}
        </span>
      </div>

      {status.status === "error" ? (
        <Card className="border-red-500/30">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-red-300">
              <AlertCircle className="h-5 w-5" /> Assessment failed
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {status.error || "The pipeline encountered an unrecoverable error."}
            </p>
            <Link href="/">
              <Button variant="outline">Try another company</Button>
            </Link>
          </CardContent>
        </Card>
      ) : status.status === "complete" ? (
        report ? (
          <ReportDashboard runId={runId} report={report} />
        ) : reportError ? (
          <Card className="border-red-500/30">
            <CardContent className="pt-6 text-sm text-red-300">{reportError}</CardContent>
          </Card>
        ) : (
          <div className="flex items-center justify-center gap-2 py-24 text-muted-foreground">
            <Spinner /> Loading report…
          </div>
        )
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Assessing {status.company_name}</CardTitle>
            </CardHeader>
            <CardContent>
              <ProgressPipeline status={status} />
            </CardContent>
          </Card>

          {status.status === "reviewing" && status.review_signals.length > 0 && (
            <HitlReview runId={runId} signals={status.review_signals} />
          )}
        </>
      )}
    </div>
  );
}
