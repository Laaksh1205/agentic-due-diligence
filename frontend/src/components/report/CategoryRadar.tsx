"use client";

import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from "recharts";

import type { RiskCategory } from "@/lib/types";

const ALL_CATEGORIES: RiskCategory[] = [
  "FINANCIAL",
  "LEGAL",
  "REGULATORY",
  "REPUTATIONAL",
  "OPERATIONAL",
  "CYBERSECURITY",
  "ESG",
];

export function CategoryRadar({
  scores,
}: {
  scores: Partial<Record<RiskCategory, number>>;
}) {
  const data = ALL_CATEGORIES.map((cat) => ({
    category: cat.charAt(0) + cat.slice(1).toLowerCase(),
    score: Math.round((scores[cat] ?? 0) * 10) / 10,
  }));

  return (
    <ResponsiveContainer width="100%" height={280}>
      <RadarChart data={data} outerRadius="72%">
        <PolarGrid stroke="hsl(var(--border))" />
        <PolarAngleAxis
          dataKey="category"
          tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
        />
        <PolarRadiusAxis domain={[0, 10]} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }} />
        <Radar
          name="Risk score"
          dataKey="score"
          stroke="hsl(var(--primary))"
          fill="hsl(var(--primary))"
          fillOpacity={0.4}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
