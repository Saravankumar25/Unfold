/**
 * Chat thread state (LLD §18). One request per turn: `POST /chats/{id}/messages` answers with the whole turn
 * (`ChatReplyOut`), so an item is `pending` until the reply (or an error) arrives. No token streaming: that keeps
 * the contract valid behind any buffering proxy or function runtime.
 */
import type { ChatMessageOut, ChatMode, ChatReplyOut, EvidenceRef } from "./api/types";

export type ChatItemStatus = "done" | "pending" | "error";

export interface ChatItem {
  /** Stable client key (server id once known for loaded messages). */
  key: string;
  id: string | null;
  seq: number | null;
  role: "user" | "assistant";
  mode: ChatMode;
  text: string;
  evidence: EvidenceRef[];
  status: ChatItemStatus;
  error: { code: string | null; message: string; retryable: boolean } | null;
}

export interface ChatState {
  items: ChatItem[];
  /** Key of the assistant item waiting for its reply, if any. */
  pendingKey: string | null;
}

export type ChatAction =
  | { type: "load"; messages: ChatMessageOut[] }
  | { type: "reset" }
  | { type: "send"; key: string; text: string; mode: ChatMode }
  | { type: "reply"; key: string; reply: ChatReplyOut }
  | { type: "fail"; key: string; code: string | null; message: string; retryable: boolean };

export const initialChatState: ChatState = { items: [], pendingKey: null };

export function assistantKey(key: string): string {
  return `${key}:a`;
}

function fromServer(m: ChatMessageOut): ChatItem {
  return {
    key: m.id,
    id: m.id,
    seq: m.seq,
    role: m.role,
    mode: m.mode,
    text: m.text,
    evidence: m.evidence ?? [],
    status: "done",
    error: null,
  };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "load":
      return { items: [...action.messages].sort((a, b) => a.seq - b.seq).map(fromServer), pendingKey: null };
    case "reset":
      return initialChatState;
    case "send": {
      const user: ChatItem = {
        key: action.key,
        id: null,
        seq: null,
        role: "user",
        mode: action.mode,
        text: action.text,
        evidence: [],
        status: "done",
        error: null,
      };
      const aKey = assistantKey(action.key);
      const assistant: ChatItem = { ...user, key: aKey, role: "assistant", text: "", status: "pending" };
      return { items: [...state.items, user, assistant], pendingKey: aKey };
    }
    case "reply": {
      const { user_message: user, message } = action.reply;
      const userKey = action.key.replace(/:a$/, "");
      return {
        items: state.items.map((it) =>
          it.key === userKey
            ? { ...it, id: user.id, seq: user.seq }
            : it.key === action.key
              ? { ...fromServer(message), key: it.key }
              : it,
        ),
        pendingKey: state.pendingKey === action.key ? null : state.pendingKey,
      };
    }
    case "fail":
      return {
        items: state.items.map((it) =>
          it.key === action.key
            ? {
                ...it,
                status: "error",
                error: { code: action.code, message: action.message || "The answer could not be generated.", retryable: action.retryable },
              }
            : it,
        ),
        pendingKey: state.pendingKey === action.key ? null : state.pendingKey,
      };
    default:
      return state;
  }
}
