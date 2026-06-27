"use client";

import { useState } from "react";
import { Check, ExternalLink, Search, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { submitVerdict } from "@/lib/api";
import type { ReviewSignal, Verdict } from "@/lib/types";
import { SEVERITY_COLOR } from "@/lib/utils";

/**
 * Browser HITL review (4.2.9). One card per critical signal with
 * Confirm / Dismiss / Investigate. Each verdict is POSTed; the pipeline resumes
 * once every signal has a verdict (or the backend timeout fires).
 */
export function HitlReview({ runId, signals }: { runId: string; signals: ReviewSignal[] }) {
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decide = async (signalId: string, verdict: Verdict) => {
    setPending(signalId);
    setError(null);
    try {
      await submitVerdict(runId, signalId, verdict);
      setVerdicts((v) => ({ ...v, [signalId]: verdict }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit verdict");
    } finally {
      setPending(null);
    }
  };

  const remaining = signals.filter((s) => !verdicts[s.id]).length;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
        <strong>Human review required.</strong> {signals.length} critical or low-confidence signal
        {signals.length === 1 ? "" : "s"} need a verdict before the report is written.
        {remaining > 0 ? ` ${remaining} remaining.` : " Resuming…"}
      </div>

      {error && (
        <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      )}

      {signals.map((sig) => {
        const verdict = verdicts[sig.id];
        const sevColor = SEVERITY_COLOR[sig.severity as keyof typeof SEVERITY_COLOR]?.bg;
        return (
          <Card key={sig.id} className={verdict ? "opacity-60" : undefined}>
            <CardContent className="space-y-3 pt-5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge className={sevColor}>{sig.severity}</Badge>
                <Badge className="border-border bg-muted text-muted-foreground">{sig.category}</Badge>
                {verdict && (
                  <Badge className="border-primary/30 bg-primary/10 text-primary">
                    Verdict: {verdict}
                  </Badge>
                )}
              </div>

              <p className="text-sm">{sig.text}</p>

              {sig.source_snippet && (
                <blockquote className="border-l-2 border-border pl-3 text-sm italic text-muted-foreground">
                  “{sig.source_snippet}”
                </blockquote>
              )}

              {sig.source_url && (
                <a
                  href={sig.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                >
                  <ExternalLink className="h-3 w-3" />
                  {sig.source_url}
                </a>
              )}

              {!verdict && (
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button
                    size="sm"
                    variant="success"
                    disabled={pending === sig.id}
                    onClick={() => decide(sig.id, "confirm")}
                  >
                    <Check className="h-4 w-4" /> Confirm
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={pending === sig.id}
                    onClick={() => decide(sig.id, "dismiss")}
                  >
                    <X className="h-4 w-4" /> Dismiss
                  </Button>
                  <Button
                    size="sm"
                    variant="warning"
                    disabled={pending === sig.id}
                    onClick={() => decide(sig.id, "investigate")}
                  >
                    <Search className="h-4 w-4" /> Investigate
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
