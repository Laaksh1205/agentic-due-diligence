import { Badge } from "@/components/ui/badge";
import type { DataSufficiency, Severity } from "@/lib/types";
import { SEVERITY_COLOR, SUFFICIENCY_COLOR } from "@/lib/utils";

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge className={SEVERITY_COLOR[severity]?.bg}>{severity}</Badge>;
}

export function SeverityDot({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${SEVERITY_COLOR[severity]?.dot}`}
      title={severity}
    />
  );
}

export function SufficiencyBadge({ value }: { value: DataSufficiency }) {
  return <Badge className={SUFFICIENCY_COLOR[value]}>{value}</Badge>;
}
