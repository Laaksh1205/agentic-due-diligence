"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { resolveCandidates, startAssessment } from "@/lib/api";
import type { Candidate, Scope } from "@/lib/types";

const SCOPES: { value: Scope; label: string; help: string }[] = [
  { value: "full", label: "Full", help: "All risk categories — the complete assessment." },
  { value: "financial", label: "Financial", help: "Focus on financial-health and solvency risks." },
  { value: "compliance", label: "Compliance", help: "Focus on regulatory, legal, and sanctions risks." },
];

// Optional jurisdiction filter — maps to the Registry Lookup `jurisdiction_code`.
const COUNTRIES: { value: string; label: string }[] = [
  { value: "", label: "Any country" },
  { value: "us", label: "United States" },
  { value: "gb", label: "United Kingdom" },
  { value: "ca", label: "Canada" },
  { value: "au", label: "Australia" },
  { value: "de", label: "Germany" },
  { value: "fr", label: "France" },
  { value: "in", label: "India" },
  { value: "jp", label: "Japan" },
  { value: "sg", label: "Singapore" },
];

const AS_ENTERED = "__as_entered__";

export function SearchForm() {
  const router = useRouter();
  const [company, setCompany] = useState("");
  const [country, setCountry] = useState("");
  const [scope, setScope] = useState<Scope>("full");
  const [autoMode, setAutoMode] = useState(false);

  const [pickerOpen, setPickerOpen] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [chosen, setChosen] = useState<string>(AS_ENTERED); // registry_id or AS_ENTERED
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (company.trim().length === 0) return;
    // Step 1 of §8c: resolve candidates, then let the user pick before research.
    setPickerOpen(true);
    setResolving(true);
    setCandidates([]);
    setChosen(AS_ENTERED);
    try {
      const found = await resolveCandidates(company.trim(), country);
      setCandidates(found);
      // Default to the top match when we have one — keeps the happy path one click.
      if (found.length > 0 && found[0].registry_id) setChosen(found[0].registry_id);
    } catch {
      setCandidates([]); // graceful — fall back to name-based research
    } finally {
      setResolving(false);
    }
  };

  const launch = async () => {
    setSubmitting(true);
    setError(null);
    const picked =
      chosen === AS_ENTERED ? null : candidates.find((c) => c.registry_id === chosen) ?? null;
    try {
      const { run_id } = await startAssessment({
        company_name: picked ? picked.name : company.trim(),
        scope,
        auto_mode: autoMode,
        registry_id: picked?.registry_id || "",
        jurisdiction: picked?.jurisdiction || country || "",
      });
      router.push(`/runs/${run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start assessment");
      setSubmitting(false);
      setPickerOpen(false);
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

        <div className="grid gap-4 sm:grid-cols-3">
          <div className="space-y-2">
            <label htmlFor="country" className="text-sm font-medium">
              Country <span className="text-muted-foreground">(optional)</span>
            </label>
            <Select id="country" value={country} onChange={(e) => setCountry(e.target.value)}>
              {COUNTRIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
            <p className="text-xs text-muted-foreground">Narrows registry matches.</p>
          </div>

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
              Auto
            </label>
            <p className="text-xs text-muted-foreground">
              {autoMode ? "Skip human review." : "Review critical signals in-browser."}
            </p>
          </div>
        </div>

        {error && (
          <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {error}
          </p>
        )}
      </form>

      <Dialog open={pickerOpen} onClose={() => !submitting && setPickerOpen(false)}>
        <h2 className="text-lg font-semibold">Confirm assessment</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Select the legal entity to assess. The pipeline then researches it across all sources.
        </p>

        {resolving ? (
          <div className="mt-6 flex items-center justify-center gap-2 py-8 text-muted-foreground">
            <Spinner /> Finding registry matches…
          </div>
        ) : (
          <div className="mt-4 max-h-72 space-y-2 overflow-y-auto">
            {candidates.length === 0 && (
              <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
                No registry matches found — proceeding with name-based research for “{company.trim()}”.
              </p>
            )}
            {candidates.map((c) => (
              <label
                key={c.registry_id || c.name}
                className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm ${
                  chosen === c.registry_id ? "border-[hsl(var(--primary))] bg-primary/5" : "border-border"
                }`}
              >
                <input
                  type="radio"
                  name="candidate"
                  className="mt-1 h-4 w-4 accent-[hsl(var(--primary))]"
                  checked={chosen === c.registry_id}
                  onChange={() => setChosen(c.registry_id)}
                />
                <span className="min-w-0">
                  <span className="font-medium">{c.name}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {[
                      c.jurisdiction && c.jurisdiction.toUpperCase(),
                      c.status,
                      c.company_type,
                      c.is_public ? "Public" : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                    {c.address ? ` — ${c.address}` : ""}
                  </span>
                </span>
              </label>
            ))}

            {/* Always allow falling back to the raw name (autonomous resolution). */}
            <label
              className={`flex cursor-pointer items-center gap-3 rounded-md border p-3 text-sm ${
                chosen === AS_ENTERED ? "border-[hsl(var(--primary))] bg-primary/5" : "border-border"
              }`}
            >
              <input
                type="radio"
                name="candidate"
                className="h-4 w-4 accent-[hsl(var(--primary))]"
                checked={chosen === AS_ENTERED}
                onChange={() => setChosen(AS_ENTERED)}
              />
              <span>
                Use “{company.trim()}” as entered
                <span className="block text-xs text-muted-foreground">
                  Let the system pick the best match automatically.
                </span>
              </span>
            </label>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={() => setPickerOpen(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={launch} disabled={submitting || resolving}>
            {submitting && <Spinner />}
            Start assessment
          </Button>
        </div>
      </Dialog>
    </>
  );
}
