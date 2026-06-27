import { Check, Loader2 } from "lucide-react";

import type { RunStatusPayload } from "@/lib/types";
import { activeStepIndex, cn, PIPELINE_STEPS } from "@/lib/utils";

export function ProgressPipeline({ status }: { status: RunStatusPayload }) {
  const active = activeStepIndex(status.status);
  const isComplete = status.status === "complete";

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-2 flex items-center justify-between text-sm">
          <span className="text-muted-foreground">{status.current_agent || "Starting…"}</span>
          <span className="font-medium">{status.progress_pct}%</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all duration-500"
            style={{ width: `${status.progress_pct}%` }}
          />
        </div>
      </div>

      <ol className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {PIPELINE_STEPS.map((step, i) => {
          const done = isComplete || i < active;
          const current = !isComplete && i === active;
          return (
            <li
              key={step.key}
              className={cn(
                "flex flex-col items-center gap-2 rounded-lg border p-3 text-center transition-colors",
                done && "border-green-500/40 bg-green-500/10",
                current && "border-primary/50 bg-primary/10",
                !done && !current && "border-border bg-card",
              )}
            >
              <span
                className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-full border text-xs font-semibold",
                  done && "border-green-500/50 bg-green-500/20 text-green-300",
                  current && "border-primary/50 bg-primary/20 text-primary",
                  !done && !current && "border-border text-muted-foreground",
                )}
              >
                {done ? (
                  <Check className="h-4 w-4" />
                ) : current ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  i + 1
                )}
              </span>
              <span className={cn("text-xs", current ? "font-medium" : "text-muted-foreground")}>
                {step.label}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
