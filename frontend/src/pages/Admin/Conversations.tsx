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
import { dateTime, duration, tokens, usd } from "../../lib/format";

function conversationsQueryKey(q: string) {
  return ["admin", "conversations", q] as const;
}

function conversationDetailKey(id: string) {
  return ["admin", "conversation", id] as const;
}

function traceQueryKey(runId: string) {
  return ["admin", "conversation-trace", runId] as const;
}

const STATUS_TONE: Record<string, BadgeTone> = {
  open: "info",
  active: "info",
  closed: "neutral",
  resolved: "success",
  error: "danger",
};

/**
 * `Conversation.user_id` XOR `guest_name`+`guest_email` (a DB CHECK
 * constraint enforces exactly one -- backend/app/db/models.py), so this
 * never has to guess which branch applies. `ConversationSummary` carries no
 * username -- only `user_id` -- because `GET /api/admin/conversations`
 * never joins `users` into its response shape (only the search query does,
 * server-side, to match against it); showing the id itself is the only
 * honest option here rather than inventing a name the API never sent.
 */
function participantLabel(row: ConversationSummary): string {
  if (row.user_id) return `User ${row.user_id}`;
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
        <Badge tone={STATUS_TONE[run.status] ?? "neutral"}>{run.status}</Badge>
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
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);
  const [selectedRunId, setSelectedRunId] = useState<string | undefined>(undefined);

  const listQuery = useQuery({
    queryKey: conversationsQueryKey(q),
    queryFn: () => admin.adminConversations({ q: q || undefined, limit: 50 }),
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

  const columns: Column<ConversationSummary>[] = [
    {
      key: "title",
      header: "Title",
      render: (row) => <ConversationRow conversation={row} selected={row.id === selectedId} onSelect={selectConversation} />,
    },
    { key: "participant", header: "Participant", render: (row) => participantLabel(row) },
    { key: "status", header: "Status", render: (row) => <Badge tone={STATUS_TONE[row.status] ?? "neutral"}>{row.status}</Badge> },
    { key: "created", header: "Created", render: (row) => dateTime(row.created_at) },
  ];

  const rows = listQuery.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-lg font-semibold text-slate-900">Conversations</h1>
        <input
          type="search"
          value={q}
          onChange={(event) => setQ(event.target.value)}
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
        <Table columns={columns} rows={rows} rowKey={(row) => row.id} />
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
