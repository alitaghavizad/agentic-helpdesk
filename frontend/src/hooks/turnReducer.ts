/**
 * Reduces the raw frames off `POST /api/conversations/{id}/messages` (read
 * through `readSse`) into the state a chat turn renders. The nine event
 * types are fixed by `backend/app/agent/loop.py` -- `token`, `tool_start`,
 * `tool_end`, `task_recorded`, `ticket_created`, `approval_requested`,
 * `attachment_request`, `error`, `done` -- but a frame is untyped JSON off
 * the wire, so every field is read defensively rather than trusted.
 */

/** A raw frame off the wire, exactly what `readSse` yields. */
export type TurnFrame = Record<string, unknown>;

export type ToolStatus = "running" | "ok" | "error";

export interface ToolRow {
  id: string;
  name: string;
  status: ToolStatus;
}

export type OutcomeType = "ticket_created" | "approval_requested" | "task_recorded" | "attachment_request";

export interface Outcome {
  type: OutcomeType;
  /** Everything the backend sent on the frame except `type` itself. */
  data: Record<string, unknown>;
}

export interface TurnState {
  text: string;
  tools: ToolRow[];
  outcomes: Outcome[];
  error: string | null;
  runId: string | null;
  done: boolean;
}

export function emptyTurn(): TurnState {
  return { text: "", tools: [], outcomes: [], error: null, runId: null, done: false };
}

const OUTCOME_TYPES: readonly OutcomeType[] = [
  "ticket_created", "approval_requested", "task_recorded", "attachment_request",
];

function isOutcomeType(value: unknown): value is OutcomeType {
  return typeof value === "string" && (OUTCOME_TYPES as readonly string[]).includes(value);
}

/**
 * A pure reducer over one frame at a time, the same shape as a Redux
 * reducer, so `useChatTurn` can fold the stream into state with a plain
 * `setState((current) => turnReducer(current, frame))` on every frame
 * without re-deriving anything from the frames that came before it.
 *
 * An unrecognised `type` -- a future tenth event, a malformed frame -- is
 * ignored rather than thrown: one bad frame must not blank the whole chat
 * page mid-turn.
 */
export function turnReducer(state: TurnState, frame: TurnFrame): TurnState {
  const type = frame.type;

  if (type === "token") {
    const text = frame.text;
    if (typeof text !== "string") return state;
    return { ...state, text: state.text + text };
  }

  if (type === "tool_start") {
    const { id, name } = frame;
    if (typeof id !== "string" || typeof name !== "string") return state;
    return { ...state, tools: [...state.tools, { id, name, status: "running" }] };
  }

  if (type === "tool_end") {
    const { id } = frame;
    if (typeof id !== "string") return state;
    const status: ToolStatus = frame.is_error === true ? "error" : "ok";
    return {
      ...state,
      tools: state.tools.map((tool) => (tool.id === id ? { ...tool, status } : tool)),
    };
  }

  if (isOutcomeType(type)) {
    const { type: _drop, ...data } = frame;
    return { ...state, outcomes: [...state.outcomes, { type, data }] };
  }

  if (type === "error") {
    const message = frame.message;
    // The partial answer already streamed stays on screen (spec §6.4) --
    // this only ever adds `error`, never touches `text`.
    return { ...state, error: typeof message === "string" ? message : "An error occurred." };
  }

  if (type === "done") {
    const runId = frame.run_id;
    return { ...state, runId: typeof runId === "string" ? runId : null, done: true };
  }

  return state;
}
