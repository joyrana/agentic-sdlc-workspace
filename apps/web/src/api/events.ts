import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { tokenStore } from "./client";

export type EventEnvelope = {
  event_id: string;
  seq: number;
  type: string;
  project_id: string;
  run_id: string | null;
  ts: string;
  payload: Record<string, unknown>;
  artifact_refs: { type: string; id: string; version?: number | null }[];
};

/**
 * Subscribe to the project's server-sent event stream.
 *
 * Uses fetch streaming (EventSource cannot send Authorization headers). Reconnects with backoff and
 * resumes from the last seen `seq` via Last-Event-ID; duplicates are dropped by `event_id`.
 * Events only trigger refetches — the backend stays the source of truth.
 */
export function useProjectEvents(projectId: string | undefined, onEvent?: (e: EventEnvelope) => void) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<"connecting" | "live" | "reconnecting" | "closed">("connecting");
  const lastSeq = useRef(0);
  const seen = useRef(new Set<string>());
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    let attempt = 0;

    async function connect(): Promise<void> {
      while (!controller.signal.aborted) {
        try {
          const token = tokenStore.get();
          const res = await fetch(`/api/v1/projects/${projectId}/events/stream?after=${lastSeq.current}`, {
            headers: {
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
              ...(lastSeq.current ? { "Last-Event-ID": String(lastSeq.current) } : {}),
            },
            signal: controller.signal,
          });
          if (res.status === 401 || res.status === 404) {
            setStatus("closed");
            return;
          }
          if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
          setStatus("live");
          attempt = 0;
          const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
          let buf = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += value;
            let idx: number;
            while ((idx = buf.indexOf("\n\n")) >= 0) {
              const chunk = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              const data = chunk
                .split("\n")
                .filter((l) => l.startsWith("data: "))
                .map((l) => l.slice(6))
                .join("\n");
              if (!data) continue;
              const ev = JSON.parse(data) as EventEnvelope;
              lastSeq.current = Math.max(lastSeq.current, ev.seq);
              if (seen.current.has(ev.event_id)) continue;
              seen.current.add(ev.event_id);
              handler.current?.(ev);
              void qc.invalidateQueries({ queryKey: ["project", projectId] });
            }
          }
        } catch (err) {
          if (controller.signal.aborted) return;
          void err;
        }
        setStatus("reconnecting");
        attempt += 1;
        await new Promise((r) => setTimeout(r, Math.min(15000, 1000 * 2 ** attempt)));
      }
    }
    void connect();
    return () => {
      controller.abort();
      setStatus("closed");
    };
  }, [projectId, qc]);

  return status;
}
