import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { Costs } from "./Costs";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const COSTS_BODY = {
  by_day: [
    { day: "2026-09-01", cost_usd: 10 },
    { day: "2026-09-02", cost_usd: 40 },
  ],
  by_model: [
    { model: "claude-opus-5", cost_usd: 25.5, calls: 12, unpriced_calls: 0 },
    { model: "gemini-2.5-flash", cost_usd: null, calls: 5, unpriced_calls: 3 },
  ],
  by_user: [
    { username: "j.doe", cost_usd: 5.25 },
    { username: "(guest)", cost_usd: 1.1 },
  ],
  by_trigger: [
    { trigger: "chat_turn", cost_usd: 30, runs: 10 },
    { trigger: "ticket_dossier", cost_usd: 5, runs: 2 },
  ],
  totals: {
    input_tokens: 10_000,
    output_tokens: 4_000,
    cache_read_tokens: 2_000,
    cache_write_tokens: 500,
    cost_usd: 50,
    cache_hit_rate: 0.16,
    unpriced_calls: 3,
  },
};

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

describe("Costs", () => {
  it("renders each of the four groupings as its own table", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) return jsonResponse(COSTS_BODY);
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    expect(await screen.findByText("claude-opus-5")).toBeInTheDocument();
    expect(screen.getByText("gemini-2.5-flash")).toBeInTheDocument();
    expect(screen.getByText("j.doe")).toBeInTheDocument();
    expect(screen.getByText("(guest)")).toBeInTheDocument();
    expect(screen.getByText("chat_turn")).toBeInTheDocument();
    expect(screen.getByText("ticket_dossier")).toBeInTheDocument();
    expect(screen.getByText("2026-09-01")).toBeInTheDocument();
    expect(screen.getByText("2026-09-02")).toBeInTheDocument();
  });

  it("renders totals with cache_hit_rate as a percentage and cost_usd through usd()", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) return jsonResponse(COSTS_BODY);
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    await screen.findByText("claude-opus-5");
    expect(screen.getByText("16.0%")).toBeInTheDocument();
    expect(screen.getByText("10,000")).toBeInTheDocument();
    expect(screen.getByText("4,000")).toBeInTheDocument();
    expect(screen.getByText("2,000")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getByText("$50.00")).toBeInTheDocument();
  });

  it("labels cache_hit_rate with its denominator (input + cache read + cache write tokens)", async () => {
    // app/admin/queries.py's costs() computes cache_read / (input + cache_read
    // + cache_write) -- deliberately including cache WRITES, not just reads
    // plus fresh input. A bare "Cache hit rate" would not say that, and the
    // number is easy to misread without it (design spec 5.3 requires the
    // denominator be stated, the same rule Overview follows for error_rate).
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) return jsonResponse(COSTS_BODY);
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    await screen.findByText("claude-opus-5");
    expect(screen.getByText(/cache hit rate.*input.*cache read.*cache write/i)).toBeInTheDocument();
  });

  it("renders a by_model row whose cost_usd is null as 'unpriced', never as $0.00", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) return jsonResponse(COSTS_BODY);
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    const modelRow = (await screen.findByText("gemini-2.5-flash")).closest("tr");
    expect(modelRow).not.toBeNull();
    expect(within(modelRow as HTMLElement).getByText("unpriced")).toBeInTheDocument();
    expect(within(modelRow as HTMLElement).queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("renders each model's unpriced_calls count in the By model table", async () => {
    // app/admin/queries.py's costs() coalesces an all-NULL model group's
    // SUM to 0.0, so "gemini-2.5-flash"'s cost_usd: null is not enough on
    // its own to show its 3 calls carry no known price -- this column is
    // the field that actually says so, per model.
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) return jsonResponse(COSTS_BODY);
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    const unpricedRow = (await screen.findByText("gemini-2.5-flash")).closest("tr") as HTMLElement;
    const pricedRow = screen.getByText("claude-opus-5").closest("tr") as HTMLElement;
    expect(within(unpricedRow).getByText("3")).toBeInTheDocument();
    expect(within(pricedRow).getByText("0")).toBeInTheDocument();
  });

  it("warns that the total understates spend when totals.unpriced_calls is greater than zero", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) return jsonResponse(COSTS_BODY);
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    expect(await screen.findByText(/total cost excludes 3 unpriced calls/i)).toBeInTheDocument();
  });

  it("shows no unpriced-calls warning when every call is priced", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) {
        return jsonResponse({
          ...COSTS_BODY,
          by_model: [{ model: "claude-opus-5", cost_usd: 25.5, calls: 12, unpriced_calls: 0 }],
          totals: { ...COSTS_BODY.totals, unpriced_calls: 0 },
        });
      }
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    await screen.findByText("claude-opus-5");
    expect(screen.queryByText(/total cost excludes/i)).not.toBeInTheDocument();
  });

  it("sizes the by-day bar widths proportionally to the largest day", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) return jsonResponse(COSTS_BODY);
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    await screen.findByText("claude-opus-5");
    // day 1 cost 10, day 2 cost 40 (the largest) -> 25% and 100% widths.
    const day1 = screen.getByRole("img", { name: /2026-09-01/ });
    const day2 = screen.getByRole("img", { name: /2026-09-02/ });
    expect(day1.style.width).toBe("25%");
    expect(day2.style.width).toBe("100%");
  });

  it("shows a loading state before the costs response arrives", async () => {
    let resolveCosts!: (value: Response) => void;
    fetchMock.mockImplementation(
      () => new Promise<Response>((resolve) => { resolveCosts = resolve; }),
    );

    render(<Costs />, { wrapper });

    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
    resolveCosts(jsonResponse(COSTS_BODY));
    await screen.findByText("claude-opus-5");
  });

  it("renders a failed costs fetch as StateBlock's error state, never as empty tables", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) {
        return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      }
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    // describeError maps any 403 to spec 6.5's fixed wording rather than
    // whatever detail FastAPI's dependency happened to raise.
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders an empty grouping distinguishably from a failed one", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/admin/costs")) {
        return jsonResponse({ ...COSTS_BODY, by_user: [] });
      }
      throw new Error(`unexpected call: ${url}`);
    });

    render(<Costs />, { wrapper });

    await screen.findByText("claude-opus-5");
    expect(screen.getByText("No user activity yet.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
