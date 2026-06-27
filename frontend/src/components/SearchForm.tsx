"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { startAssessment } from "@/lib/api";
import type { Scope } from "@/lib/types";

const SCOPES: { value: Scope; label: string; help: string }[] = [
  { value: "full", label: "Full", help: "All risk categories — the complete assessment." },
  { value: "financial", label: "Financial", help: "Focus on financial-health and solvency risks." },
  { value: "compliance", label: "Compliance", help: "Focus on regulatory, legal, and sanctions risks." },
];

export function SearchForm() {
  const router = useRouter();
  const [company, setCompany] = useState("");
  const [scope, setScope] = useState<Scope>("full");
  const [autoMode, setAutoMode] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (company.trim().length === 0) return;
    setConfirmOpen(true); // entity confirmation step (4.2.3)
  };

  const launch = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const { run_id } = await startAssessment({
        company_name: company.trim(),
        scope,
        auto_mode: autoMode,
      });
      router.push(`/runs/${run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start assessment");
      setSubmitting(false);
      setConfirmOpen(false);
    }
  };

  const activeScope = SCOPES.find((s) => s.value === scope)!;

  return (
    <>
      <form onSubmit={onSubmit} className="space-y-5">
        <div className="space-y-2">
          <label htmlFor="company" className="text-sm font-medium">
            Company name
          </label>
          <div className="flex gap-2">
            <Input
              id="company"
              placeholder="e.g. Boeing, Stripe, Revolut"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              autoFocus
            />
            <Button type="submit" size="lg" disabled={company.trim().length === 0}>
              <Search className="h-4 w-4" />
              Assess
            </Button>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <label htmlFor="scope" className="text-sm font-medium">
              Evaluation scope
            </label>
            <Select id="scope" value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
              {SCOPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </Select>
            <p className="text-xs text-muted-foreground">{activeScope.help}</p>
          </div>

          <div className="space-y-2">
            <span className="text-sm font-medium">Review mode</span>
            <label className="flex h-11 cursor-pointer items-center gap-3 rounded-md border border-input bg-background px-3 text-sm">
              <input
                type="checkbox"
                checked={autoMode}
                onChange={(e) => setAutoMode(e.target.checked)}
                className="h-4 w-4 accent-[hsl(var(--primary))]"
              />
              Auto mode — skip human review of critical signals
            </label>
            <p className="text-xs text-muted-foreground">
              {autoMode
                ? "Critical findings are flagged as pending, not confirmed."
                : "You'll review critical signals in the browser before the report is written."}
            </p>
          </div>
        </div>

        {error && (
          <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {error}
          </p>
        )}
      </form>

      <Dialog open={confirmOpen} onClose={() => !submitting && setConfirmOpen(false)}>
        <h2 className="text-lg font-semibold">Confirm assessment</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The pipeline will resolve this name to a legal entity (canonical name, jurisdiction,
          public/private) as its first step, then research it across all sources.
        </p>
        <dl className="mt-4 space-y-2 rounded-md border border-border bg-background p-4 text-sm">
          <div className="flex justify-between">
            <dt className="text-muted-foreground">Company</dt>
            <dd className="font-medium">{company}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">Scope</dt>
            <dd className="font-medium capitalize">{scope}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">Human review</dt>
            <dd className="font-medium">{autoMode ? "Skipped (auto)" : "Enabled"}</dd>
          </div>
        </dl>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={launch} disabled={submitting}>
            {submitting && <Spinner />}
            Start assessment
          </Button>
        </div>
      </Dialog>
    </>
  );
}
