import { RunClient } from "./RunClient";

// Under static export (`output: 'export'`) a dynamic route must enumerate its
// params at build time. Run ids are runtime UUIDs, so we export one placeholder
// shell; the FastAPI backend serves it for every /runs/<id> path and the client
// reads the real id from the URL (see RunClient → useParams). This server
// component exists only to host generateStaticParams (not allowed in a
// "use client" page); all interactivity lives in RunClient.
export function generateStaticParams() {
  return [{ runId: "live" }];
}

export default function RunPage() {
  return <RunClient />;
}
