/**
 * Chat thread state driven by the `POST /chats/{id}/messages` SSE events (LLD §18):
 * chat.started · retrieval.started · retrieval.completed · agent.started · agent.tool · agent.completed ·
 * response.delta · response.evidence · response.completed · error
 */
import type { ChatEvent, ChatMessageOut, ChatMode, EvidenceRef } from "./api/types";

export type ActivityKind = "retrieval" | "agent" | "tool";

export interface ActivityStep {
  kind: ActivityKind;
  label: string;
  detail: string | null;
  done: boolean;
}

export type ChatItemStatus = "done" | "streaming" | "error" | "stopped";

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
  activity: ActivityStep[];
  error: { code: string | null; message: string; retryable: boolean } | null;
}

export interface ChatState {
  items: ChatItem[];
  /** Key of the assistant item currently streaming, if any. */
  streamingKey: string | null;
}

export type ChatAction =
  | { type: "load"; messages: ChatMessageOut[] }
  | { type: "reset" }
  | { type: "send"; key: string; text: string; mode: ChatMode }
  | { type: "event"; key: string; event: Partial<ChatEvent> & { type: string } }
  | { type: "fail"; key: string; code: string | null; message: string; retryable: boolean }
  | { type: "stop"; key: string };

export const initialChatState: ChatState = { items: [], streamingKey: null };

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
    activity: [],
    error: null,
  };
}

function mergeEvidence(current: EvidenceRef[], incoming: EvidenceRef[]): EvidenceRef[] {
  const seen = new Set(current.map((e) => e.chunk_id));
  const out = [...current];
  for (const e of incoming) {
    if (!seen.has(e.chunk_id)) {
      seen.add(e.chunk_id);
      out.push(e);
    }
  }
  return out;
}

function finishActivity(steps: ActivityStep[], kinds: ActivityKind[]): ActivityStep[] {
  return steps.map((s) => (kinds.includes(s.kind) ? { ...s, done: true } : s));
}

/** Apply one SSE event to the assistant item it belongs to. Exported for tests. */
export function applyChatEvent(item: ChatItem, event: Partial<ChatEvent> & { type: string }): ChatItem {
  switch (event.type) {
    case "chat.started":
      return { ...item, id: event.message_id ?? item.id };
    case "retrieval.started":
      return {
        ...item,
        activity: [...item.activity, { kind: "retrieval", label: "Searching the project", detail: null, done: false }],
      };
    case "retrieval.completed": {
      const hasOpen = item.activity.some((s) => s.kind === "retrieval" && !s.done);
      const steps = hasOpen
        ? item.activity.map((s) =>
            s.kind === "retrieval" && !s.done ? { ...s, done: true, detail: event.detail ?? s.detail } : s,
          )
        : [...item.activity, { kind: "retrieval" as const, label: "Searched the project", detail: event.detail ?? null, done: true }];
      return { ...item, activity: steps };
    }
    case "agent.started":
      return {
        ...item,
        activity: [
          ...finishActivity(item.activity, ["retrieval"]),
          { kind: "agent", label: "Investigating the codebase", detail: null, done: false },
        ],
      };
    case "agent.tool":
      return {
        ...item,
        activity: [
          ...finishActivity(item.activity, ["tool"]),
          { kind: "tool", label: event.tool ? `Using ${event.tool}` : "Using a project tool", detail: event.detail ?? null, done: false },
        ],
      };
    case "agent.completed":
      return { ...item, activity: finishActivity(item.activity, ["agent", "tool"]) };
    case "response.delta":
      return {
        ...item,
        text: item.text + (event.text ?? ""),
        activity: finishActivity(item.activity, ["retrieval", "agent", "tool"]),
      };
    case "response.evidence":
      return { ...item, evidence: mergeEvidence(item.evidence, event.evidence ?? []) };
    case "response.completed":
      return {
        ...item,
        id: event.message_id ?? item.id,
        seq: event.seq ?? item.seq,
        status: "done",
        activity: finishActivity(item.activity, ["retrieval", "agent", "tool"]),
      };
    case "error":
      return {
        ...item,
        status: "error",
        activity: finishActivity(item.activity, ["retrieval", "agent", "tool"]),
        error: {
          code: event.code ?? null,
          message: event.message || "The answer could not be generated.",
          retryable: Boolean(event.retryable),
        },
      };
    default:
      return item; // unknown event types are ignored (forward compatible)
  }
}

function updateItem(state: ChatState, key: string, fn: (item: ChatItem) => ChatItem): ChatItem[] {
  return state.items.map((it) => (it.key === key ? fn(it) : it));
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "load":
      return { items: [...action.messages].sort((a, b) => a.seq - b.seq).map(fromServer), streamingKey: null };
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
        activity: [],
        error: null,
      };
      const aKey = assistantKey(action.key);
      const assistant: ChatItem = { ...user, key: aKey, role: "assistant", text: "", status: "streaming" };
      return { items: [...state.items, user, assistant], streamingKey: aKey };
    }
    case "event": {
      const items = updateItem(state, action.key, (it) => applyChatEvent(it, action.event));
      const terminal = action.event.type === "response.completed" || action.event.type === "error";
      return { items, streamingKey: terminal && state.streamingKey === action.key ? null : state.streamingKey };
    }
    case "fail":
      return {
        items: updateItem(state, action.key, (it) =>
          applyChatEvent(it, { type: "error", code: action.code, message: action.message, retryable: action.retryable }),
        ),
        streamingKey: state.streamingKey === action.key ? null : state.streamingKey,
      };
    case "stop":
      return {
        items: updateItem(state, action.key, (it) =>
          it.status === "streaming"
            ? { ...it, status: "stopped", activity: finishActivity(it.activity, ["retrieval", "agent", "tool"]) }
            : it,
        ),
        streamingKey: state.streamingKey === action.key ? null : state.streamingKey,
      };
    default:
      return state;
  }
}

/** Stream ended without a terminal event (connection dropped): treat as an error the user can retry. */
export function finalizeIfOpen(state: ChatState, key: string): ChatState {
  const item = state.items.find((i) => i.key === key);
  if (!item || item.status !== "streaming") return state;
  return chatReducer(state, {
    type: "fail",
    key,
    code: "STREAM_INTERRUPTED",
    message: "The answer stream ended unexpectedly. Please try again.",
    retryable: true,
  });
}
