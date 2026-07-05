import { FileSearch, ShieldAlert, Workflow } from "lucide-react";

import { SearchForm } from "@/components/SearchForm";
import { Card, CardContent } from "@/components/ui/card";

const FEATURES = [
  {
    icon: FileSearch,
    title: "Multi-source research",
    body: "Companies House, SEC EDGAR, global registries, and web — gathered concurrently via MCP.",
  },
  {
    icon: ShieldAlert,
    title: "Citation-verified signals",
    body: "Every risk is anchored to a verbatim source quote and fuzzy-verified against the source. Unverifiable quotes are rejected; lone low-trust findings are capped and flagged for review.",
  },
  {
    icon: Workflow,
    title: "Human-in-the-loop",
    body: "Critical findings pause for your review in the browser before synthesis.",
  },
];

export default function HomePage() {
  return (
    <div className="space-y-10">
      <section className="space-y-3 text-center">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          Due diligence in minutes, not weeks
        </h1>
        <p className="mx-auto max-w-2xl text-muted-foreground">
          Enter a company and the agentic pipeline researches it across global sources, extracts
          grounded risk signals, and synthesizes a citation-verified report.
        </p>
      </section>

      <Card className="mx-auto max-w-3xl">
        <CardContent className="pt-6">
          <SearchForm />
        </CardContent>
      </Card>

      <section className="grid gap-4 sm:grid-cols-3">
        {FEATURES.map((f) => (
          <Card key={f.title}>
            <CardContent className="space-y-2 pt-6">
              <f.icon className="h-6 w-6 text-primary" />
              <h3 className="font-semibold">{f.title}</h3>
              <p className="text-sm text-muted-foreground">{f.body}</p>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
  );
}
