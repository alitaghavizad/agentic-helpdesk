import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Users } from "./Users";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const SEEDED_EMPLOYEE = {
  id: "u1",
  username: "jamie.rivera",
  email: "jamie.rivera@example.com",
  full_name: "Jamie Rivera",
  role: "employee",
  clearance: "standard",
  department: "Finance",
  employee_ref: "EMP-042",
  helpdesk_ref: null,
  is_active: true,
  dev_seed: true,
};

const ADMIN_ROW = {
  id: "u2",
  username: "admin",
  email: "admin@example.com",
  full_name: "Administrator",
  role: "admin",
  clearance: "privileged",
  department: null,
  employee_ref: null,
  helpdesk_ref: null,
  is_active: true,
  dev_seed: false,
};

function usersPage(items: unknown[], { limit = 50, offset = 0, total = items.length } = {}) {
  return { items, limit, offset, total };
}

function renderUsers() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/users"]}>
        <Users />
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

describe("Admin Users", () => {
  it("renders username, full name, email, role, clearance, department, refs and a dev-seed badge", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) {
        return jsonResponse(usersPage([SEEDED_EMPLOYEE, ADMIN_ROW]));
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();

    expect(await screen.findByText("jamie.rivera")).toBeInTheDocument();
    expect(screen.getByText("Jamie Rivera")).toBeInTheDocument();
    expect(screen.getByText("jamie.rivera@example.com")).toBeInTheDocument();
    expect(screen.getByLabelText("Role for jamie.rivera")).toHaveValue("employee");
    expect(screen.getByLabelText("Clearance for jamie.rivera")).toHaveValue("standard");
    expect(screen.getByText("Finance")).toBeInTheDocument();
    expect(screen.getByText("EMP-042")).toBeInTheDocument();
    expect(screen.getByText("dev seed")).toBeInTheDocument();

    // The admin row is seeded from a different password (ADMIN_PASSWORD,
    // not the shared dev password) and must NOT be flagged the same way.
    expect(screen.getByText("admin@example.com")).toBeInTheDocument();
    expect(screen.getByLabelText("Role for admin")).toHaveValue("admin");
    // Only one "dev seed" badge on screen, for the seeded row alone.
    expect(screen.getAllByText("dev seed")).toHaveLength(1);
  });

  it("renders '—' for a null department and a row with no refs at all", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) return jsonResponse(usersPage([ADMIN_ROW]));
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();

    expect(await screen.findByText("admin@example.com")).toBeInTheDocument();
    // department is null and there is no employee_ref/helpdesk_ref -- both
    // render as an explicit placeholder, not a blank cell or "null".
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("sends PATCH with only the changed role and re-renders the row from the PATCH response", async () => {
    const user = userEvent.setup();
    let patchBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) return jsonResponse(usersPage([SEEDED_EMPLOYEE]));
      if (u.endsWith("/api/admin/users/u1") && init?.method === "PATCH") {
        patchBody = JSON.parse(String(init.body));
        // The server's response deliberately differs from what a naive
        // optimistic update would show (clearance bumped to "sensitive"
        // even though only role was sent) -- proving the row re-renders
        // from THIS response, not from the option the admin merely
        // selected.
        return jsonResponse({ id: "u1", role: "helpdesk", clearance: "sensitive" });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();
    const roleSelect = await screen.findByLabelText("Role for jamie.rivera");
    await user.selectOptions(roleSelect, "helpdesk");

    await vi.waitFor(() => {
      expect(patchBody).toEqual({ role: "helpdesk" });
    });
    await vi.waitFor(() => {
      expect(screen.getByLabelText("Role for jamie.rivera")).toHaveValue("helpdesk");
    });
    // The clearance select reflects the server's response value too, not
    // the pre-edit "standard" the row started with.
    expect(screen.getByLabelText("Clearance for jamie.rivera")).toHaveValue("sensitive");
  });

  it("sends PATCH with only the changed clearance and re-renders the row from the PATCH response", async () => {
    const user = userEvent.setup();
    let patchBody: unknown = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) return jsonResponse(usersPage([SEEDED_EMPLOYEE]));
      if (u.endsWith("/api/admin/users/u1") && init?.method === "PATCH") {
        patchBody = JSON.parse(String(init.body));
        return jsonResponse({ id: "u1", role: "employee", clearance: "privileged" });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();
    const clearanceSelect = await screen.findByLabelText("Clearance for jamie.rivera");
    await user.selectOptions(clearanceSelect, "privileged");

    await vi.waitFor(() => {
      expect(patchBody).toEqual({ clearance: "privileged" });
    });
    await vi.waitFor(() => {
      expect(screen.getByLabelText("Clearance for jamie.rivera")).toHaveValue("privileged");
    });
  });

  it("pages a 126-row dataset using the server's own limit, three pages of 50/50/26", async () => {
    const user = userEvent.setup();
    const page1Items = Array.from({ length: 50 }, (_, i) => ({ ...SEEDED_EMPLOYEE, id: `p1-${i}`, username: `user-${i}` }));
    const page2Items = Array.from({ length: 50 }, (_, i) => ({ ...SEEDED_EMPLOYEE, id: `p2-${i}`, username: `user-${50 + i}` }));
    const page3Items = Array.from({ length: 26 }, (_, i) => ({ ...SEEDED_EMPLOYEE, id: `p3-${i}`, username: `user-${100 + i}` }));
    const requestedOffsets: string[] = [];
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      requestedOffsets.push(u);
      if (u.endsWith("/api/admin/users?offset=0")) return jsonResponse(usersPage(page1Items, { total: 126 }));
      if (u.endsWith("/api/admin/users?offset=50")) return jsonResponse(usersPage(page2Items, { offset: 50, total: 126 }));
      if (u.endsWith("/api/admin/users?offset=100")) return jsonResponse(usersPage(page3Items, { offset: 100, total: 126 }));
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();

    expect(await screen.findByText("user-0")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–50 of 126")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).not.toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("user-50")).toBeInTheDocument();
    expect(screen.queryByText("user-0")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 51–100 of 126")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).not.toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("user-100")).toBeInTheDocument();
    expect(screen.getByText("Showing 101–126 of 126")).toBeInTheDocument();
    // Last page: exactly 26 rows, and Next is now disabled -- there is no
    // fourth page to request.
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByText("user-50")).toBeInTheDocument();

    expect(requestedOffsets).toEqual([
      "http://localhost:8000/api/admin/users?offset=0",
      "http://localhost:8000/api/admin/users?offset=50",
      "http://localhost:8000/api/admin/users?offset=100",
      "http://localhost:8000/api/admin/users?offset=50",
    ]);
  });

  it("pages using the response's clamped limit, not an assumed page size", async () => {
    // Simulates queries.clamp_limit answering an over-large request with a
    // smaller server maximum: the response says limit=40, not the 50 this
    // screen would otherwise be tempted to hardcode. The pager's next
    // offset must come from THIS number.
    const user = userEvent.setup();
    const page1 = Array.from({ length: 40 }, (_, i) => ({ ...SEEDED_EMPLOYEE, id: `a-${i}`, username: `acct-${i}` }));
    const page2 = Array.from({ length: 40 }, (_, i) => ({ ...SEEDED_EMPLOYEE, id: `b-${i}`, username: `acct-${40 + i}` }));
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) return jsonResponse(usersPage(page1, { limit: 40, total: 90 }));
      if (u.endsWith("/api/admin/users?offset=40")) return jsonResponse(usersPage(page2, { limit: 40, offset: 40, total: 90 }));
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();
    expect(await screen.findByText("Showing 1–40 of 90")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    // If the pager had hardcoded 50, this would request offset=50 and the
    // mock above would throw "unexpected call".
    expect(await screen.findByText("Showing 41–80 of 90")).toBeInTheDocument();
  });

  it("does not render a pager when the whole dataset fits on one page", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) return jsonResponse(usersPage([SEEDED_EMPLOYEE], { total: 1 }));
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();
    expect(await screen.findByText("jamie.rivera")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next page" })).not.toBeInTheDocument();
  });

  it("issues exactly one request for a page, not one per row", async () => {
    const items = Array.from({ length: 10 }, (_, i) => ({ ...SEEDED_EMPLOYEE, id: `r-${i}`, username: `row-${i}` }));
    let calls = 0;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) {
        calls += 1;
        return jsonResponse(usersPage(items, { total: 10 }));
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();
    expect(await screen.findByText("row-0")).toBeInTheDocument();
    expect(screen.getByText("row-9")).toBeInTheDocument();

    await act(async () => {
      for (let i = 0; i < 10; i++) await Promise.resolve();
    });
    expect(calls).toBe(1);
  });

  it("shows a loading state before the users response arrives", async () => {
    let resolveList!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) {
        return new Promise<Response>((resolve) => {
          resolveList = resolve;
        });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();
    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
    resolveList(jsonResponse(usersPage([SEEDED_EMPLOYEE])));
    await screen.findByText("jamie.rivera");
  });

  it("renders a failed users fetch as StateBlock's error state, never as an empty table", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByText("jamie.rivera")).not.toBeInTheDocument();
  });

  it("renders an empty account list distinguishably from a failed one", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/admin/users?offset=0")) return jsonResponse(usersPage([], { total: 0 }));
      throw new Error(`unexpected call: ${u}`);
    });

    renderUsers();
    expect(await screen.findByText("No user accounts.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
