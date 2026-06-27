"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronRight, Inbox } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { getRunHistory } from "@/lib/api";
import type { RunHistoryItem } from "@/lib/types";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function statusBadge(status: string) {
  if (status === "complete") return "border-green-500/30 bg-green-500/10 text-green-300";
  if (status === "error") return "border-red-500/30 bg-red-500/10 text-red-300";
  return "border-primary/30 bg-primary/10 text-primary";
}

export default function RunsHistoryPage() {
  const [runs, setRuns] = useState<RunHistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRunHistory()
      .then(setRuns)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load history"));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Assessment History</h1>
        <p className="text-sm text-muted-foreground">Past and in-flight due-diligence runs.</p>
      </div>

      {error && (
        <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      )}

      {runs === null && !error ? (
        <div className="flex items-center justify-center gap-2 py-24 text-muted-foreground">
          <Spinner /> Loading history…
        </div>
      ) : runs && runs.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center text-muted-foreground">
            <Inbox className="h-8 w-8" />
            <p>No assessments yet.</p>
            <Link href="/" className="text-primary hover:underline">
              Start your first assessment →
            </Link>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{runs?.length} run(s)</CardTitle>
          </CardHeader>
          <CardContent className="divide-y divide-border p-0">
            {runs?.map((r) => (
              <Link
                key={r.run_id}
                href={`/runs/${r.run_id}`}
                className="flex items-center justify-between gap-4 px-5 py-3 transition-colors hover:bg-muted/40"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium">{r.entity_name}</div>
                  <div className="text-xs text-muted-foreground">{formatDate(r.created_at)}</div>
                </div>
                <div className="flex items-center gap-3">
                  <Badge className={statusBadge(r.status)}>{r.status}</Badge>
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
