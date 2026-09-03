import type { ReactNode } from "react";
import type { IncidentDossier } from "../api/endpoints/admin";
import { usd, tokens } from "../lib/format";

/**
 * One labelled block inside the dossier card. Every one of the fifteen
 * `IncidentDossier` fields gets exactly one of these -- see the field list
 * in task-10-brief.md -- so the card is a straight enumeration of the
 * schema, not a curated subset.
 */
function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</h4>
      <div className="text-sm text-slate-700">{children}</div>
    </div>
  );
}

/** A list section that still renders something for an empty array, rather
 * than silently collapsing to nothing -- an incident with no risk flags or
 * no open questions is a real, meaningful result, not a rendering gap. */
function ListSection({ label, items, empty, render }: {
  label: string;
  items: unknown[];
  empty: string;
  render: (item: never, index: number) => ReactNode;
}) {
  return (
    <Section label={label}>
      {items.length === 0 ? (
        <p className="italic text-slate-400">{empty}</p>
      ) : (
        <ul className="list-disc space-y-1 pl-5">
          {items.map((item, index) => (
            <li key={index}>{render(item as never, index)}</li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/**
 * Builds a `Blob` + object URL and clicks a throwaway `<a download>` --
 * the brief's specified mechanism for "Download JSON produces the dossier
 * verbatim". The object URL is revoked immediately after the click so it
 * does not leak: the browser has already queued the download by the time
 * `click()` returns synchronously.
 */
function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/**
 * Design spec 5.2 / task-10 brief: "Rendered as a card, section per field,
 * with a Download JSON button." The backend validates this object with
 * `client.messages.parse` before it ever reaches here (app/admin/dossier.py)
 * -- there is no such thing as a partially-valid `IncidentDossier` -- so
 * this component always has a complete object to render and never has to
 * guess at a missing field. A field that legitimately came back as prose
 * like "Not stated in the supplied material" is rendered exactly as given,
 * not specially treated as empty -- this component does no interpretation
 * of the model's own text.
 */
export function DossierCard({ dossier }: { dossier: IncidentDossier }) {
  const { requester, recommended_assignee: assignee, cost_summary: cost } = dossier;

  return (
    <div className="space-y-3 rounded border border-slate-300 bg-white p-4" data-testid="dossier-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-900">Incident dossier — {dossier.ticket_number}</h3>
        <button
          type="button"
          onClick={() => downloadJson(`dossier-${dossier.ticket_number}.json`, dossier)}
          className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          Download JSON
        </button>
      </div>

      <Section label="Problem statement">{dossier.problem_statement}</Section>

      <div className="flex flex-wrap gap-4">
        <Section label="Classification">{dossier.classification}</Section>
        <Section label="Severity">{dossier.severity}</Section>
      </div>

      <Section label="Requester">
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
          <dt className="font-medium text-slate-500">Name</dt>
          <dd>{requester.name}</dd>
          <dt className="font-medium text-slate-500">Role</dt>
          <dd>{requester.role}</dd>
          <dt className="font-medium text-slate-500">Department</dt>
          <dd>{requester.department ?? "—"}</dd>
          <dt className="font-medium text-slate-500">Clearance</dt>
          <dd>{requester.clearance ?? "—"}</dd>
        </dl>
      </Section>

      <ListSection
        label="Timeline"
        items={dossier.timeline}
        empty="No timeline entries."
        render={(entry: IncidentDossier["timeline"][number]) => (
          <>
            <span className="font-mono text-xs text-slate-500">{entry.at}</span> — {entry.what}
          </>
        )}
      />

      <ListSection
        label="Evidence"
        items={dossier.evidence}
        empty="No evidence recorded."
        render={(item: string) => item}
      />

      <ListSection
        label="Knowledge sources"
        items={dossier.knowledge_sources}
        empty="No knowledge sources cited."
        render={(source: IncidentDossier["knowledge_sources"][number]) => (
          <>
            <span className="font-mono text-xs text-slate-500">{source.document_id}</span> — {source.why_it_mattered}
          </>
        )}
      />

      <ListSection
        label="Tools invoked"
        items={dossier.tools_invoked}
        empty="No tools invoked."
        render={(tool: IncidentDossier["tools_invoked"][number]) => (
          <>
            <span className="font-medium">{tool.name}</span> — {tool.summary}
          </>
        )}
      />

      <Section label="Agent reasoning summary">{dossier.agent_reasoning_summary}</Section>

      <Section label="Recommended assignee">
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
          <dt className="font-medium text-slate-500">Helpdesk ref</dt>
          <dd>{assignee.helpdesk_ref}</dd>
          <dt className="font-medium text-slate-500">Specialization</dt>
          <dd>{assignee.specialization}</dd>
          <dt className="font-medium text-slate-500">Rationale</dt>
          <dd>{assignee.rationale}</dd>
        </dl>
      </Section>

      <ListSection
        label="Risk flags"
        items={dossier.risk_flags}
        empty="No risk flags."
        render={(flag: IncidentDossier["risk_flags"][number]) => (
          <>
            <span className="font-medium">{flag.kind}</span> — {flag.detail}
          </>
        )}
      />

      <ListSection
        label="Recommended next actions"
        items={dossier.recommended_next_actions}
        empty="No recommended next actions."
        render={(item: string) => item}
      />

      <ListSection
        label="Open questions"
        items={dossier.open_questions}
        empty="No open questions."
        render={(item: string) => item}
      />

      <Section label="Cost summary">
        {usd(cost.cost_usd)} · {tokens(cost.input_tokens)} input tokens · {tokens(cost.output_tokens)} output tokens
      </Section>
    </div>
  );
}
