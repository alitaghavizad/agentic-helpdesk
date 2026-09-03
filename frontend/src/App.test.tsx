import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as ctx from "./auth/AuthContext";

const ADMIN = {
  kind: "user", user_id: "u1", role: "admin", clearance: "privileged",
  department: "IT", employee_ref: null, helpdesk_ref: null,
  username: "admin", full_name: "Administrator",
};

// ADMIN is a "user" principal, so Shell -> NavBar renders NotificationBell,
// which fetches through useNotifications -- hence the QueryClient and the
// stubbed fetch below, same as any other test mounting a signed-in Shell.
const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    if (url.includes("/stream")) {
      return new Response(new ReadableStream(), { headers: { "content-type": "text/event-stream" } });
    }
    return jsonResponse([]);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("App routing", () => {
  it("renders not-found for an unknown path nested under /admin", () => {
    // path="/admin/*" matches and shadows the top-level "*" for anything
    // under /admin, so a typo'd admin sub-route must resolve to its own
    // not-found rather than an empty <Outlet/>.
    vi.spyOn(ctx, "useAuth").mockReturnValue({
      status: "signed-in", principal: ADMIN, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
    } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/admin/nonsense"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText(/page not found/i)).toBeInTheDocument();
  });


  it("wires /admin to the real Overview screen, not Task 2's placeholder", async () => {
    vi.spyOn(ctx, "useAuth").mockReturnValue({
      status: "signed-in", principal: ADMIN, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
    } as never);
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/overview")) {
        return jsonResponse({
          runs_today: 1, spend_today: 0, pending_approvals: 0, open_tickets: 0, error_rate: 0,
        });
      }
      if (u.includes("/stream")) {
        return new Response(new ReadableStream(), { headers: { "content-type": "text/event-stream" } });
      }
      return jsonResponse([]);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/admin"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(screen.queryByText(/this screen is not built yet/i)).not.toBeInTheDocument();
  });

  it("wires /admin/costs to the real Costs screen, not Task 2's placeholder", async () => {
    vi.spyOn(ctx, "useAuth").mockReturnValue({
      status: "signed-in", principal: ADMIN, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
    } as never);
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/costs")) {
        return jsonResponse({
          by_day: [], by_model: [], by_user: [], by_trigger: [],
          totals: {
            input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
            cost_usd: 0, cache_hit_rate: 0,
          },
        });
      }
      if (u.includes("/stream")) {
        return new Response(new ReadableStream(), { headers: { "content-type": "text/event-stream" } });
      }
      return jsonResponse([]);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/admin/costs"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Costs" })).toBeInTheDocument();
    expect(screen.queryByText(/this screen is not built yet/i)).not.toBeInTheDocument();
  });

  it("wires /admin/traces to the real Traces screen, not Task 2's placeholder", async () => {
    vi.spyOn(ctx, "useAuth").mockReturnValue({
      status: "signed-in", principal: ADMIN, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
    } as never);
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/runs?limit=50")) {
        return jsonResponse({ items: [], limit: 50, offset: 0, total: 0 });
      }
      if (u.includes("/stream")) {
        return new Response(new ReadableStream(), { headers: { "content-type": "text/event-stream" } });
      }
      return jsonResponse([]);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/admin/traces"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Traces" })).toBeInTheDocument();
    expect(screen.queryByText(/this screen is not built yet/i)).not.toBeInTheDocument();
  });
});
