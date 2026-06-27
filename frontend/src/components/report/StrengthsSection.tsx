"use client";

import { useState } from "react";
import { ThumbsUp } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RiskSignal } from "@/lib/types";
import { SignalDetail } from "./SignalDetail";

export function StrengthsSection({
  signals,
  summary,
}: {
  signals: RiskSignal[];
  summary?: string;
}) {
  const [selected, setSelected] = useState<RiskSignal | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ThumbsUp className="h-4 w-4 text-green-400" />
          Strengths &amp; Positive Indicators ({signals.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {summary && <p className="text-sm text-muted-foreground">{summary}</p>}
        {signals.length === 0 ? (
          <p className="text-sm text-muted-foreground">No positive signals identified.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {signals.map((s) => (
              <button
                key={s.id}
                onClick={() => setSelected(s)}
                className="rounded-md border border-green-500/20 bg-green-500/5 p-3 text-left text-sm transition-colors hover:bg-green-500/10"
              >
                <span className="line-clamp-3">{s.text}</span>
              </button>
            ))}
          </div>
        )}
      </CardContent>
      <SignalDetail signal={selected} open={selected !== null} onClose={() => setSelected(null)} />
    </Card>
  );
}
