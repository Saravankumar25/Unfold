import { describe, expect, it } from "vitest";
import type { ChatMessageOut, ChatReplyOut } from "@/lib/api/types";
import { assistantKey, chatReducer, initialChatState } from "@/lib/chat-state";

const message = (over: Partial<ChatMessageOut>): ChatMessageOut => ({
  id: "m",
  seq: 1,
  role: "user",
  mode: "default",
  text: "",
  evidence: [],
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

const ref = { chunk_id: "c1", path: "app/main.py", start_line: 3, end_line: 9, symbol: null, score: null };

describe("chatReducer", () => {
  it("shows the question at once and an assistant item waiting for its reply", () => {
    const next = chatReducer(initialChatState, { type: "send", key: "k", text: "Where is auth?", mode: "default" });
    expect(next.items.map((i) => [i.role, i.status, i.text])).toEqual([
      ["user", "done", "Where is auth?"],
      ["assistant", "pending", ""],
    ]);
    expect(next.pendingKey).toBe(assistantKey("k"));
  });

  it("fills the answer, its evidence and the server ids from the reply", () => {
    let state = chatReducer(initialChatState, { type: "send", key: "k", text: "q", mode: "deeper" });
    const reply: ChatReplyOut = {
      user_message: message({ id: "u1", seq: 1, text: "q" }),
      message: message({ id: "a1", seq: 2, role: "assistant", text: "It lives in `main.py` [E1].", evidence: [ref] }),
      grounding: { claims: 1, verified: 1 },
      timings: { total_ms: 12 },
    };
    state = chatReducer(state, { type: "reply", key: assistantKey("k"), reply });
    const [user, answer] = state.items;
    expect(user).toMatchObject({ id: "u1", seq: 1, status: "done" });
    expect(answer).toMatchObject({ id: "a1", seq: 2, status: "done", text: "It lives in `main.py` [E1]." });
    expect(answer!.evidence).toEqual([ref]);
    expect(answer!.key).toBe(assistantKey("k")); // stable key: no remount flicker
    expect(state.pendingKey).toBeNull();
  });

  it("marks the answer failed and keeps the question", () => {
    let state = chatReducer(initialChatState, { type: "send", key: "k", text: "q", mode: "default" });
    state = chatReducer(state, {
      type: "fail",
      key: assistantKey("k"),
      code: "LLM_BUDGET_EXHAUSTED",
      message: "This chat reached its limit.",
      retryable: false,
    });
    expect(state.items[1]).toMatchObject({ status: "error", error: { code: "LLM_BUDGET_EXHAUSTED", retryable: false } });
    expect(state.items[0]!.text).toBe("q");
    expect(state.pendingKey).toBeNull();
  });

  it("loads history in sequence order", () => {
    const state = chatReducer(initialChatState, {
      type: "load",
      messages: [message({ id: "b", seq: 2, role: "assistant", text: "a" }), message({ id: "a", seq: 1, text: "q" })],
    });
    expect(state.items.map((i) => i.id)).toEqual(["a", "b"]);
  });
});
