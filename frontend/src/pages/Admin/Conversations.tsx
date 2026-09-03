import { useState } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import type { ConversationSummary, RunSummary } from "../../api/endpoints/admin";
import type { MessageView } from "../../api/endpoints/chat";
import { StateBlock, describeError } from "../../components/StateBlock";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { Badge } from "../../components/Badge";
import type { BadgeTone } from "../../components/Badge";
import { SpanTree } from "../../components/SpanTree";
import { Pager } from "../../components/Pager";
import { dateTime, duration, tokens, usd } from "../../lib/format";
import { RUN_STATUS_TONE } from "../../lib/runStatus";

function conversationsQueryKey(q: string, offset: number) {
  return ["admin", "conversations", q, offset] as const;
}

function conversationDetailKey(id: string) {
  return ["admin", "conversation", id] as const;
}

function traceQueryKey(runId: string) {
  return ["admin", "conversation-trace", runId] as const;
}

// `ConversationStatus` (backend/app/db/models.py) only ever carries
// `active`/`closed` -- `open` and `resolved` are dead keys here (they
// belong to ticket status, not conversation status). Run status is a
// wholly different enum (`RunStatus`: running/ok/error/aborted) and lives
// in the shared `RUN_STATUS_TONE` map instead; the two used to be
// conflated in one map, which happened to share the `error` key but
// rendered a running/ok run as neutral grey -- indistinguishable from a
// finished one.
const CONVERSATION_STATUS_TONE: Record<string, BadgeTone> = {
  active: "info",
  closed: "neutral",
};

/**
 * `Conversation.user_id` XOR `guest_name`+`guest_email` (a DB CHECK
 * constraint enforces exactly one -- backend/app/db/models.py), so this
 * never has to guess which branch applies.
 *
 * `ConversationSummary` now carries `full_name`/`username` for the
 * `user_id` case (backend/app/admin/queries.py's `_conversations_with_
 * participant` -- the same OUTER JOIN `GET /api/admin/conversations?q=`
 * already used to MATCH a username/full_name, just also selected into the
 * response). Without this, an admin who searched "jamie" and got a row
 * back had no way to see that "jamie" was what matched -- the id alone
 * doesn't tell you why the row is here. `full_name` wins when both are
 * present (it is what a person recognises); the id is the last resort,
 * for the edge case of a `user_id` whose `users` row cannot be joined
 * (a race with account deletion, not a case the schema forbids outright).
 */
function participantLabel(row: ConversationSummary): string {
  if (row.user_id) return row.full_name ?? row.username ?? `User ${row.user_id}`;
  if (row.guest_name && row.guest_email) return `${row.guest_name} <${row.guest_email}>`;
  if (row.guest_name) return row.guest_name;
  if (row.guest_email) return row.guest_email;
  return "—";
}

/**
 * `MessageView.content` is the stored Anthropic content-block list, not a
 * string (backend/app/chat/schemas.py) -- it can hold text, image and
 * tool-result blocks, and possibly a kind this admin screen has never seen
 * before. Text renders as text; every other recognised kind gets a plain
 * label instead of its payload (this is a transcript, not a span
 * inspector); and anything else falls through to the same label keyed off
 * whatever `type` it carries, so an unrecognised block never throws.
 */
function renderContentBlock(block: unknown, key: number): ReactNode {
  if (typeof block === "string") {
    return (
      <p key={key} className="whitespace-pre-wrap text-sm text-slate-800">
        {block}
      </p>
    );
  }
  if (block && typeof block === "object") {
    const record = block as Record<string, unknown>;
    if (typeof record.text === "string") {
      return (
        <p key={key} className="whitespace-pre-wrap text-sm text-slate-800">
          {record.text}
        </p>
      );
    }
    const kind = typeof record.type === "string" ? record.type : "block";
    return (
      <p key={key} className="text-sm italic text-slate-500">
        [{kind}]
      </p>
    );
  }
  return (
    <p key={key} className="text-sm italic text-slate-500">
      [block]
    </p>
  );
}

function MessageBubble({ message }: { message: MessageView }) {
  const blocks = Array.isArray(message.content) ? message.content : [message.content];
  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <div className="mb-1 flex items-center justify-between text-xs">
        <Badge tone={message.role === "assistant" ? "info" : "neutral"}>{message.role}</Badge>
        <span className="text-slate-500">{dateTime(message.created_at)}</span>
      </div>
      <div className="space-y-1">
        {blocks.map((block, index) => renderContentBlock(block, index))}
      </div>
    </div>
  );
}

function RunRow({ run, selected, onSelect }: { run: RunSummary; selected: boolean; onSelect: (id: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(run.id)}
      aria-current={selected}
      className={`w-full rounded border px-2 py-1.5 text-left text-xs ${
        selected ? "border-slate-400 bg-slate-100" : "border-slate-200 bg-white hover:bg-slate-50"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-slate-800">{run.id}</span>
        <Badge tone={RUN_STATUS_TONE[run.status] ?? "neutral"}>{run.status}</Badge>
      </div>
      <div className="mt-1 flex items-center gap-2 text-slate-500">
        <span>{run.trigger}</span>
        <span>{duration(run.duration_ms)}</span>
        <span>{usd(run.cost_usd)}</span>
      </div>
    </button>
  );
}

function ConversationRow({
  conversation, selected, onSelect,
}: {
  conversation: ConversationSummary;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(conversation.id)}
      aria-current={selected}
      className={`rounded px-1.5 py-0.5 text-left text-xs underline ${selected ? "text-slate-900" : "text-blue-700"}`}
    >
      {conversation.title ?? "(untitled)"}
    </button>
  );
}

/**
 * Spec 15's Conversations screen, plus §15's requirement (the reason task 0
 * added `GET /api/admin/conversations/{id}`) that the transcript render
 * beside the span tree of a selected run: two columns, transcript on the
 * left, the conversation's own runs plus `<SpanTree>` on the right.
 */
export function Conversations() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);
  const [selectedRunId, setSelectedRunId] = useState<string | undefined>(undefined);

  const listQuery = useQuery({
    queryKey: conversationsQueryKey(q, offset),
    queryFn: () => admin.adminConversations({ q: q || undefined, limit: 50, offset }),
  });

  const detailQuery = useQuery({
    queryKey: conversationDetailKey(selectedId ?? ""),
    queryFn: () => admin.adminConversationDetail(selectedId as string),
    enabled: selectedId !== undefined,
  });

  const traceQuery = useQuery({
    queryKey: traceQueryKey(selectedRunId ?? ""),
    queryFn: () => admin.adminTrace(selectedRunId as string),
    enabled: selectedRunId !== undefined,
  });

  function selectConversation(id: string) {
    setSelectedId(id);
    // A run selected under the previous conversation has no meaning here --
    // leaving it set would either show a stale trace or silently query a
    // run id that belongs to a different conversation entirely.
    setSelectedRunId(undefined);
  }

  function handleSearchChange(value: string) {
    setQ(value);
    // A new search term can filter the previously-selected conversation
    // right out of the list -- leaving the detail panel open would show a
    // transcript and run list for a row the admin can no longer even see
    // above it, with no way to tell it is now orphaned from the filter.
    setSelectedId(undefined);
    setSelectedRunId(undefined);
    // A new search term also invalidates whatever page of results the old
    // term was showing -- staying on, say, offset=50 could land on a page
    // past the end of the new, usually much smaller, result set.
    setOffset(0);
  }

  const columns: Column<ConversationSummary>[] = [
    {
      key: "title",
      header: "Title",
      render: (row) => <ConversationRow conversation={row} selected={row.id === selectedId} onSelect={selectConversation} />,
    },
    { key: "participant", header: "Participant", render: (row) => participantLabel(row) },
    { key: "status", header: "Status", render: (row) => <Badge tone={CONVERSATION_STATUS_TONE[row.status] ?? "neutral"}>{row.status}</Badge> },
    { key: "created", header: "Created", render: (row) => dateTime(row.created_at) },
  ];

  const page = listQuery.data;
  const rows = page?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-lg font-semibold text-slate-900">Conversations</h1>
        <input
          type="search"
          value={q}
          onChange={(event) => handleSearchChange(event.target.value)}
          placeholder="Search by title or participant"
          aria-label="Search conversations"
          className="w-72 rounded border border-slate-300 px-2 py-1 text-sm"
        />
      </div>

      {listQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : listQuery.isError ? (
        <StateBlock status="error" message={describeError(listQuery.error)} />
      ) : rows.length === 0 ? (
        <StateBlock status="empty" emptyLabel="No conversations recorded yet." />
      ) : (
        <>
          <Table columns={columns} rows={rows} rowKey={(row) => row.id} />
          {page && <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />}
        </>
      )}

      {selectedId !== undefined && (
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded border border-slate-200 bg-white p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Transcript</h2>
            {detailQuery.isLoading ? (
              <StateBlock status="loading" />
            ) : detailQuery.isError ? (
              <StateBlock status="error" message={describeError(detailQuery.error)} />
            ) : !detailQuery.data ? (
              <StateBlock status="loading" />
            ) : detailQuery.data.messages.length === 0 ? (
              <StateBlock status="empty" emptyLabel="No messages recorded for this conversation." />
            ) : (
              <div className="space-y-2">
                {detailQuery.data.messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
              </div>
            )}
          </div>

          <div className="rounded border border-slate-200 bg-white p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Runs</h2>
            {detailQuery.isLoading ? (
              <StateBlock status="loading" />
            ) : detailQuery.isError ? (
              <StateBlock status="error" message={describeError(detailQuery.error)} />
            ) : !detailQuery.data ? (
              <StateBlock status="loading" />
            ) : detailQuery.data.runs.length === 0 ? (
              // Explicit rather than an empty panel: a conversation that
              // never triggered the agent (a guest who only browsed, or a
              // thread closed before any turn ran) is a normal outcome, not
              // a loading gap or a fetch failure.
              <p className="rounded border border-dashed border-slate-200 p-4 text-center text-sm text-slate-500">
                No runs recorded for this conversation.
              </p>
            ) : (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  {detailQuery.data.runs.map((run) => (
                    <RunRow key={run.id} run={run} selected={run.id === selectedRunId} onSelect={setSelectedRunId} />
                  ))}
                </div>

                {selectedRunId !== undefined && (
                  <div className="border-t border-slate-100 pt-3">
                    {traceQuery.isLoading ? (
                      <StateBlock status="loading" />
                    ) : traceQuery.isError ? (
                      <StateBlock status="error" message={describeError(traceQuery.error)} />
                    ) : !traceQuery.data ? (
                      <StateBlock status="loading" />
                    ) : (
                      <>
                        {traceQuery.data.truncated && (
                          <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">
                            This trace was truncated: only {tokens(traceQuery.data.span_count)} span
                            {traceQuery.data.span_count === 1 ? "" : "s"} of the full run are shown below.
                          </p>
                        )}
                        <SpanTree roots={traceQuery.data.roots} totalMs={traceQuery.data.run.duration_ms ?? 0} />
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
