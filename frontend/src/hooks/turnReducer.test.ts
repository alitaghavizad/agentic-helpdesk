import { describe, expect, it } from "vitest";
import { emptyTurn, turnReducer } from "./turnReducer";

const apply = (...frames: Record<string, unknown>[]) => frames.reduce(turnReducer, emptyTurn());

describe("turnReducer", () => {
  it("accumulates token frames into one message", () => {
    expect(apply({ type: "token", text: "Hel" }, { type: "token", text: "lo" }).text).toBe("Hello");
  });

  it("pairs tool_start with tool_end by id", () => {
    const state = apply(
      { type: "tool_start", name: "search_knowledge", id: "t1" },
      { type: "tool_start", name: "lookup_employee", id: "t2" },
      { type: "tool_end", name: "search_knowledge", id: "t1", is_error: false },
    );
    expect(state.tools).toEqual([
      { id: "t1", name: "search_knowledge", status: "ok" },
      { id: "t2", name: "lookup_employee", status: "running" },
    ]);
  });

  it("marks a failed tool as failed, not as finished", () => {
    const state = apply(
      { type: "tool_start", name: "send_email", id: "t1" },
      { type: "tool_end", name: "send_email", id: "t1", is_error: true },
    );
    expect(state.tools[0].status).toBe("error");
  });

  it("collects the four outcome events as cards", () => {
    const state = apply(
      { type: "ticket_created", ticket_number: "TCK-000012", ticket_id: "abc" },
      { type: "approval_requested", request_number: "REQ-000003" },
      { type: "task_recorded", title: "VPN failure" },
      { type: "attachment_request", reason: "need a screenshot" },
    );
    expect(state.outcomes.map((o) => o.type)).toEqual([
      "ticket_created", "approval_requested", "task_recorded", "attachment_request",
    ]);
    expect(state.outcomes[0].data.ticket_number).toBe("TCK-000012");
  });

  it("keeps the text already streamed when an error arrives", () => {
    // The backend emits `error` mid-turn (budget exceeded, refusal). The
    // partial answer stays on screen: discarding it loses work the user
    // already read.
    const state = apply({ type: "token", text: "Partial" }, { type: "error", message: "Turn ended: budget." });
    expect(state.text).toBe("Partial");
    expect(state.error).toBe("Turn ended: budget.");
  });

  it("captures run_id from done, which is the link to the trace", () => {
    const state = apply({ type: "done", run_id: "r-1" });
    expect(state).toMatchObject({ runId: "r-1", done: true });
  });

  it("ignores an unknown event type instead of throwing", () => {
    // A backend that grows a tenth event type must not blank the chat page.
    expect(() => apply({ type: "something_new" })).not.toThrow();
  });
});
