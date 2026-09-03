import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Traces } from "./Traces";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** A `text/event-stream` response left open until the test ends it --
 * mirrors Overview.test.tsx's helper of the same name (useRunStream is
 * shared by both screens). */
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

const RUN_OK = {
  id: "r1",
  trigger: "chat_turn",
  status: "ok",
  started_at: "2026-09-01T10:00:00Z",
  duration_ms: 1500,
  cost_usd: 0.02,
  llm_calls: 3,
  tool_calls: 1,
  error: null,
};

const RUN_ERROR = {
  id: "r2",
  trigger: "ticket_dossier",
  status: "error",
  started_at: "2026-09-01T11:00:00Z",
  duration_ms: 500,
  cost_usd: null,
  llm_calls: 1,
  tool_calls: 0,
  error: "Model refused: policy violation",
};

const RUNS_PAGE = { items: [RUN_OK, RUN_ERROR], limit: 50, offset: 0, total: 2 };

const TRACE_OK = {
  run: {
    id: "r1",
    trigger: "chat_turn",
    status: "ok",
    started_at: "2026-09-01T10:00:00Z",
    duration_ms: 1500,
    cost_usd: 0.02,
    input_tokens: 100,
    output_tokens: 50,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    error: null,
  },
  roots: [
    {
      id: "span-1",
      kind: "llm",
      name: "answer",
      status: "ok",
      error: null,
      model: "claude-opus-5",
      duration_ms: 1500,
      input_tokens: 100,
      output_tokens: 50,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      cost_usd: 0.02,
      input: { prompt: "hello" },
      output: { text: "hi" },
      children: [],
    },
  ],
  span_count: 1,
  truncated: false,
};

const TRACE_TRUNCATED = {
  ...TRACE_OK,
  span_count: 500,
  truncated: true,
};

function renderTraces({ route = "/admin/traces" }: { route?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/admin/traces" element={<Traces />} />
          <Route path="/admin/traces/:runId" element={<Traces />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Traces", () => {
  it("renders each run's id, trigger, status, started, duration, cost, and llm/tool call counts", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse(RUNS_PAGE);
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    const row = (await screen.findByText("r1")).closest("tr") as HTMLElement;
    expect(within(row).getByText("chat_turn")).toBeInTheDocument();
    expect(within(row).getByText("ok")).toBeInTheDocument();
    expect(within(row).getByText(new Date("2026-09-01T10:00:00Z").toLocaleString())).toBeInTheDocument();
    expect(within(row).getByText("1.5s")).toBeInTheDocument();
    expect(within(row).getByText("$0.020000")).toBeInTheDocument();
    expect(within(row).getByText("3")).toBeInTheDocument();
    expect(within(row).getByText("1")).toBeInTheDocument();
  });

  it("selecting a row loads its trace", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse(RUNS_PAGE);
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      if (u.endsWith("/api/admin/runs/r1/trace")) return jsonResponse(TRACE_OK);
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    await screen.findByText("r1");
    await user.click(screen.getByRole("button", { name: "r1" }));

    expect(await screen.findByText("answer")).toBeInTheDocument();
    expect(screen.getByText("claude-opus-5")).toBeInTheDocument();
  });

  it("renders a visible banner when the trace is truncated", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse(RUNS_PAGE);
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      if (u.endsWith("/api/admin/runs/r1/trace")) return jsonResponse(TRACE_TRUNCATED);
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    await screen.findByText("r1");
    await user.click(screen.getByRole("button", { name: "r1" }));

    // A silently short waterfall would read as a run that simply stopped
    // there -- this banner is the one thing standing between that and the
    // admin knowing spans were actually dropped at the server's cap.
    expect(await screen.findByText(/truncated/i)).toBeInTheDocument();
  });

  it("does not render a truncation banner for a trace that was not truncated", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse(RUNS_PAGE);
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      if (u.endsWith("/api/admin/runs/r1/trace")) return jsonResponse(TRACE_OK);
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    await screen.findByText("r1");
    await user.click(screen.getByRole("button", { name: "r1" }));

    await screen.findByText("answer");
    expect(screen.queryByText(/truncated/i)).not.toBeInTheDocument();
  });

  it("prepends a new run arriving on the stream to the list", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse(RUNS_PAGE);
      if (u.endsWith("/api/admin/runs/stream")) {
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode(frame({ type: "run_finished", id: "r9", trigger: "chat_turn", status: "ok" })));
          },
        });
        return new Response(body, { headers: { "content-type": "text/event-stream" } });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    await screen.findByText("r1");
    const newRow = (await screen.findByText("r9")).closest("tr") as HTMLElement;
    expect(newRow).not.toBeNull();

    // Prepended means it renders ABOVE the fetched page's own rows, not
    // merely present somewhere in the table.
    const rows = screen.getAllByRole("row");
    const newRowIndex = rows.indexOf(newRow);
    const existingRowIndex = rows.indexOf(screen.getByText("r1").closest("tr") as HTMLElement);
    expect(newRowIndex).toBeGreaterThan(0);
    expect(newRowIndex).toBeLessThan(existingRowIndex);
  });

  it("does not duplicate a run once the stream's finished frame is also present in a refetched list", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse(RUNS_PAGE);
      if (u.endsWith("/api/admin/runs/stream")) {
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            // r1 is already in RUNS_PAGE -- a frame for it must not create
            // a second row.
            controller.enqueue(encoder.encode(frame({ type: "run_finished", id: "r1", trigger: "chat_turn", status: "ok" })));
          },
        });
        return new Response(body, { headers: { "content-type": "text/event-stream" } });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    await screen.findByText("r1");
    expect(screen.getAllByText("r1")).toHaveLength(1);
  });

  it("shows an error-status run's error text in the list", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse(RUNS_PAGE);
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    expect(await screen.findByText("Model refused: policy violation")).toBeInTheDocument();
  });

  it("shows a loading state before the runs response arrives", async () => {
    let resolveRuns!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) {
        return new Promise<Response>((resolve) => { resolveRuns = resolve; });
      }
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    // Two role="status" elements can coexist here (the connection banner
    // and StateBlock's own loading indicator) -- find the one that actually
    // says "loading" rather than asserting there is exactly one.
    await waitFor(() => {
      const statuses = screen.getAllByRole("status");
      expect(statuses.some((el) => /loading/i.test(el.textContent ?? ""))).toBe(true);
    });
    resolveRuns(jsonResponse(RUNS_PAGE));
    await screen.findByText("r1");
  });

  it("renders a failed runs fetch as StateBlock's error state, never as an empty table", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) {
        return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      }
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders an empty run list distinguishably from a failed one", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse({ items: [], limit: 50, offset: 0, total: 0 });
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces();

    expect(await screen.findByText("No runs recorded yet.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("loads the deep-linked run's trace directly when the page mounts at /admin/traces/:runId", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) return jsonResponse(RUNS_PAGE);
      if (u.endsWith("/api/admin/runs/stream")) return openStreamResponse().response;
      if (u.endsWith("/api/admin/runs/r1/trace")) return jsonResponse(TRACE_OK);
      throw new Error(`unexpected call: ${u}`);
    });

    renderTraces({ route: "/admin/traces/r1" });

    expect(await screen.findByText("answer")).toBeInTheDocument();
  });
});
