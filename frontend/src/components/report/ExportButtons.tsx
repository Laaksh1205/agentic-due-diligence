import { FileDown, FileJson } from "lucide-react";

import { Button } from "@/components/ui/button";
import { exportUrl } from "@/lib/api";

export function ExportButtons({ runId }: { runId: string }) {
  return (
    <div className="flex gap-2">
      <a href={exportUrl(runId, "pdf")} target="_blank" rel="noreferrer">
        <Button variant="outline" size="sm">
          <FileDown className="h-4 w-4" /> PDF
        </Button>
      </a>
      <a href={exportUrl(runId, "json")} target="_blank" rel="noreferrer">
        <Button variant="outline" size="sm">
          <FileJson className="h-4 w-4" /> JSON
        </Button>
      </a>
    </div>
  );
}
