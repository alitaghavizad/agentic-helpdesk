import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import * as notifications from "../api/endpoints/notifications";
import type { Notification, NotificationStreamEvent } from "../api/endpoints/notifications";
import { readSse } from "../api/sse";

const QUERY_KEY = ["notifications"] as const;
const MAX_BACKOFF_MS = 30_000;

function isStreamEvent(value: unknown): value is NotificationStreamEvent {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as Record<string, unknown>).id === "string" &&
    typeof (value as Record<string, unknown>).title === "string"
  );
}

function fromStreamEvent(event: NotificationStreamEvent): Notification {
  // Everything the stream emits -- replayed backlog or live -- is unread by
  // construction (app/notifications/router.py only ever streams unread rows).
  return {
    id: event.id,
    type: event.type,
    title: event.title,
    body: event.body,
    link_type: event.link_type,
    link_id: event.link_id,
    read: false,
  };
}

export interface UseNotifications {
  items: Notification[];
  unread: number;
  markRead: (id: string) => Promise<void>;
}

/**
 * `GET /api/notifications` for the backlog (TanStack Query), plus a live SSE
 * subscription that prepends anything arriving after that snapshot.
 *
 * The backend deliberately subscribes to its broker BEFORE reading the
 * backlog (spec 10 / app/notifications/router.py), so a notification
 * committed in between is never lost -- but can arrive twice, once replayed
 * on the stream and once in the backlog fetched moments later. Collapsing
 * that overlap by id is this hook's job, not the backend's.
 */
export function useNotifications(): UseNotifications {
  const { principal } = useAuth();
  const queryClient = useQueryClient();
  const [streamed, setStreamed] = useState<Notification[]>([]);
  const isUser = principal?.kind === "user";

  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: notifications.list,
    enabled: isUser,
  });

  const backlog = query.data ?? [];

  // A ref, not state: the stream effect below reads the latest backlog ids
  // on every frame without needing the backlog itself in its dependency
  // array (which would tear the connection down and reopen it on every
  // backlog refetch).
  const backlogIds = useRef<Set<string>>(new Set());
  backlogIds.current = new Set(backlog.map((item) => item.id));

  // Once the backlog catches up with a notification this hook already
  // streamed in, drop the streamed copy so the id renders exactly once.
  useEffect(() => {
    setStreamed((current) => current.filter((item) => !backlogIds.current.has(item.id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data]);

  useEffect(() => {
    // Guests 403 here: notifications.user_id is NOT NULL and a guest is not
    // a row in `users` (app/notifications/router.py's _require_user). Opening
    // the stream anyway would just reconnect forever against that wall.
    if (!isUser) return;

    const controller = new AbortController();
    let cancelled = false;

    async function run() {
      let attempt = 0;
      while (!cancelled) {
        try {
          const response = await notifications.stream({ signal: controller.signal });
          attempt = 0;
          for await (const frame of readSse(response)) {
            if (cancelled) return;
            if (!isStreamEvent(frame)) continue;
            const event = frame;
            setStreamed((current) => {
              if (backlogIds.current.has(event.id) || current.some((item) => item.id === event.id)) {
                return current;
              }
              return [fromStreamEvent(event), ...current];
            });
          }
          if (cancelled) return;
        } catch {
          if (cancelled) return;
        }
        // The stream ended -- either the backend dropped a subscriber that
        // fell too far behind (it closes rather than queues, per
        // app/notifications/router.py) or the connection just failed.
        // Reconnect with capped exponential backoff; the next connect's
        // backlog replay re-delivers whatever this one missed.
        const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
        attempt += 1;
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }

    run();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [isUser]);

  const items = [...streamed, ...backlog];
  const unread = items.reduce((count, item) => (item.read ? count : count + 1), 0);

  async function markRead(id: string): Promise<void> {
    const updated = await notifications.markRead(id);
    // Patch both sources in place rather than invalidating the query --
    // marking one notification read must not refetch the whole backlog.
    setStreamed((current) => current.map((item) => (item.id === id ? updated : item)));
    queryClient.setQueryData<Notification[]>(QUERY_KEY, (old) =>
      old?.map((item) => (item.id === id ? updated : item)),
    );
  }

  return { items, unread, markRead };
}
