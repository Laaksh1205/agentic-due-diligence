"use client";

import { useEffect, useRef, useState } from "react";

import { getRunStatus, wsUrl } from "./api";
import type { RunStatusPayload } from "./types";
import { isTerminal } from "./utils";

/**
 * Subscribe to live run status (4.2.4). Prefers the WebSocket stream and falls
 * back to polling if the socket fails to open. Stops once the run is terminal.
 */
export function useRunStatus(runId: string) {
  const [status, setStatus] = useState<RunStatusPayload | null>(null);
  const [connected, setConnected] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!runId) return; // id is read from the URL after mount; wait for it
    let cancelled = false;
    let ws: WebSocket | null = null;

    const stopPolling = () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };

    const startPolling = () => {
      if (pollRef.current) return;
      const tick = async () => {
        try {
          const s = await getRunStatus(runId);
          if (cancelled) return;
          setStatus(s);
          if (isTerminal(s.status)) stopPolling();
        } catch {
          /* keep polling */
        }
      };
      void tick();
      pollRef.current = setInterval(tick, 1500);
    };

    // Seed immediately so the page never shows an empty state.
    void getRunStatus(runId)
      .then((s) => !cancelled && setStatus(s))
      .catch(() => {});

    try {
      ws = new WebSocket(wsUrl(runId));
      ws.onopen = () => !cancelled && setConnected(true);
      ws.onmessage = (ev) => {
        if (cancelled) return;
        try {
          const payload = JSON.parse(ev.data) as RunStatusPayload;
          if ("status" in payload) setStatus(payload);
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onerror = () => startPolling();
      ws.onclose = () => {
        if (cancelled) return;
        setConnected(false);
        // If the run isn't finished, fall back to polling.
        setStatus((cur) => {
          if (!cur || !isTerminal(cur.status)) startPolling();
          return cur;
        });
      };
    } catch {
      startPolling();
    }

    return () => {
      cancelled = true;
      stopPolling();
      ws?.close();
    };
  }, [runId]);

  return { status, connected };
}
