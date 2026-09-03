import type { BadgeTone } from "../components/Badge";

/**
 * Tone map for `RunStatus` (backend/app/db/models.py: `running`/`ok`/
 * `error`/`aborted`) -- shared by Traces.tsx and Conversations.tsx so a run
 * badge means the same thing everywhere it appears. Originally lived only
 * in Traces.tsx; Conversations.tsx used to reuse a conversation-status map
 * instead, which happened to share the `error` key but rendered a
 * `running`/`ok` run as neutral grey -- a still-running run looked
 * identical to a finished one on that screen alone.
 */
export const RUN_STATUS_TONE: Record<string, BadgeTone> = {
  ok: "success",
  error: "danger",
  aborted: "warning",
  running: "info",
};
