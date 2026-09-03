import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import * as chat from "../api/endpoints/chat";
import type { Conversation, MessageView } from "../api/endpoints/chat";
import { conversationQueryKey, conversationsQueryKey, useChatTurn } from "../hooks/useChatTurn";
import type { Outcome, ToolRow } from "../hooks/turnReducer";
import { StateBlock, describeError } from "../components/StateBlock";
import { dateTime } from "../lib/format";

/**
 * `content` is the stored Anthropic content-block list exactly as
 * exchanged (backend/app/chat/schemas.py's `MessageView`), or a plain
 * string for the rare row that isn't. Only text blocks render -- image/tool
 * blocks have no place in a transcript bubble.
 */
function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (block && typeof block === "object" && typeof (block as Record<string, unknown>).text === "string") {
          return (block as Record<string, unknown>).text as string;
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function outcomeLabel(outcome: Outcome): { text: string; to?: string } {
  switch (outcome.type) {
    case "ticket_created": {
      // /tickets/:id is now a real route (task 5's Tickets page), so this
      // links straight to the created ticket instead of the list -- falling
      // back to the list only if the frame is somehow missing its id.
      const ticketId = outcome.data.ticket_id;
      const to = typeof ticketId === "string" && ticketId.length > 0 ? `/tickets/${ticketId}` : "/tickets";
      return { text: `Ticket ${outcome.data.ticket_number ?? ""} created`, to };
    }
    case "approval_requested":
      return { text: `Approval ${outcome.data.request_number ?? ""} requested` };
    case "task_recorded":
      return { text: `Task recorded: ${outcome.data.title ?? ""}` };
    case "attachment_request":
      return { text: `Attachment needed: ${outcome.data.reason ?? ""}` };
    default:
      return { text: "" };
  }
}

function ToolRowView({ tool }: { tool: ToolRow }) {
  const label = tool.status === "running" ? "running" : tool.status === "ok" ? "done" : "failed";
  return (
    <li className="text-xs text-slate-500">
      used <span className="font-medium text-slate-700">{tool.name}</span> ({label})
    </li>
  );
}

function ConversationList({
  conversations, selectedId, onSelect,
}: {
  conversations: Conversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (conversations.length === 0) {
    return <StateBlock status="empty" emptyLabel="No conversations yet. Start one below." />;
  }
  return (
    <ul className="space-y-1">
      {conversations.map((conversation) => (
        <li key={conversation.id}>
          <button
            type="button"
            onClick={() => onSelect(conversation.id)}
            aria-current={conversation.id === selectedId}
            className={`w-full rounded px-3 py-2 text-left text-sm ${
              conversation.id === selectedId ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
            }`}
          >
            {conversation.title ?? "Untitled conversation"}
          </button>
        </li>
      ))}
    </ul>
  );
}

export function Chat() {
  const { principal } = useAuth();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [attachmentName, setAttachmentName] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const conversationsQuery = useQuery({ queryKey: conversationsQueryKey, queryFn: chat.listConversations });
  const conversationQuery = useQuery({
    queryKey: conversationQueryKey(selectedId ?? ""),
    queryFn: () => chat.getConversation(selectedId as string),
    enabled: selectedId !== null,
  });

  const { turn, busy, send, reset } = useChatTurn(selectedId);
  const [pendingUserContent, setPendingUserContent] = useState<string | null>(null);
  // The message ids present the moment a send started, captured so the
  // optimistic user bubble can tell "a NEW user message landed" apart from
  // "the transcript I already knew about got refetched." Not state: writing
  // it must never itself trigger a render.
  const priorMessageIdsRef = useRef<Set<string>>(new Set());

  // Switching conversations must not leave a previous conversation's live
  // turn (or its optimistic user bubble) bleeding into the newly selected
  // one's transcript panel.
  useEffect(() => {
    reset();
    setPendingUserContent(null);
    priorMessageIdsRef.current = new Set();
  }, [selectedId, reset]);

  const messages: MessageView[] = conversationQuery.data?.messages ?? [];

  // Whether THIS turn's answer is already in the refetched transcript --
  // computed straight from data, not from a timestamp. An earlier version
  // gated this on `conversationQuery.dataUpdatedAt` advancing past a
  // timestamp recorded from an effect; when the invalidated refetch
  // resolved before React committed the `done` render, that comparison was
  // never satisfied and the live bubble (answer + trace link) rendered
  // forever alongside the now-identical stored message. Reading the
  // transcript directly has no such window: it is simply true or false on
  // every render, synchronously, from whatever `messages` currently holds.
  const turnPersisted = turn.done && turn.runId !== null
    && messages.some((message) => message.run_id === turn.runId);

  // Whether the OPTIMISTIC USER bubble's own message is now in the stored
  // transcript. Deliberately NOT gated on `turnPersisted`: the backend
  // commits the user's message synchronously, before the turn even starts
  // running (backend/app/chat/router.py's send_message_endpoint stages and
  // commits it up front) -- long before the assistant's answer is
  // persisted, and TanStack Query's default `refetchOnWindowFocus` means the
  // transcript can refetch and pick up that user message mid-turn, well
  // before `turnPersisted` ever becomes true. Gating on `turnPersisted` left
  // a window where the transcript already had the real user message AND the
  // optimistic bubble was still showing it -- rendered twice. Comparing
  // against the message ids captured right before this send started (rather
  // than matching on content, which a duplicate could coincidentally share)
  // is what makes this correct regardless of when or how many times a
  // refetch lands.
  const userMessagePersisted = pendingUserContent !== null
    && messages.some((message) => message.role === "user" && !priorMessageIdsRef.current.has(message.id));

  // Once the user's own message is persisted, the optimistic bubble showing
  // it is no longer needed.
  useEffect(() => {
    if (userMessagePersisted) setPendingUserContent(null);
  }, [userMessagePersisted]);

  async function handleNewConversation() {
    const created = await chat.createConversation();
    await queryClient.invalidateQueries({ queryKey: conversationsQueryKey });
    setSelectedId(created.id);
  }

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || !selectedId || busy) return;
    setDraft("");
    // Optimistic: the composer clears immediately, but a turn can run for
    // tens of seconds, and the user's own question should not vanish from
    // the transcript for that whole window just because the stored
    // transcript hasn't been refetched yet. Snapshot which message ids
    // already exist so `userMessagePersisted` can recognise the real one
    // landing, however soon a refetch brings it in.
    priorMessageIdsRef.current = new Set(messages.map((message) => message.id));
    setPendingUserContent(content);
    await send(content);
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !selectedId) return;
    setAttachmentError(null);
    setUploading(true);
    try {
      const uploaded = await chat.uploadAttachment(selectedId, file);
      setAttachmentName(uploaded.filename);
    } catch (err) {
      setAttachmentError(err instanceof ApiError ? err.detail : "Upload failed. Please try again.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  const showLiveTurn = !turnPersisted
    && (busy || turn.text.length > 0 || turn.tools.length > 0 || turn.outcomes.length > 0 || turn.error !== null);
  const showPendingUser = pendingUserContent !== null && !userMessagePersisted;

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[16rem_1fr]">
      <aside className="space-y-3">
        <button
          type="button"
          onClick={handleNewConversation}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          New conversation
        </button>
        {conversationsQuery.isLoading ? (
          <StateBlock status="loading" />
        ) : conversationsQuery.isError ? (
          <StateBlock status="error" message={describeError(conversationsQuery.error)} />
        ) : (
          <ConversationList
            conversations={conversationsQuery.data ?? []}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        )}
      </aside>

      <section className="flex min-h-[28rem] flex-col rounded border border-slate-200 bg-white">
        {selectedId === null ? (
          <div className="flex flex-1 items-center justify-center">
            <StateBlock status="empty" emptyLabel="Select a conversation, or start a new one." />
          </div>
        ) : (
          <>
            <div className="flex-1 space-y-4 overflow-y-auto p-4">
              {conversationQuery.isLoading ? (
                <StateBlock status="loading" />
              ) : conversationQuery.isError ? (
                <StateBlock status="error" message={describeError(conversationQuery.error)} />
              ) : messages.length === 0 && !showLiveTurn && !showPendingUser ? (
                <StateBlock status="empty" emptyLabel="No messages yet. Say hello below." />
              ) : (
                <ul className="space-y-3">
                  {messages.map((message) => (
                    <li
                      key={message.id}
                      className={`rounded p-3 text-sm ${
                        message.role === "user" ? "bg-slate-100 text-slate-900" : "bg-blue-50 text-slate-900"
                      }`}
                    >
                      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
                        {message.role} · {dateTime(message.created_at)}
                      </p>
                      <p className="whitespace-pre-wrap">{messageText(message.content)}</p>
                      {message.role === "assistant" && message.run_id && principal?.role === "admin" && (
                        <Link to={`/admin/traces/${message.run_id}`} className="mt-1 inline-block text-xs text-blue-700 underline">
                          View trace
                        </Link>
                      )}
                    </li>
                  ))}

                  {showPendingUser && (
                    <li className="rounded bg-slate-100 p-3 text-sm text-slate-900" aria-label="you, sending">
                      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">you</p>
                      <p className="whitespace-pre-wrap">{pendingUserContent}</p>
                    </li>
                  )}

                  {showLiveTurn && (
                    <li className="rounded bg-blue-50 p-3 text-sm text-slate-900" aria-label="assistant, streaming">
                      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">assistant</p>
                      <p className="whitespace-pre-wrap">{turn.text}</p>

                      {turn.tools.length > 0 && (
                        <ul className="mt-2 space-y-0.5">
                          {turn.tools.map((tool) => (
                            <ToolRowView key={tool.id} tool={tool} />
                          ))}
                        </ul>
                      )}

                      {turn.outcomes.length > 0 && (
                        <ul className="mt-2 space-y-1">
                          {turn.outcomes.map((outcome, index) => {
                            const { text, to } = outcomeLabel(outcome);
                            return (
                              <li key={`${outcome.type}-${index}`} className="rounded border border-slate-200 bg-white px-2 py-1 text-xs">
                                {to ? <Link to={to} className="text-blue-700 underline">{text}</Link> : text}
                              </li>
                            );
                          })}
                        </ul>
                      )}

                      {turn.error && (
                        <p role="alert" className="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">
                          {turn.error}
                        </p>
                      )}

                      {turn.done && turn.runId && principal?.role === "admin" && (
                        <Link to={`/admin/traces/${turn.runId}`} className="mt-2 inline-block text-xs text-blue-700 underline">
                          View trace
                        </Link>
                      )}
                    </li>
                  )}
                </ul>
              )}
            </div>

            <form onSubmit={handleSend} className="border-t border-slate-200 p-3">
              {attachmentError && (
                <p role="alert" className="mb-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">
                  {attachmentError}
                </p>
              )}
              {attachmentName && !attachmentError && (
                <p className="mb-2 text-xs text-slate-500">Attached: {attachmentName}</p>
              )}
              <div className="flex items-end gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  aria-label="Attach a file"
                  onChange={handleFileChange}
                  disabled={busy || uploading}
                  className="text-xs"
                />
                <textarea
                  aria-label="Message"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  disabled={busy}
                  rows={2}
                  className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:bg-slate-50"
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      handleSend(event);
                    }
                  }}
                />
                <button
                  type="submit"
                  disabled={busy || draft.trim().length === 0}
                  className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {busy ? "Sending…" : "Send"}
                </button>
              </div>
            </form>
          </>
        )}
      </section>
    </div>
  );
}
