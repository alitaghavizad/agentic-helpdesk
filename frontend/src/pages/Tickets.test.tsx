import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Tickets, detailPhase } from "./Tickets";
import * as authCtx from "../auth/AuthContext";

// fetch is stubbed directly, the same way Chat.test.tsx and
// useNotifications.test.tsx do it -- MSW is not used anywhere in this
// project.
const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const ADMIN = {
  kind: "user", user_id: "u1", role: "admin", clearance: "privileged",
  department: "IT", employee_ref: null, helpdesk_ref: null,
  username: "admin", full_name: "Administrator",
};
const HELPDESK = {
  kind: "user", user_id: "u2", role: "helpdesk", clearance: "standard",
  department: "IT", employee_ref: null, helpdesk_ref: "HD-1",
  username: "h.specialist", full_name: "Helpdesk Specialist",
};
const EMPLOYEE = {
  kind: "user", user_id: "u3", role: "employee", clearance: "standard",
  department: "Engineering", employee_ref: "EMP-1", helpdesk_ref: null,
  username: "j.doe", full_name: "Jane Doe",
};

const TICKET_1 = {
  id: "t1", ticket_number: "TCK-000001", title: "VPN issue", status: "open",
  priority: "high", assignee_helpdesk_ref: "HD-1", created_at: "2026-09-01T10:00:00Z",
};
const TICKET_2 = {
  id: "t2", ticket_number: "TCK-000002", title: "Password reset", status: "assigned",
  priority: "low", assignee_helpdesk_ref: "HD-2", created_at: "2026-09-01T11:00:00Z",
};

function ticketDetail(overrides: Partial<typeof TICKET_1> = {}) {
  return {
    ...TICKET_1,
    ...overrides,
    body: "The VPN client refuses to connect from home.",
    matched_specialization: "networking",
    assignment_rationale: "best score",
    assignment_score: 0.9,
    resolution: null,
    resolved_at: null,
  };
}

function renderTickets(principal: unknown, { route = "/tickets" }: { route?: string } = {}) {
  vi.spyOn(authCtx, "useAuth").mockReturnValue({
    status: "signed-in", principal, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/tickets" element={<Tickets />} />
          <Route path="/tickets/:id" element={<Tickets />} />
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

describe("Tickets", () => {
  it("renders scoped tickets from GET /api/tickets with number, title, status badge, priority and assignee", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/tickets")) return jsonResponse([TICKET_1, TICKET_2]);
      throw new Error(`unexpected call: ${url}`);
    });

    renderTickets(ADMIN);

    // { selector: "span" } picks out the Badge specifically -- the row's own
    // status-dropdown also has an <option> reading the same text (e.g.
    // "open"), which a bare getByText would ambiguously match too.
    expect(await screen.findByText("TCK-000001")).toBeInTheDocument();
    expect(screen.getByText("VPN issue")).toBeInTheDocument();
    expect(screen.getByText("open", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("high", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("HD-1")).toBeInTheDocument();

    expect(screen.getByText("TCK-000002")).toBeInTheDocument();
    expect(screen.getByText("Password reset")).toBeInTheDocument();
    expect(screen.getByText("assigned", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("low", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("HD-2")).toBeInTheDocument();

    // No filtering client-side -- the client sends the plain GET and
    // trusts the server's row scoping entirely (spec 6.4).
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/tickets", expect.anything());
  });

  it("re-queries GET /api/tickets?status= when the status filter changes", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets") || u.includes("/api/tickets?status=")) return jsonResponse([TICKET_1]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderTickets(ADMIN);
    await screen.findByText("TCK-000001");
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/tickets"))).toBe(true);

    await userEvent.selectOptions(screen.getByLabelText(/filter by status/i), "open");

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/tickets?status=open"))).toBe(true);
    });
  });

  it("shows an employee no edit controls and no Resolve button", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      throw new Error(`unexpected call: ${url}`);
    });

    renderTickets(EMPLOYEE);
    await screen.findByText("TCK-000001");

    // The server would 403 an employee's PATCH/resolve regardless -- showing
    // these controls anyway would be a lie about what the user can do. Spec
    // 5's "/tickets" row promises staff "the edit controls and Resolve" --
    // status, priority, resolve AND reassign are all "the edit controls",
    // so an employee must see none of them, not just the status one.
    expect(screen.queryByLabelText(/status for tck-000001/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/priority for tck-000001/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resolve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reassign/i })).not.toBeInTheDocument();
  });

  it("shows a helpdesk user the full set of edit controls: status, priority, Resolve and Reassign", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      throw new Error(`unexpected call: ${url}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    expect(screen.getByLabelText(/status for tck-000001/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/priority for tck-000001/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resolve/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reassign/i })).toBeInTheDocument();
  });

  it("disables the Resolve modal's submit button until resolution text is non-empty", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      throw new Error(`unexpected call: ${url}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    await userEvent.click(screen.getByRole("button", { name: /resolve/i }));
    const dialog = await screen.findByRole("dialog", { name: /resolve tck-000001/i });
    const submit = within(dialog).getByRole("button", { name: /^resolve$/i });
    expect(submit).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText(/resolution/i), "Restarted the VPN client; connects fine now.");
    expect(submit).toBeEnabled();

    // Whitespace-only must not count as "non-empty" either -- the service
    // layer rejects it too, and the button must not lie about that.
    await userEvent.clear(within(dialog).getByLabelText(/resolution/i));
    await userEvent.type(within(dialog).getByLabelText(/resolution/i), "   ");
    expect(submit).toBeDisabled();
  });

  it("submits the resolution through POST /resolve and never sends status: resolved via PATCH", async () => {
    let resolveCalls = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      if (u.endsWith("/api/tickets/t1/resolve") && init?.method === "POST") {
        resolveCalls += 1;
        expect(JSON.parse(init.body as string)).toEqual({ resolution: "Fixed the VPN client config." });
        return jsonResponse(ticketDetail({ status: "resolved" }));
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    await userEvent.click(screen.getByRole("button", { name: /resolve/i }));
    const dialog = await screen.findByRole("dialog", { name: /resolve tck-000001/i });
    await userEvent.type(within(dialog).getByLabelText(/resolution/i), "Fixed the VPN client config.");
    await userEvent.click(within(dialog).getByRole("button", { name: /^resolve$/i }));

    await waitFor(() => expect(resolveCalls).toBe(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // PATCH is never called for a resolution -- POST /resolve is the only
    // path that can move a ticket to "resolved" (backend/app/tickets/router.py
    // rejects status: "resolved" via PATCH outright).
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "PATCH")).toBe(false);
  });

  it("never offers 'resolved' as a status-dropdown option", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      throw new Error(`unexpected call: ${url}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    const select = screen.getByLabelText(/status for tck-000001/i) as HTMLSelectElement;
    const values = Array.from(select.options).map((option) => option.value);
    expect(values).not.toContain("resolved");
  });

  it("renders a failed PATCH's ApiError detail and leaves the row's status unchanged", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      if (u.endsWith("/api/tickets/t1") && init?.method === "PATCH") {
        return jsonResponse({ detail: "cannot transition open -> closed directly" }, 409);
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    const select = screen.getByLabelText(/status for tck-000001/i) as HTMLSelectElement;
    expect(select.value).toBe("open");

    await userEvent.selectOptions(select, "closed");

    expect(await screen.findByText("cannot transition open -> closed directly")).toBeInTheDocument();
    // The row must not silently flip to the attempted value on failure --
    // it must reflect exactly what the server still has. { selector: "span" }
    // picks out the Badge specifically: the dropdown always has a "closed"
    // <option> regardless of outcome, so a bare query would ambiguously (or
    // wrongly) match that instead of the badge this assertion cares about.
    expect(select.value).toBe("open");
    expect(screen.getByText("open", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByText("closed", { selector: "span" })).not.toBeInTheDocument();
  });

  it("applies a successful status PATCH to the row without re-fetching the list", async () => {
    let listFetches = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/tickets/t1") && init?.method === "PATCH") {
        expect(JSON.parse(init.body as string)).toEqual({ status: "in_progress" });
        return jsonResponse(ticketDetail({ status: "in_progress" }));
      }
      if (u.endsWith("/api/tickets")) {
        listFetches += 1;
        return jsonResponse([TICKET_1]);
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    const select = screen.getByLabelText(/status for tck-000001/i) as HTMLSelectElement;
    await userEvent.selectOptions(select, "in_progress");

    await waitFor(() => expect(select.value).toBe("in_progress"));
    expect(screen.getByText("in_progress", { selector: "span" })).toBeInTheDocument();
    expect(listFetches).toBe(1);
  });

  it("applies a successful priority PATCH to the row via its own select", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/tickets/t1") && init?.method === "PATCH") {
        expect(JSON.parse(init.body as string)).toEqual({ priority: "urgent" });
        return jsonResponse(ticketDetail({ priority: "urgent" }));
      }
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    const select = screen.getByLabelText(/priority for tck-000001/i) as HTMLSelectElement;
    expect(select.value).toBe("high");
    await userEvent.selectOptions(select, "urgent");

    await waitFor(() => expect(select.value).toBe("urgent"));
    expect(screen.getByText("urgent", { selector: "span" })).toBeInTheDocument();
  });

  it("renders a failed priority PATCH's ApiError detail and leaves the priority unchanged", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      if (u.endsWith("/api/tickets/t1") && init?.method === "PATCH") {
        return jsonResponse({ detail: "cannot change priority right now" }, 409);
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    const select = screen.getByLabelText(/priority for tck-000001/i) as HTMLSelectElement;
    await userEvent.selectOptions(select, "urgent");

    expect(await screen.findByText("cannot change priority right now")).toBeInTheDocument();
    expect(select.value).toBe("high");
    expect(screen.getByText("high", { selector: "span" })).toBeInTheDocument();
  });

  it("renders an already-resolved ticket's status as plain text, never a selectable option", async () => {
    // A resolved ticket must not offer a status <select> at all -- if it
    // did, its only reachable value in a select bound to the CURRENT status
    // would be "resolved" itself, which PATCH must never carry (POST
    // /resolve is the only path there). Rendering plain text instead makes
    // that structurally impossible rather than relying on nobody wiring up
    // a way to reach it.
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/tickets")) return jsonResponse([{ ...TICKET_1, status: "resolved" }]);
      throw new Error(`unexpected call: ${url}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    expect(screen.queryByLabelText(/status for tck-000001/i)).not.toBeInTheDocument();
    expect(screen.getAllByText("resolved").length).toBeGreaterThan(0);
  });

  it("disables the Reassign modal's submit button until both the assignee ref and rationale are filled", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      throw new Error(`unexpected call: ${url}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    await userEvent.click(screen.getByRole("button", { name: /reassign/i }));
    const dialog = await screen.findByRole("dialog", { name: /reassign tck-000001/i });
    const submit = within(dialog).getByRole("button", { name: /^reassign$/i });
    // Pre-filled with the current assignee, but with no rationale yet --
    // backend/app/tickets/router.py:127 400s a reassignment with an empty
    // rationale, so the button must not let that request happen.
    expect(submit).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText(/rationale/i), "Escalating to a networking specialist.");
    expect(submit).toBeEnabled();

    await userEvent.clear(within(dialog).getByLabelText(/helpdesk specialist ref/i));
    expect(submit).toBeDisabled();
  });

  it("submits a reassignment via PATCH with both assignee_helpdesk_ref and reassignment_rationale", async () => {
    let patchCalls = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      if (u.endsWith("/api/tickets/t1") && init?.method === "PATCH") {
        patchCalls += 1;
        expect(JSON.parse(init.body as string)).toEqual({
          assignee_helpdesk_ref: "HD-2",
          reassignment_rationale: "Escalating to a networking specialist.",
        });
        return jsonResponse(ticketDetail({ assignee_helpdesk_ref: "HD-2" }));
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    await userEvent.click(screen.getByRole("button", { name: /reassign/i }));
    const dialog = await screen.findByRole("dialog", { name: /reassign tck-000001/i });
    await userEvent.clear(within(dialog).getByLabelText(/helpdesk specialist ref/i));
    await userEvent.type(within(dialog).getByLabelText(/helpdesk specialist ref/i), "HD-2");
    await userEvent.type(within(dialog).getByLabelText(/rationale/i), "Escalating to a networking specialist.");
    await userEvent.click(within(dialog).getByRole("button", { name: /^reassign$/i }));

    await waitFor(() => expect(patchCalls).toBe(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("HD-2")).toBeInTheDocument();
  });

  it("surfaces a rejected reassignment's ApiError detail instead of swallowing it", async () => {
    // backend/app/tickets/router.py 400s with "no such helpdesk specialist:
    // ..." for an unresolvable ref, even though both fields were supplied --
    // this must reach the user, not be silently dropped.
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_1]);
      if (u.endsWith("/api/tickets/t1") && init?.method === "PATCH") {
        return jsonResponse({ detail: "no such helpdesk specialist: 'HD-999'" }, 400);
      }
      throw new Error(`unexpected call: ${u} ${init?.method}`);
    });

    renderTickets(HELPDESK);
    await screen.findByText("TCK-000001");

    await userEvent.click(screen.getByRole("button", { name: /reassign/i }));
    const dialog = await screen.findByRole("dialog", { name: /reassign tck-000001/i });
    await userEvent.clear(within(dialog).getByLabelText(/helpdesk specialist ref/i));
    await userEvent.type(within(dialog).getByLabelText(/helpdesk specialist ref/i), "HD-999");
    await userEvent.type(within(dialog).getByLabelText(/rationale/i), "Trying an unknown ref.");
    await userEvent.click(within(dialog).getByRole("button", { name: /^reassign$/i }));

    expect(await screen.findByText("no such helpdesk specialist: 'HD-999'")).toBeInTheDocument();
    // The assignee must not have visibly changed -- the row still reflects
    // what the server actually has.
    expect(screen.getByText("HD-1")).toBeInTheDocument();
  });

  it("renders the single ticket at /tickets/:id via GET /api/tickets/{id}, without listing", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets/t1")) return jsonResponse(ticketDetail());
      throw new Error(`unexpected call: ${u}`);
    });

    renderTickets(ADMIN, { route: "/tickets/t1" });

    expect(await screen.findByText("VPN issue")).toBeInTheDocument();
    expect(screen.getByText("The VPN client refuses to connect from home.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/tickets"))).toBe(false);
  });

  it("renders StateBlock's error wording for a ticket outside the caller's scope (404)", async () => {
    // load_readable_ticket returns 404, not 403, for a ticket the caller may
    // not see -- deliberately, so the endpoint never confirms which ids
    // exist. This must render as StateBlock's error state, never an empty
    // table (a failed request must never look like "no data").
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets/t9")) return jsonResponse({ detail: "no such ticket" }, 404);
      throw new Error(`unexpected call: ${u}`);
    });

    renderTickets(EMPLOYEE, { route: "/tickets/t9" });

    expect(await screen.findByRole("alert")).toHaveTextContent("no such ticket");
  });
});

describe("detailPhase", () => {
  it("resolves to loading while the query is loading", () => {
    expect(detailPhase({ isLoading: true, isError: false, data: undefined })).toBe("loading");
  });

  it("resolves to error when the query has errored", () => {
    expect(detailPhase({ isLoading: false, isError: true, data: undefined })).toBe("error");
  });

  it("resolves to data once the query has data", () => {
    expect(detailPhase({ isLoading: false, isError: false, data: { id: "t1" } })).toBe("data");
  });

  it("falls back to loading -- never a blank page -- when not flagged loading or error but data is still absent", () => {
    // The narrow gap this exists for: e.g. a route param change landing
    // between the old query's teardown and the new one's status settling.
    // Nothing about the Tickets.tsx render path may fall through to
    // rendering neither StateBlock nor TicketDetailView.
    expect(detailPhase({ isLoading: false, isError: false, data: undefined })).toBe("loading");
    expect(detailPhase({ isLoading: false, isError: false, data: null })).toBe("loading");
  });
});
