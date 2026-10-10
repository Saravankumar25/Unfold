"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { isApiError } from "@/lib/api/client";
import { api } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import type { ChatMode } from "@/lib/api/types";
import { assistantKey, chatReducer, initialChatState, type ChatState } from "@/lib/chat-state";

let keyCounter = 0;
function nextKey(): string {
  keyCounter += 1;
  return `local-${keyCounter}-${Math.random().toString(36).slice(2, 8)}`;
}

export interface ChatThread {
  chatId: string | null;
  state: ChatState;
  /** A question is waiting for its answer. */
  pending: boolean;
  loadingHistory: boolean;
  historyError: unknown;
  send: (text: string, mode: ChatMode, sectionKey?: string | null) => Promise<void>;
  selectChat: (chatId: string | null) => void;
  reloadHistory: () => void;
}

/**
 * One chat thread: loads history for `chatId` and sends questions with `POST /chats/{id}/messages` (one JSON
 * reply per turn). When no chat exists yet, the first `send` creates one (`POST /projects/{id}/chats`) and
 * reports it via `onCreated`.
 */
export function useChatThread(
  projectId: string,
  initialChatId: string | null,
  onCreated?: (chatId: string) => void,
): ChatThread {
  const [chatId, setChatId] = useState<string | null>(initialChatId);
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [historyNonce, setHistoryNonce] = useState(0);
  /** `${chatId}#${nonce}` of the last settled history load, plus its error. */
  const [history, setHistory] = useState<{ key: string | null; error: unknown }>({ key: null, error: null });
  const abortRef = useRef<AbortController | null>(null);
  const onCreatedRef = useRef(onCreated);
  useEffect(() => {
    onCreatedRef.current = onCreated;
  });
  const historyKey = chatId ? `${chatId}#${historyNonce}` : null;

  useEffect(() => {
    if (!chatId) return;
    const key = `${chatId}#${historyNonce}`;
    if (history.key === key) return; // already settled (or freshly created by `send`)
    const controller = new AbortController();
    api.getChat(chatId, controller.signal).then(
      (detail) => {
        if (controller.signal.aborted) return;
        dispatch({ type: "load", messages: detail.messages });
        setHistory({ key, error: null });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setHistory({ key, error });
      },
    );
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `history.key` is a guard, not a trigger
  }, [chatId, historyNonce]);

  const loadingHistory = historyKey !== null && history.key !== historyKey;
  const historyError = history.key === historyKey ? history.error : null;

  useEffect(() => () => abortRef.current?.abort(), []);

  const selectChat = useCallback((id: string | null) => {
    abortRef.current?.abort();
    dispatch({ type: "reset" });
    setHistoryNonce((n) => n + 1); // force a fresh history load even for a previously loaded id
    setChatId(id);
  }, []);

  const send = useCallback(
    async (text: string, mode: ChatMode, sectionKey: string | null = null) => {
      const trimmed = text.trim();
      if (!trimmed || abortRef.current) return;
      const key = nextKey();
      const aKey = assistantKey(key);
      const controller = new AbortController();
      abortRef.current = controller;
      dispatch({ type: "send", key, text: trimmed, mode });
      try {
        let id = chatId;
        if (!id) {
          const created = await api.createChat(projectId);
          id = created.id;
          setHistory({ key: `${id}#${historyNonce}`, error: null }); // nothing to load: the reply fills it
          setChatId(id);
          onCreatedRef.current?.(id);
        }
        const reply = await api.postMessage(id, { text: trimmed, mode, section_key: sectionKey }, controller.signal);
        if (!controller.signal.aborted) dispatch({ type: "reply", key: aKey, reply });
      } catch (error) {
        if (!controller.signal.aborted) {
          dispatch({
            type: "fail",
            key: aKey,
            code: isApiError(error) ? error.code : null,
            message:
              errorMessage(error) + (isApiError(error) && error.requestId ? ` (request ${error.requestId})` : ""),
            retryable: isApiError(error) ? error.retryable : true,
          });
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [chatId, projectId, historyNonce],
  );

  return {
    chatId,
    state,
    pending: state.pendingKey !== null,
    loadingHistory,
    historyError,
    send,
    selectChat,
    reloadHistory: () => setHistoryNonce((n) => n + 1),
  };
}
