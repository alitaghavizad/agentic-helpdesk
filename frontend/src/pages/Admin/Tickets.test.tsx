import { act, render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Tickets } from "./Tickets";
import type { IncidentDossier } from "../../api/endpoints/admin";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const TICKET_OPEN = {
  id: "t1",
  ticket_number: "TCK-000001",
  title: "Cannot log in to the billing portal",
  status: "open",
  priority: "high",
  assignee_helpdesk_ref: "hd-1",
  created_at: "2026-09-01T00:00:00Z",
};

const TICKET_ESCALATED = {
  id: "t2",
  ticket_number: "TCK-000002",
  title: "Possible data loss reported",
  status: "escalated",
  priority: "urgent",
  assignee_helpdesk_ref: "hd-2",
  created_at: "2026-09-01T01:00:00Z",
};

const TICKET_RESOLVED = {
  id: "t3",
  ticket_number: "TCK-000003",
  title: "Password reset request",
  status: "resolved",
  priority: "low",
  assignee_helpdesk_ref: "hd-3",
  created_at: "2026-09-01T02:00:00Z",
};

function detailFor(summary: typeof TICKET_OPEN, overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ...summary,
    body: "Full ticket body.",
    matched_specialization: "account_access",
    assignment_rationale: "Best available match for account-lockout incidents.",
    assignment_score: 0.87,
    resolution: null,
    resolved_at: null,
    ...overrides,
  };
}

const FULL_DOSSIER: IncidentDossier = {
  ticket_number: "TCK-000001",
  problem_statement: "The customer cannot access the billing portal after a password reset.",
  classification: "account_access",
  severity: "high",
  requester: { name: "Jamie Rivera", role: "employee", department: "Finance", clearance: "standard" },
  timeline: [{ at: "2026-09-01T10:00:00Z", what: "Ticket opened by requester." }],
  evidence: ["Login attempt logs show three consecutive 401s."],
  knowledge_sources: [{ document_id: "KB-100", why_it_mattered: "Documents the portal's SSO reset flow." }],
  tools_invoked: [{ name: "search_knowledge_base", summary: "Searched for lockout causes." }],
  agent_reasoning_summary: "The agent matched the symptoms to a known SSO propagation delay.",
  recommended_assignee: {
    helpdesk_ref: "hd-42",
    specialization: "billing_systems",
    rationale: "Has resolved similar incidents before.",
  },
  risk_flags: [{ kind: "repeat_contact", detail: "Second contact this week." }],
  recommended_next_actions: ["Manually re-sync the SSO session."],
  open_questions: ["Whether the requester also lost mobile access."],
  cost_summary: { cost_usd: 0.0421, input_tokens: 5230, output_tokens: 812 },
};

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/tickets"]}>
        <Tickets />
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

describe("Admin Tickets board", () => {
  it("renders one column per TicketStatus, grouping tickets under the right column", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_OPEN, TICKET_ESCALATED, TICKET_RESOLVED]);
      if (u.endsWith(`/api/tickets/${TICKET_OPEN.id}`)) return jsonResponse(detailFor(TICKET_OPEN));
      if (u.endsWith(`/api/tickets/${TICKET_ESCALATED.id}`)) return jsonResponse(detailFor(TICKET_ESCALATED));
      if (u.endsWith(`/api/tickets/${TICKET_RESOLVED.id}`)) return jsonResponse(detailFor(TICKET_RESOLVED));
      throw new Error(`unexpected call: ${u}`);
    });

    renderBoard();

    // All six status columns are present, not just the ones with tickets.
    expect(await screen.findByText(/^Open \(1\)$/)).toBeInTheDocument();
    expect(screen.getByText(/^Assigned \(0\)$/)).toBeInTheDocument();
    expect(screen.getByText(/^In progress \(0\)$/)).toBeInTheDocument();
    expect(screen.getByText(/^Resolved \(1\)$/)).toBeInTheDocument();
    expect(screen.getByText(/^Closed \(0\)$/)).toBeInTheDocument();
    expect(screen.getByText(/^Escalated \(1\)$/)).toBeInTheDocument();

    // Each ticket appears once, under its own status.
    expect(screen.getByText("Cannot log in to the billing portal")).toBeInTheDocument();
    expect(screen.getByText("Possible data loss reported")).toBeInTheDocument();
    expect(screen.getByText("Password reset request")).toBeInTheDocument();
  });

  it("shows assignee, matched specialization, assignment rationale and assignment score on each card", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_OPEN]);
      if (u.endsWith(`/api/tickets/${TICKET_OPEN.id}`)) {
        return jsonResponse(
          detailFor(TICKET_OPEN, {
            matched_specialization: "account_access",
            assignment_rationale: "Best available match for account-lockout incidents.",
            assignment_score: 0.87,
          }),
        );
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderBoard();

    expect(await screen.findByText("Assignee: hd-1")).toBeInTheDocument();
    expect(await screen.findByText("account_access")).toBeInTheDocument();
    expect(screen.getByText("Best available match for account-lockout incidents.")).toBeInTheDocument();
    expect(screen.getByText("0.87")).toBeInTheDocument();
  });

  it("enters a pending state on Generate dossier and stays pending through a long-running call, with no client-side timeout", async () => {
    let resolveDossier!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_OPEN]);
      if (u.endsWith(`/api/tickets/${TICKET_OPEN.id}`)) return jsonResponse(detailFor(TICKET_OPEN));
      if (u.endsWith(`/api/admin/tickets/${TICKET_OPEN.id}/dossier`)) {
        return new Promise<Response>((resolve) => {
          resolveDossier = resolve;
        });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderBoard();
    const button = await screen.findByRole("button", { name: "Generate dossier" });

    // Fake timers are installed BEFORE the click, with shouldAdvanceTime so
    // real async plumbing (React Testing Library's own polling, promise
    // microtasks) keeps working -- and, critically, so that ANY setTimeout
    // the click's own code path schedules (e.g. a hypothetical client-side
    // timeout wrapping the mutation) is scheduled under this same fake
    // clock, not real time. Installing fake timers only after the click
    // would let such a timeout run on the real clock instead, where this
    // test's later virtual time-jump can never reach it -- silently
    // proving nothing.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      fireEvent.click(button);

      expect(await screen.findByRole("status")).toHaveTextContent(/Generating dossier…/);
      expect(screen.getByRole("button", { name: "Generating dossier…" })).toBeDisabled();

      // The real backend call was measured at 36.5s. Advance well past that
      // with the promise still unresolved -- a test that never advances
      // time proves nothing about whether some client-side timeout would
      // have fired. It must still be pending, not errored, after this.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(45_000);
      });

      expect(screen.getByRole("status")).toHaveTextContent(/Generating dossier…/);
      expect(screen.getByRole("button", { name: "Generating dossier…" })).toBeDisabled();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      // Now let the server actually answer.
      resolveDossier(jsonResponse(FULL_DOSSIER));

      await waitFor(() => {
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
      });
      expect(screen.getByText(/Incident dossier — TCK-000001/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders the returned dossier's sections on the card that generated it", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_OPEN]);
      if (u.endsWith(`/api/tickets/${TICKET_OPEN.id}`)) return jsonResponse(detailFor(TICKET_OPEN));
      if (u.endsWith(`/api/admin/tickets/${TICKET_OPEN.id}/dossier`)) return jsonResponse(FULL_DOSSIER);
      throw new Error(`unexpected call: ${u}`);
    });
    const user = userEvent.setup();

    renderBoard();
    await user.click(await screen.findByRole("button", { name: "Generate dossier" }));

    expect(await screen.findByText(/Incident dossier — TCK-000001/)).toBeInTheDocument();
    expect(screen.getByText(/cannot access the billing portal after a password reset/)).toBeInTheDocument();
    // "account_access" appears both as the routing decision's matched
    // specialization and the dossier's own classification -- assert there
    // is at least one match rather than a brittle unique-text lookup.
    expect(screen.getAllByText("account_access").length).toBeGreaterThan(0);
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("Jamie Rivera")).toBeInTheDocument();
    expect(screen.getByText(/Ticket opened by requester/)).toBeInTheDocument();
    expect(screen.getByText(/three consecutive 401s/)).toBeInTheDocument();
    expect(screen.getByText(/KB-100/)).toBeInTheDocument();
    expect(screen.getByText(/search_knowledge_base/)).toBeInTheDocument();
    expect(screen.getByText(/SSO propagation delay/)).toBeInTheDocument();
    expect(screen.getByText("hd-42")).toBeInTheDocument();
    expect(screen.getByText("repeat_contact")).toBeInTheDocument();
    expect(screen.getByText(/re-sync the SSO session/)).toBeInTheDocument();
    expect(screen.getByText(/lost mobile access/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.042100/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download json/i })).toBeInTheDocument();
  });

  it("renders the server's error and no partial dossier card when the dossier call fails", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_OPEN]);
      if (u.endsWith(`/api/tickets/${TICKET_OPEN.id}`)) return jsonResponse(detailFor(TICKET_OPEN));
      if (u.endsWith(`/api/admin/tickets/${TICKET_OPEN.id}/dossier`)) {
        return jsonResponse({ detail: "The model response failed dossier schema validation." }, 502);
      }
      throw new Error(`unexpected call: ${u}`);
    });
    const user = userEvent.setup();

    renderBoard();
    await user.click(await screen.findByRole("button", { name: "Generate dossier" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The model response failed dossier schema validation.",
    );

    // No partial card: none of the dossier's own content or structure may
    // appear. Assert the absence, not merely the presence of the error --
    // a component that renders half a dossier alongside an error message
    // would still pass a check that only looked for the alert.
    expect(screen.queryByTestId("dossier-card")).not.toBeInTheDocument();
    expect(screen.queryByText(/Incident dossier/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download json/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate dossier" })).toBeInTheDocument();
  });

  it("does not leave a previous successful dossier showing if a regeneration attempt fails", async () => {
    let dossierCallCount = 0;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([TICKET_OPEN]);
      if (u.endsWith(`/api/tickets/${TICKET_OPEN.id}`)) return jsonResponse(detailFor(TICKET_OPEN));
      if (u.endsWith(`/api/admin/tickets/${TICKET_OPEN.id}/dossier`)) {
        dossierCallCount += 1;
        if (dossierCallCount === 1) return jsonResponse(FULL_DOSSIER);
        return jsonResponse({ detail: "Model call failed on retry." }, 502);
      }
      throw new Error(`unexpected call: ${u}`);
    });
    const user = userEvent.setup();

    renderBoard();
    await user.click(await screen.findByRole("button", { name: "Generate dossier" }));
    expect(await screen.findByText(/Incident dossier — TCK-000001/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Regenerate dossier" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Model call failed on retry.");
    expect(screen.queryByText(/Incident dossier/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("dossier-card")).not.toBeInTheDocument();
  });

  it("shows a loading state before the tickets list arrives", async () => {
    let resolveList!: (value: Response) => void;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) {
        return new Promise<Response>((resolve) => {
          resolveList = resolve;
        });
      }
      throw new Error(`unexpected call: ${u}`);
    });

    renderBoard();

    expect(await screen.findByRole("status")).toHaveTextContent(/loading/i);
    resolveList(jsonResponse([]));
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("renders a failed tickets fetch as an error state, never as an empty board", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse({ detail: "Forbidden: admin role required" }, 403);
      throw new Error(`unexpected call: ${u}`);
    });

    renderBoard();

    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access to this.");
    expect(screen.queryByText(/^Open \(/)).not.toBeInTheDocument();
  });

  it("renders an empty board distinguishably from a failed one", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/api/tickets")) return jsonResponse([]);
      throw new Error(`unexpected call: ${u}`);
    });

    renderBoard();

    expect(await screen.findByText("No tickets to show.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
