import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useNotifications } from "./useNotifications";
import * as authCtx from "../auth/AuthContext";

// fetch is stubbed directly, the same way client.test.ts and
// AuthContext.test.tsx do it, rather than through MSW: the four behaviours
// here hinge on exact call sequencing (never call /stream for a guest, never
// re-fetch the backlog from markRead) and on controlling exactly which bytes
// arrive on the stream's ReadableStream body -- both are simpler to assert
// against a single vi.fn() than to wire through a service-worker-style
// request interceptor for a body type (ReadableStream) MSW's Node interop
// has to special-case.
const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** A `text/event-stream` response whose body never closes -- matching a real
 * long-lived connection, so the hook never runs its reconnect-with-backoff
 * path (and never schedules a stray timer) during these tests. */
function openStreamResponse(...frames: string[]) {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      // deliberately left open
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}

const USER = {
  kind: "user", user_id: "u1", role: "employee", clearance: "standard",
  department: "Engineering", employee_ref: "EMP-1", helpdesk_ref: null,
  username: "j.doe", full_name: "Jane Doe",
};
const GUEST = { kind: "guest", user_id: null, role: "guest", clearance: "standard",
  department: null, employee_ref: null, helpdesk_ref: null, username: null, full_name: "Visitor" };

function mockAuth(principal: unknown) {
  vi.spyOn(authCtx, "useAuth").mockReturnValue({
    status: "signed-in", principal, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useNotifications", () => {
  it("does not open the stream for a guest principal", async () => {
    // notifications.user_id is NOT NULL and a guest is not a row in `users`,
    // so GET /api/notifications/stream 403s a guest -- opening it at all
    // would just reconnect forever against that wall.
    mockAuth(GUEST);
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/stream")) throw new Error("must not be called for a guest");
      return jsonResponse([]);
    });

    renderHook(() => useNotifications(), { wrapper });

    // Give any effect a chance to run before asserting the negative -- a
    // guest also never triggers the backlog fetch (the same 403 applies to
    // GET /api/notifications), so there is no query settling to await on.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/stream"))).toBe(false);
  });

  it("prepends a streamed notification", async () => {
    mockAuth(USER);
    const frame = 'data: {"type":"ticket_updated","id":"n1","title":"Ticket updated",'
      + '"body":"Your ticket moved","link_type":"ticket","link_id":"t1","created_at":null}\n\n';
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/stream")) return openStreamResponse(frame);
      return jsonResponse([]);
    });

    const { result } = renderHook(() => useNotifications(), { wrapper });

    await waitFor(() => expect(result.current.items).toHaveLength(1));
    expect(result.current.items[0]).toMatchObject({ id: "n1", title: "Ticket updated", read: false });
    expect(result.current.unread).toBe(1);
  });

  it("does not duplicate a notification present in both backlog and stream", async () => {
    // The backend subscribes to its broker before reading the backlog on
    // purpose, so nothing committed in between is lost -- the cost is that
    // the same row can be replayed on the stream AND returned by the
    // backlog fetch. Collapsing that overlap by id is this hook's job.
    mockAuth(USER);
    const backlogRow = { id: "n1", type: "ticket_updated", title: "Ticket updated",
      body: "Your ticket moved", link_type: "ticket", link_id: "t1", read: false };
    const frame = 'data: {"type":"ticket_updated","id":"n1","title":"Ticket updated",'
      + '"body":"Your ticket moved","link_type":"ticket","link_id":"t1","created_at":null}\n\n';
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/stream")) return openStreamResponse(frame);
      return jsonResponse([backlogRow]);
    });

    const { result } = renderHook(() => useNotifications(), { wrapper });

    await waitFor(() => expect(result.current.items).toHaveLength(1));
    // Give the stream's already-queued frame a moment to be processed too,
    // to prove it was collapsed rather than simply not yet arrived.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].id).toBe("n1");
  });

  it("marks one notification read without refetching the backlog", async () => {
    mockAuth(USER);
    const backlogRow = { id: "n1", type: "ticket_updated", title: "Ticket updated",
      body: "Your ticket moved", link_type: "ticket", link_id: "t1", read: false };
    let backlogFetches = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/stream")) return openStreamResponse();
      if (url.includes("/read") && init?.method === "POST") {
        return jsonResponse({ ...backlogRow, read: true });
      }
      backlogFetches += 1;
      return jsonResponse([backlogRow]);
    });

    const { result } = renderHook(() => useNotifications(), { wrapper });

    await waitFor(() => expect(result.current.unread).toBe(1));
    await result.current.markRead("n1");

    await waitFor(() => expect(result.current.unread).toBe(0));
    expect(result.current.items[0]).toMatchObject({ id: "n1", read: true });
    expect(backlogFetches).toBe(1);
  });
});
