import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { readSse } from "../api/sse";
import * as chat from "../api/endpoints/chat";
import { emptyTurn, turnReducer } from "./turnReducer";
import type { TurnState } from "./turnReducer";

export const conversationsQueryKey = ["conversations"] as const;
export const conversationQueryKey = (conversationId: string) => ["conversation", conversationId] as const;

export interface UseChatTurn {
  turn: TurnState;
  /** True for the lifetime of one turn -- the composer disables on this. */
  busy: boolean;
  send: (content: string) => Promise<void>;
  /** Clears the streamed turn once its content has been folded into the
   * stored transcript (task 4 step 4: "on `done` invalidates the
   * conversation query so the stored transcript replaces the streamed
   * one"). The page calls this once it has re-rendered from fresh data. */
  reset: () => void;
}

/**
 * Owns one chat turn: POSTs through `apiStream`, feeds `readSse` frames into
 * `turnReducer`, and invalidates the conversation's stored-transcript query
 * as soon as `done` arrives so the persisted messages (backend/app/chat/
 * router.py appends the assistant message only after the stream's `done`
 * frame) replace what streamed into this hook's local state.
 *
 * A turn is never retried or resumed (spec §6.3): if the connection drops
 * mid-stream, whatever text/tools/error already landed in state is final.
 */
export function useChatTurn(conversationId: string | null): UseChatTurn {
  const queryClient = useQueryClient();
  const [turn, setTurn] = useState<TurnState>(emptyTurn());
  const [busy, setBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  // Every stream ties its AbortController to component unmount -- a chat
  // turn is exactly the kind of long-lived connection that leaks if a user
  // navigates away mid-turn without this.
  useEffect(() => {
    return () => {
      controllerRef.current?.abort();
    };
  }, []);

  const reset = useCallback(() => setTurn(emptyTurn()), []);

  const send = useCallback(
    async (content: string) => {
      if (!conversationId) return;
      const controller = new AbortController();
      controllerRef.current = controller;
      setTurn(emptyTurn());
      setBusy(true);
      try {
        const response = await chat.sendMessage(conversationId, content, { signal: controller.signal });
        for await (const frame of readSse(response)) {
          setTurn((current) => turnReducer(current, frame));
          if (frame.type === "done") {
            await queryClient.invalidateQueries({ queryKey: conversationQueryKey(conversationId) });
          }
        }
      } catch {
        // Dropped connection or an abort on unmount -- never retried
        // (spec §6.3). Whatever already landed in `turn` stays on screen.
      } finally {
        setBusy(false);
        controllerRef.current = null;
      }
    },
    [conversationId, queryClient],
  );

  return { turn, busy, send, reset };
}
