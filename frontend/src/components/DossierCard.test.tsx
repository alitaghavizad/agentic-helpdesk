import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DossierCard } from "./DossierCard";
import type { IncidentDossier } from "../api/endpoints/admin";

const FULL_DOSSIER: IncidentDossier = {
  ticket_number: "TCK-000042",
  problem_statement: "The customer cannot access the billing portal after a password reset.",
  classification: "account_access",
  severity: "high",
  requester: {
    name: "Jamie Rivera",
    role: "employee",
    department: "Finance",
    clearance: "standard",
  },
  timeline: [
    { at: "2026-09-01T10:00:00Z", what: "Ticket opened by requester." },
    { at: "2026-09-01T10:05:00Z", what: "Agent triaged and matched to billing specialization." },
  ],
  evidence: ["Login attempt logs show three consecutive 401s.", "Password reset email was delivered."],
  knowledge_sources: [
    { document_id: "KB-100", why_it_mattered: "Documents the portal's SSO reset flow." },
    { document_id: "KB-204", why_it_mattered: "Lists known lockout causes." },
  ],
  tools_invoked: [
    { name: "search_knowledge_base", summary: "Searched for billing portal lockout causes." },
    { name: "lookup_account", summary: "Looked up the requester's account status." },
  ],
  agent_reasoning_summary: "The agent matched the symptoms to a known SSO propagation delay.",
  recommended_assignee: {
    helpdesk_ref: "hd-42",
    specialization: "billing_systems",
    rationale: "Has resolved similar SSO propagation incidents before.",
  },
  risk_flags: [{ kind: "repeat_contact", detail: "This is the requester's second contact this week." }],
  recommended_next_actions: ["Manually re-sync the SSO session.", "Confirm access with the requester."],
  open_questions: ["Whether the requester also lost access to the mobile app."],
  cost_summary: { cost_usd: 0.0421, input_tokens: 5230, output_tokens: 812 },
};

describe("DossierCard", () => {
  it("renders all fifteen IncidentDossier fields", () => {
    render(<DossierCard dossier={FULL_DOSSIER} />);

    // 1. ticket_number
    expect(screen.getByText(/TCK-000042/)).toBeInTheDocument();
    // 2. problem_statement
    expect(screen.getByText(/cannot access the billing portal/)).toBeInTheDocument();
    // 3. classification
    expect(screen.getByText("account_access")).toBeInTheDocument();
    // 4. severity
    expect(screen.getByText("high")).toBeInTheDocument();
    // 5. requester {name, role, department, clearance}
    expect(screen.getByText("Jamie Rivera")).toBeInTheDocument();
    expect(screen.getByText("employee")).toBeInTheDocument();
    expect(screen.getByText("Finance")).toBeInTheDocument();
    expect(screen.getByText("standard")).toBeInTheDocument();
    // 6. timeline[]
    expect(screen.getByText(/Ticket opened by requester/)).toBeInTheDocument();
    expect(screen.getByText(/Agent triaged and matched/)).toBeInTheDocument();
    // 7. evidence[]
    expect(screen.getByText(/three consecutive 401s/)).toBeInTheDocument();
    expect(screen.getByText(/Password reset email was delivered/)).toBeInTheDocument();
    // 8. knowledge_sources[]{document_id, why_it_mattered}
    expect(screen.getByText(/KB-100/)).toBeInTheDocument();
    expect(screen.getByText(/Documents the portal's SSO reset flow/)).toBeInTheDocument();
    expect(screen.getByText(/KB-204/)).toBeInTheDocument();
    // 9. tools_invoked[]
    expect(screen.getByText(/search_knowledge_base/)).toBeInTheDocument();
    expect(screen.getByText(/Looked up the requester's account status/)).toBeInTheDocument();
    // 10. agent_reasoning_summary
    expect(screen.getByText(/SSO propagation delay/)).toBeInTheDocument();
    // 11. recommended_assignee
    expect(screen.getByText("hd-42")).toBeInTheDocument();
    expect(screen.getByText("billing_systems")).toBeInTheDocument();
    expect(screen.getByText(/resolved similar SSO propagation incidents/)).toBeInTheDocument();
    // 12. risk_flags[]
    expect(screen.getByText("repeat_contact")).toBeInTheDocument();
    expect(screen.getByText(/second contact this week/)).toBeInTheDocument();
    // 13. recommended_next_actions[]
    expect(screen.getByText(/re-sync the SSO session/)).toBeInTheDocument();
    expect(screen.getByText(/Confirm access with the requester/)).toBeInTheDocument();
    // 14. open_questions[]
    expect(screen.getByText(/lost access to the mobile app/)).toBeInTheDocument();
    // 15. cost_summary
    expect(screen.getByText(/\$0\.042100/)).toBeInTheDocument();
    expect(screen.getByText(/5,230/)).toBeInTheDocument();
    expect(screen.getByText(/812/)).toBeInTheDocument();
  });

  it("renders a 'say so rather than guessing' prose value as-is, not as a missing field", () => {
    const dossier: IncidentDossier = {
      ...FULL_DOSSIER,
      requester: { ...FULL_DOSSIER.requester, department: "Not stated in the supplied material" },
    };
    render(<DossierCard dossier={dossier} />);

    expect(screen.getByText("Not stated in the supplied material")).toBeInTheDocument();
    // Must not have been swallowed into the generic "—" empty-field marker.
    expect(screen.queryByText("—", { selector: "dd" })).not.toBeInTheDocument();
  });

  it("still renders a meaningful section for an empty list field, rather than nothing", () => {
    const dossier: IncidentDossier = { ...FULL_DOSSIER, risk_flags: [], open_questions: [] };
    render(<DossierCard dossier={dossier} />);

    expect(screen.getByText("No risk flags.")).toBeInTheDocument();
    expect(screen.getByText("No open questions.")).toBeInTheDocument();
  });

  describe("Download JSON", () => {
    const createObjectURL = vi.fn((_blob: Blob) => "blob:mock-url");
    const revokeObjectURL = vi.fn();

    beforeEach(() => {
      createObjectURL.mockClear();
      revokeObjectURL.mockClear();
      vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL }));
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("produces the dossier verbatim as the downloaded blob, then revokes the object URL", async () => {
      const user = userEvent.setup();
      render(<DossierCard dossier={FULL_DOSSIER} />);

      await user.click(screen.getByRole("button", { name: /download json/i }));

      expect(createObjectURL).toHaveBeenCalledTimes(1);
      const blob = createObjectURL.mock.calls[0][0] as Blob;
      const text = await blob.text();
      expect(JSON.parse(text)).toEqual(FULL_DOSSIER);

      expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    });
  });
});
