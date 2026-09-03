import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRunStream } from "./useRunStream";

// The hook's own cap (useRunStream.ts's MAX_BACKOFF_MS).
const MAX_BACKOFF_MS = 30_000;

async function flushMicrotasks(times = 15) {
  await act(async () => {
    for (let i = 0; i < times; i++) await Promise.resolve();
  });
}

// fetch is stubbed directly, the same way useNotifications.test.tsx does it:
// the behaviours here hinge on controlling exactly which bytes arrive on
// the stream's body and on when it ends, which is simpler against a plain
// vi.fn() than through a request-interceptor library.
const fetchMock = vi.fn();

/**
 * A `text/event-stream` response whose body stays open until the test (or
 * an abort) ends it -- matching a real long-lived connection, copied from
 * useNotifications.test.tsx's helper of the same name. Returns the
 * controller too, so a test can push more frames or close the stream
 * itself to simulate the backend ending it (app/admin/router.py's
 * `admin_runs_stream` does this both for a normal drop and for the
 * dropped-subscriber case).
 */
function openStreamResponse(signal?: AbortSignal, ...frames: string[]) {
  const encoder = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      ctrl = controller;
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
    },
  });
  signal?.addEventListener("abort", () => {
    try {
      ctrl.error(new DOMException("The operation was aborted.", "AbortError"));
    } catch {
      // Already closed/errored -- nothing left to do.
    }
  });
  return { response: new Response(body, { headers: { "content-type": "text/event-stream" } }), controller: ctrl };
}

function frame(event: Record<string, unknown>): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useRunStream", () => {
  it("reports connected once the stream opens", async () => {
    fetchMock.mockImplementation(async () => openStreamResponse().response);

    const { result } = renderHook(() => useRunStream());

    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(result.current.events).toEqual([]);
  });

  it("appends a run_finished frame arriving on the stream", async () => {
    const event = { type: "run_finished", id: "run-1", trigger: "chat_turn", status: "ok", duration_ms: 1200, cost_usd: 0.05 };
    fetchMock.mockImplementation(async () => openStreamResponse(undefined, frame(event)).response);

    const { result } = renderHook(() => useRunStream());

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0]).toEqual(event);
  });

  it("prepends new frames so the newest run leads the feed", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    fetchMock.mockImplementation(async () => {
      const opened = openStreamResponse(undefined, frame({ type: "run_finished", id: "run-1" }));
      controller = opened.controller;
      return opened.response;
    });

    const { result } = renderHook(() => useRunStream());
    await waitFor(() => expect(result.current.events).toHaveLength(1));

    await act(async () => {
      controller.enqueue(new TextEncoder().encode(frame({ type: "run_finished", id: "run-2" })));
      await Promise.resolve();
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.events.map((e) => e.id)).toEqual(["run-2", "run-1"]);
  });

  it("ignores a frame with no id or type rather than crashing the loop", async () => {
    // isRunEvent's guard: a malformed frame must be skipped, not thrown --
    // one bad frame taking down the whole feed would be worse than
    // dropping it.
    fetchMock.mockImplementation(async () =>
      openStreamResponse(undefined, frame({ not: "an event" }), frame({ type: "run_finished", id: "run-1" })).response,
    );

    const { result } = renderHook(() => useRunStream());

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].id).toBe("run-1");
  });

  it("flips connected to false when the stream ends (the backend drops a subscriber rather than queueing)", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    fetchMock.mockImplementation(async () => {
      const opened = openStreamResponse();
      controller = opened.controller;
      return opened.response;
    });

    const { result } = renderHook(() => useRunStream());
    await waitFor(() => expect(result.current.connected).toBe(true));

    await act(async () => {
      controller.close();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.connected).toBe(false);
  });

  it("reconnects with backoff and reports connected again once the retry succeeds", async () => {
    let streamCalls = 0;
    let firstController!: ReadableStreamDefaultController<Uint8Array>;
    fetchMock.mockImplementation(async () => {
      streamCalls += 1;
      const opened = openStreamResponse();
      if (streamCalls === 1) firstController = opened.controller;
      return opened.response;
    });

    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useRunStream());
      await flushMicrotasks();
      expect(result.current.connected).toBe(true);
      expect(streamCalls).toBe(1);

      await act(async () => {
        firstController.close();
      });
      await flushMicrotasks();
      expect(result.current.connected).toBe(false);

      // First backoff delay is 1000ms.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      await flushMicrotasks();

      expect(streamCalls).toBe(2);
      expect(result.current.connected).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("aborts the stream's AbortSignal on unmount", async () => {
    // The global constraint this phase is held to: every stream must tie
    // its AbortController to unmount. Removing the `controller.abort()`
    // call from useRunStream's cleanup makes this fail.
    let capturedSignal: AbortSignal | undefined;
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      capturedSignal = init?.signal ?? undefined;
      return openStreamResponse(capturedSignal).response;
    });

    const { unmount } = renderHook(() => useRunStream());
    await waitFor(() => expect(capturedSignal).toBeDefined());
    expect(capturedSignal?.aborted).toBe(false);

    unmount();

    expect(capturedSignal?.aborted).toBe(true);
  });

  it("does not reconnect after unmount, even across a full backoff window", async () => {
    let streamCalls = 0;
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      streamCalls += 1;
      return openStreamResponse(init?.signal ?? undefined).response;
    });

    vi.useFakeTimers();
    try {
      const { unmount } = renderHook(() => useRunStream());
      await flushMicrotasks();
      expect(streamCalls).toBe(1);

      unmount();

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
