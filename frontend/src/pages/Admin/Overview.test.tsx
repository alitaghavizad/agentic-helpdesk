import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { Overview } from "./Overview";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const OVERVIEW_BODY = {
  runs_today: 42,
  spend_today: 12.5,
  pending_approvals: 3,
  open_tickets: 7,
  error_rate: 0.25,
};

/** A `text/event-stream` response left open until the test ends it,
 * matching a real long-lived connection -- copied from
 * useNotifications.test.tsx / useRunStream.test.tsx's helper of the same
 * name. Returns the controller so a test can push frames or close the
 * stream to simulate the backend dropping it. */
function openStreamResponse() {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      ctrl = controller;
    },
  });
  return { response: new Response(body, { headers: { "content-type": "text/event-stream" } }), controller: ctrl };
}

function frame(event: Record<string, unknown>): string {
  return `data: ${JSON.stringify(event)}\n\n`;
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

describe("Overview", () => {
  it("renders the five counters from GET /api/admin/overview, with error_rate labelled by what it measures", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/overview")) return jsonResponse(OVERVIEW_BODY);
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      throw new Error(`unexpected call: ${u}`);
    });

    render(<Overview />, { wrapper });

    expect(await screen.findByText("42")).toBeInTheDocument();
    expect(screen.getByText("$12.50")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("25.0%")).toBeInTheDocument();
    // error_rate is a fraction of today's COMPLETED runs, not of every run
    // (in-flight runs have no outcome yet) -- the label must say so, not
    // just "Error rate".
    expect(screen.getByText(/of today's completed runs/i)).toBeInTheDocument();
  });

  it("shows a loading state before the overview responds", async () => {
    let resolveOverview!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/overview")) {
        return new Promise<Response>((resolve) => { resolveOverview = resolve; });
      }
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      throw new Error(`unexpected call: ${u}`);
    });

    render(<Overview />, { wrapper });

    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
    resolveOverview(jsonResponse(OVERVIEW_BODY));
    await waitFor(() => expect(screen.getByText("42")).toBeInTheDocument());
  });

  it("renders a failed overview fetch as StateBlock's error state, never as empty/zeroed counters", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/overview")) return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      throw new Error(`unexpected call: ${u}`);
    });

    render(<Overview />, { wrapper });

    // describeError maps any 403 to spec 6.5's fixed wording rather than
    // whatever detail FastAPI's dependency happened to raise.
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByText("Runs today")).not.toBeInTheDocument();
  });

  it("appends a run_finished frame arriving on the stream to the activity feed", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/overview")) return jsonResponse(OVERVIEW_BODY);
      if (u.endsWith("/api/admin/runs/stream")) {
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode(frame({ type: "run_finished", id: "run-9", status: "ok" })));
          },
        });
        return new Response(body, { headers: { "content-type": "text/event-stream" } });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    render(<Overview />, { wrapper });

    await screen.findByText("42");
    expect(await screen.findByText("run-9")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("refetches the overview counters after the run stream disconnects", async () => {
    let overviewFetches = 0;
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/overview")) {
        overviewFetches += 1;
        return jsonResponse({ ...OVERVIEW_BODY, runs_today: overviewFetches === 1 ? 42 : 99 });
      }
      if (u.endsWith("/api/admin/runs/stream")) {
        const opened = openStreamResponse();
        streamController = opened.controller;
        return opened.response;
      }
      throw new Error(`unexpected call: ${u}`);
    });

    render(<Overview />, { wrapper });

    // Wait for the initial fetch and a live connection.
    await screen.findByText("42");
    await screen.findByText("Live");

    // Simulate the backend dropping this subscriber (app/admin/router.py
    // closes rather than queues for one that falls behind) by ending the
    // stream's body.
    await act(async () => {
      streamController.close();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    // The disconnect alone -- not a manual reload -- must trigger a second
    // read of the counters, since there is no backlog on reconnect to make
    // up the gap.
    await waitFor(() => expect(overviewFetches).toBe(2));
    expect(await screen.findByText("99")).toBeInTheDocument();
  });
});
