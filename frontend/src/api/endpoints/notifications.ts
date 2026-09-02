import { apiFetch, apiStream } from "../client";
import type { components } from "../schema";

export type Notification = components["schemas"]["NotificationResponse"];

/**
 * A raw frame off `GET /api/notifications/stream`. Not the same shape as
 * `Notification`: the backend serializes the stream by hand
 * (app/notifications/router.py's `event_stream`) rather than through the
 * `NotificationResponse` model the two REST endpoints below use, so it
 * carries `created_at` and omits `read` (everything the stream emits --
 * backlog replay or live -- is unread by construction).
 */
export interface NotificationStreamEvent {
  id: string;
  type: string;
  title: string;
  body: string;
  link_type: string | null;
  link_id: string | null;
  created_at: string | null;
}

export const list = () => apiFetch<Notification[]>("/api/notifications");

export const markRead = (id: string) =>
  apiFetch<Notification>(`/api/notifications/${id}/read`, { method: "POST" });

/**
 * Never EventSource (backend/app/deps.py authenticates every route from an
 * Authorization header, which EventSource cannot send) -- apiStream carries
 * the bearer token and hands back the raw Response for readSse to parse.
 */
export const stream = (init: RequestInit) => apiStream("/api/notifications/stream", init);
