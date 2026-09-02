import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NavBar } from "./NavBar";
import * as ctx from "../auth/AuthContext";

const BASE = {
  kind: "user", user_id: "u1", role: "employee", clearance: "standard",
  department: "Engineering", employee_ref: "EMP-0007", helpdesk_ref: null,
  username: "j.doe", full_name: "Jane Doe",
};

// A "user" principal (BASE.kind) renders NotificationBell, which fetches
// through useNotifications -- so this file needs a QueryClient and a
// stubbed fetch, the same as any other test that mounts NavBar as-is.
const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function renderWith(principal: Partial<ctx.Principal>) {
  vi.spyOn(ctx, "useAuth").mockReturnValue({
    status: "signed-in", principal, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NavBar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    if (url.includes("/stream")) {
      // Never closes -- matches a real long-lived connection and keeps
      // these tests from exercising the hook's reconnect/backoff path.
      return new Response(new ReadableStream(), { headers: { "content-type": "text/event-stream" } });
    }
    return jsonResponse([]);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("NavBar identity", () => {
  it("shows the principal's full name, not a raw id", () => {
    // The seeded admin (backend/app/db/seed.py) has full_name="Administrator"
    // and neither employee_ref nor helpdesk_ref -- full_name must win over
    // every other fallback or the admin sees a UUID where their name belongs.
    renderWith({ ...BASE, role: "admin", employee_ref: null, full_name: "Administrator", username: "admin" });
    expect(screen.getByText("Administrator")).toBeInTheDocument();
    expect(screen.queryByText("u1")).not.toBeInTheDocument();
  });

  it("falls back to employee_ref only when the server sends neither name field", () => {
    // Defensive path only -- backend/app/auth/router.py always populates
    // full_name today, but this keeps the display honest if that ever lapses.
    renderWith({ ...BASE, full_name: null, username: null });
    expect(screen.getByText("EMP-0007")).toBeInTheDocument();
  });
});
