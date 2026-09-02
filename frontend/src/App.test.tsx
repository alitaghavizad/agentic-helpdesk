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
});
