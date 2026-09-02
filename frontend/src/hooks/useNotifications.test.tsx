import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useNotifications } from "./useNotifications";
import * as authCtx from "../auth/AuthContext";

// The hook's own cap (useNotifications.ts's MAX_BACKOFF_MS) -- kept here too
// so the reconnect test can advance comfortably past the worst case without
// hard-coding "30000" twice for no reason.
const MAX_BACKOFF_MS = 30_000;

/** Flushes pending microtask chains (fetch/await resolution) without
 * advancing any timer -- safe to call whether or not fake timers are active,
 * since fake timers only freeze setTimeout/setInterval, not the promise
 * microtask queue. */
async function flushMicrotasks(times = 15) {
  await act(async () => {
    for (let i = 0; i < times; i++) await Promise.resolve();
  });
}

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

/**
 * A `text/event-stream` response whose body never closes on its own --
 * matching a real long-lived connection, so the hook never runs its
 * reconnect-with-backoff path (and never schedules a stray timer) unless a
 * test deliberately aborts it.
 *
 * `signal`, when given, is wired to actually error the stream's reader on
 * abort -- the way a real aborted `fetch` does -- so tests can exercise what
 * happens when the hook's own AbortController fires. A bare stub response
 * would not do this: our stubbed `fetch` never looks at `init.signal` at
 * all, so without this wiring `controller.abort()` would have no observable
 * effect on the body the test already handed back.
 */
function openStreamResponse(signal?: AbortSignal, ...frames: string[]) {
  const encoder = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      ctrl = controller;
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      // deliberately left open
    },
  });
  signal?.addEventListener("abort", () => {
    try {
      ctrl.error(new DOMException("The operation was aborted.", "AbortError"));
    } catch {
      // Already closed/errored -- nothing left to do.
    }
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
      + '"body":"Your ticket moved","link_type":"ticket","link_id":"t1","created_at":"2026-09-02T10:00:00Z"}\n\n';
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/stream")) return openStreamResponse(undefined, frame);
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
      body: "Your ticket moved", link_type: "ticket", link_id: "t1", read: false,
      created_at: "2026-09-02T09:00:00Z" };
    const frame = 'data: {"type":"ticket_updated","id":"n1","title":"Ticket updated",'
      + '"body":"Your ticket moved","link_type":"ticket","link_id":"t1","created_at":"2026-09-02T09:00:00Z"}\n\n';
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/stream")) return openStreamResponse(undefined, frame);
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

  it("does not duplicate a stream frame for an id the backlog already delivered (backlog-first ordering)", async () => {
    // The two named-test scenarios above race the backlog GET against the
    // stream's own backlog replay -- either can "win," so either dedupe path
    // (the `query.data` collapse effect, or the frame-arrival guard) can be
    // the one that actually fires. This pins the frame-arrival guard
    // specifically, by holding the stream open and silent until well after
    // the backlog has already settled -- the realistic production ordering,
    // since a plain GET typically resolves before a stream connection even
    // finishes subscribing.
    mockAuth(USER);
    const backlogRow = { id: "n1", type: "ticket_updated", title: "Ticket updated",
      body: "Your ticket moved", link_type: "ticket", link_id: "t1", read: false,
      created_at: "2026-09-02T09:00:00Z" };
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/stream")) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller;
            // Nothing enqueued yet -- the frame arrives later, once the
            // backlog below has already settled.
          },
        });
        return new Response(body, { headers: { "content-type": "text/event-stream" } });
      }
      return jsonResponse([backlogRow]);
    });

    const { result } = renderHook(() => useNotifications(), { wrapper });

    // Backlog settles first, with nothing yet on the stream.
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    const frame = 'data: {"type":"ticket_updated","id":"n1","title":"Ticket updated",'
      + '"body":"Your ticket moved","link_type":"ticket","link_id":"t1","created_at":"2026-09-02T09:00:00Z"}\n\n';
    await act(async () => {
      streamController?.enqueue(new TextEncoder().encode(frame));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].id).toBe("n1");
  });

  it("marks one notification read without refetching the backlog", async () => {
    mockAuth(USER);
    const backlogRow = { id: "n1", type: "ticket_updated", title: "Ticket updated",
      body: "Your ticket moved", link_type: "ticket", link_id: "t1", read: false,
      created_at: "2026-09-02T09:00:00Z" };
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
    await act(async () => {
      await result.current.markRead("n1");
    });

    await waitFor(() => expect(result.current.unread).toBe(0));
    expect(result.current.items[0]).toMatchObject({ id: "n1", read: true });
    expect(backlogFetches).toBe(1);
  });

  it("aborts the stream's AbortSignal on unmount", async () => {
    // A leaked stream per navigation is exactly what tying the
    // AbortController to unmount exists to prevent (a global constraint of
    // this phase) -- so it needs its own assertion, not just "the other
    // tests didn't crash."
    mockAuth(USER);
    let capturedSignal: AbortSignal | undefined;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/stream")) {
        capturedSignal = init?.signal ?? undefined;
        return openStreamResponse(capturedSignal);
      }
      return jsonResponse([]);
    });

    const { unmount } = renderHook(() => useNotifications(), { wrapper });
    await waitFor(() => expect(capturedSignal).toBeDefined());
    expect(capturedSignal?.aborted).toBe(false);

    unmount();

    expect(capturedSignal?.aborted).toBe(true);
  });

  it("does not reconnect after unmount, even across a full backoff window", async () => {
    // The stream stays open (never closes on its own) until the test aborts
    // it via unmount -- simulating a live connection that unmount cuts off
    // mid-flight, not one that had already ended on its own. Without
    // `cancelled` guarding the reconnect loop's catch block, that abort's
    // rejection is indistinguishable from a dropped connection and the loop
    // schedules a fresh reconnect.
    mockAuth(USER);
    let streamCalls = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/stream")) {
        streamCalls += 1;
        return openStreamResponse(init?.signal ?? undefined);
      }
      return jsonResponse([]);
    });

    vi.useFakeTimers();
    try {
      const { unmount } = renderHook(() => useNotifications(), { wrapper });

      await flushMicrotasks();
      expect(streamCalls).toBe(1);

      unmount();

      // Let the abort's rejection propagate through the read loop, then run
      // the clock past the largest possible backoff delay.
      await flushMicrotasks();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(MAX_BACKOFF_MS + 1_000);
      });

      expect(streamCalls).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
