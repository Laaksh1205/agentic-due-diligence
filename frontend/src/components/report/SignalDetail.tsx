import { ExternalLink } from "lucide-react";

import { SeverityBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Dialog } from "@/components/ui/dialog";
import type { RiskSignal } from "@/lib/types";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b border-border/60 py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  );
}

export function SignalDetail({
  signal,
  open,
  onClose,
}: {
  signal: RiskSignal | null;
  open: boolean;
  onClose: () => void;
}) {
  if (!signal) return null;
  return (
    <Dialog open={open} onClose={onClose} className="max-w-xl">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 pr-6">
          <SeverityBadge severity={signal.severity} />
          <Badge className="border-border bg-muted text-muted-foreground">{signal.risk_category}</Badge>
          <Badge className="border-border bg-muted text-muted-foreground">{signal.signal_polarity}</Badge>
          {signal.is_corroborated && (
            <Badge className="border-green-500/30 bg-green-500/10 text-green-300">Corroborated</Badge>
          )}
          {signal.is_contradictory && (
            <Badge className="border-purple-500/30 bg-purple-500/10 text-purple-300">Contradicted</Badge>
          )}
        </div>

        <p className="text-sm leading-relaxed">{signal.text}</p>

        {signal.source_snippet && (
          <blockquote className="rounded-md border-l-2 border-primary/50 bg-background p-3 text-sm italic text-muted-foreground">
            “{signal.source_snippet}”
          </blockquote>
        )}

        <div>
          <Field label="Source type" value={signal.source_type} />
          <Field
            label="Confidence"
            value={`${(signal.confidence_score * 100).toFixed(0)}%`}
          />
          <Field label="Temporal weight" value={signal.temporal_weight.toFixed(2)} />
          <Field label="Data date" value={signal.data_date ?? "—"} />
          <Field
            label="Human verdict"
            value={signal.human_verdict ?? (signal.requires_human_review ? "Pending review" : "—")}
          />
          {signal.corroborating_signals.length > 0 && (
            <Field label="Corroborating signals" value={signal.corroborating_signals.length} />
          )}
        </div>

        {signal.source_url && (
          <a
            href={signal.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            View source
          </a>
        )}
      </div>
    </Dialog>
  );
}
