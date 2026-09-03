import { useEffect, useState } from "react";
import * as admin from "../api/endpoints/admin";
import type { RunEvent } from "../api/endpoints/admin";
import { readSse } from "../api/sse";

const MAX_BACKOFF_MS = 30_000;

// Keeps the live-activity feed bounded across a long-open admin session --
// nothing about GET /api/admin/runs/stream ever tells the client to stop
// listening, so without a cap this would grow for as long as the tab stays
// open.
const MAX_EVENTS = 200;

function isRunEvent(value: unknown): value is RunEvent {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as Record<string, unknown>).id === "string" &&
    typeof (value as Record<string, unknown>).type === "string"
  );
}

export interface UseRunStream {
  events: RunEvent[];
  connected: boolean;
}

/**
 * `GET /api/admin/runs/stream` -- live run activity for the Overview and
 * Traces screens. Mirrors useNotifications' reconnect policy (capped
 * exponential backoff, abort tied to unmount) but there is no backlog to
 * merge in: app/admin/router.py's stream takes no DB session and replays
 * nothing on connect, and it drops (closes, does not queue for) a
 * subscriber that falls too far behind. So unlike useNotifications this
 * hook does not attempt to reconcile a REST snapshot against the stream --
 * it just reports whether the feed is currently live, via `connected`, so
 * a caller with its own snapshot (Overview's counters, Traces' run list)
 * knows when that snapshot might already be stale and needs a fresh read.
 */
export function useRunStream(): UseRunStream {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    let backoffTimer: ReturnType<typeof setTimeout> | null = null;

    function wait(ms: number): Promise<void> {
      return new Promise((resolve) => {
        backoffTimer = setTimeout(resolve, ms);
      });
    }

    async function run() {
      let attempt = 0;
      while (!cancelled) {
        try {
          const response = await admin.runsStream({ signal: controller.signal });
          attempt = 0;
          setConnected(true);
          for await (const frame of readSse(response)) {
            if (cancelled) return;
            if (!isRunEvent(frame)) continue;
            const event = frame;
            setEvents((current) => [event, ...current].slice(0, MAX_EVENTS));
          }
          if (cancelled) return;
        } catch {
          if (cancelled) return;
        }
        // The stream ended -- either the backend dropped a subscriber that
        // fell too far behind (it closes rather than queues, per
        // app/admin/router.py) or the connection just failed. Either way
        // there is no backlog to replay on reconnect, so the honest thing
        // this hook can do is say it is no longer live; a caller with its
        // own snapshot decides whether that is worth a refetch.
        if (cancelled) return;
        setConnected(false);
        const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
        attempt += 1;
        await wait(delay);
      }
    }

    run();

    return () => {
      cancelled = true;
      controller.abort();
      // Otherwise a stray timer from the backoff wait above outlives the
      // component (harmlessly, since `cancelled` guards the loop it would
      // resume -- but for up to 30s of nothing to do).
      if (backoffTimer !== null) clearTimeout(backoffTimer);
    };
  }, []);

  return { events, connected };
}
