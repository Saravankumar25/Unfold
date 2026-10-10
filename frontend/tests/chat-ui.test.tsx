import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatMessages } from "@/components/app/chat-thread";
import { ApiError } from "@/lib/api/client";
import { api } from "@/lib/api/endpoints";
import type { ChatReplyOut } from "@/lib/api/types";
import type { ChatItem } from "@/lib/chat-state";
import { ANSWER_TIMEOUT_MS, useChatThread } from "@/lib/hooks/use-chat-thread";

const ref = { chunk_id: "c1", path: "app/main.py", start_line: 3, end_line: 9, symbol: null, score: null };

const item = (over: Partial<ChatItem>): ChatItem => ({
  key: "k",
  id: null,
  seq: null,
  role: "assistant",
  mode: "default",
  text: "",
  evidence: [],
  status: "done",
  error: null,
  ...over,
});

describe("ChatMessages", () => {
  it("renders the question, the answer and a sources button that opens the evidence", async () => {
    const open = vi.fn();
    render(
      <ChatMessages
        slow={false}
        onOpenEvidence={open}
        items={[
          item({ key: "u", role: "user", text: "Where is the entrypoint?" }),
          item({ key: "a", text: "It is in `main.py` [E1].", evidence: [ref] }),
        ]}
      />,
    );
    expect(screen.getByText("Where is the entrypoint?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /1 source/ }));
    expect(open).toHaveBeenCalledWith([{ label: "E1", ref }], null);
  });

  it("says it is working, and later that the service may be waking up", () => {
    const pending = [item({ status: "pending" })];
    const { rerender } = render(<ChatMessages items={pending} slow={false} onOpenEvidence={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent(/writing an answer/i);
    rerender(<ChatMessages items={pending} slow onOpenEvidence={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent(/waking up/i);
  });

  it("shows a failed answer as an alert", () => {
    render(
      <ChatMessages
        slow={false}
        onOpenEvidence={vi.fn()}
        items={[item({ status: "error", error: { code: "X", message: "This chat reached its limit.", retryable: false } })]}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("This chat reached its limit.");
  });
});

describe("useChatThread", () => {
  afterEach(() => vi.restoreAllMocks());

  const reply: ChatReplyOut = {
    user_message: { id: "u1", seq: 1, role: "user", mode: "default", text: "q", evidence: [], created_at: "" },
    message: { id: "a1", seq: 2, role: "assistant", mode: "default", text: "answer [E1]", evidence: [ref], created_at: "" },
    grounding: {},
    timings: {},
  };

  it("creates the chat on the first question, then sends it as plain JSON", async () => {
    const created = vi.spyOn(api, "createChat").mockResolvedValue({
      id: "c1",
      project_id: "p",
      title: null,
      message_count: 0,
      created_at: "",
      updated_at: "",
    });
    const post = vi.spyOn(api, "postMessage").mockResolvedValue(reply);
    vi.spyOn(api, "getChat").mockResolvedValue({ chat: {} as never, messages: [] });
    const onCreated = vi.fn();
    const { result } = renderHook(() => useChatThread("p", null, onCreated));

    await act(async () => {
      await result.current.send("q", "default", "overview");
    });
    expect(created).toHaveBeenCalledWith("p");
    expect(post).toHaveBeenCalledWith("c1", { text: "q", mode: "default", section_key: "overview" }, expect.anything());
    expect(onCreated).toHaveBeenCalledWith("c1");
    expect(result.current.pending).toBe(false);
    expect(result.current.state.items.map((i) => i.text)).toEqual(["q", "answer [E1]"]);
  });

  it("keeps the question and surfaces the error envelope when the answer fails", async () => {
    vi.spyOn(api, "getChat").mockResolvedValue({ chat: {} as never, messages: [] });
    vi.spyOn(api, "postMessage").mockRejectedValue(
      new ApiError({ code: "RATE_LIMITED", message: "Too many requests", retryable: true, status: 429, requestId: "r-1" }),
    );
    const { result } = renderHook(() => useChatThread("p", "c1"));
    await waitFor(() => expect(result.current.loadingHistory).toBe(false));
    await act(async () => {
      await result.current.send("q", "default");
    });
    const [question, answer] = result.current.state.items;
    expect(question!.text).toBe("q");
    expect(answer).toMatchObject({ status: "error", error: { code: "RATE_LIMITED", retryable: true } });
    expect(answer!.error!.message).toContain("request r-1");
  });

  it("ignores a second question while one is pending", async () => {
    vi.spyOn(api, "getChat").mockResolvedValue({ chat: {} as never, messages: [] });
    let finish: (r: ChatReplyOut) => void = () => undefined;
    const post = vi.spyOn(api, "postMessage").mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    const { result } = renderHook(() => useChatThread("p", "c1"));
    await waitFor(() => expect(result.current.loadingHistory).toBe(false));
    await act(async () => {
      void result.current.send("one", "default");
    });
    await act(async () => {
      await result.current.send("two", "default");
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(result.current.pending).toBe(true);
    await act(async () => finish(reply));
    expect(result.current.pending).toBe(false);
  });
});

describe("useChatThread races", () => {
  afterEach(() => vi.restoreAllMocks());

  const chat = (id: string) => ({ id, project_id: "p", title: null, message_count: 0, created_at: "", updated_at: "" });

  it("does not jump to a new chat the user already left while it was being created", async () => {
    vi.spyOn(api, "getChat").mockResolvedValue({ chat: {} as never, messages: [] });
    let created: (c: ReturnType<typeof chat>) => void = () => undefined;
    vi.spyOn(api, "createChat").mockImplementation(() => new Promise((resolve) => (created = resolve)));
    const post = vi.spyOn(api, "postMessage");
    const onCreated = vi.fn();
    const { result } = renderHook(() => useChatThread("p", null, onCreated));

    await act(async () => {
      void result.current.send("first question", "default");
    });
    act(() => result.current.selectChat("other"));
    await act(async () => created(chat("orphan")));

    expect(result.current.chatId).toBe("other");
    expect(onCreated).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("refuses a question while the history is still loading instead of losing its answer", async () => {
    let finish: (d: { chat: never; messages: never[] }) => void = () => undefined;
    vi.spyOn(api, "getChat").mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    const post = vi.spyOn(api, "postMessage");
    const { result } = renderHook(() => useChatThread("p", "c1"));
    expect(result.current.loadingHistory).toBe(true);
    await act(async () => {
      await result.current.send("too early", "default");
    });
    expect(post).not.toHaveBeenCalled();
    expect(result.current.state.items).toEqual([]);
    await act(async () => finish({ chat: {} as never, messages: [] }));
    expect(result.current.loadingHistory).toBe(false);
  });

  it("reports a timed-out answer as a retryable failure", async () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(api, "getChat").mockResolvedValue({ chat: {} as never, messages: [] });
      vi.spyOn(api, "postMessage").mockImplementation(
        (_id, _body, signal) =>
          new Promise((_resolve, reject) => {
            signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      );
      const { result } = renderHook(() => useChatThread("p", "c1"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      await act(async () => {
        void result.current.send("q", "default");
        await vi.advanceTimersByTimeAsync(ANSWER_TIMEOUT_MS + 1);
      });
      expect(result.current.state.items[1]).toMatchObject({
        status: "error",
        error: { code: "ANSWER_TIMEOUT", retryable: true },
      });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("ChatMessages retry", () => {
  it("offers Try again on a retryable failure and resends the question", async () => {
    const retry = vi.fn();
    render(
      <ChatMessages
        slow={false}
        onOpenEvidence={vi.fn()}
        onRetry={retry}
        items={[
          item({ key: "u", role: "user", text: "Where is auth?" }),
          item({ key: "a", status: "error", error: { code: "X", message: "Provider busy", retryable: true } }),
        ]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(retry).toHaveBeenCalledWith("Where is auth?");
  });

  it("does not offer it when the failure is final", () => {
    render(
      <ChatMessages
        slow={false}
        onOpenEvidence={vi.fn()}
        onRetry={vi.fn()}
        items={[
          item({ key: "u", role: "user", text: "q" }),
          item({ key: "a", status: "error", error: { code: "LLM_BUDGET_EXHAUSTED", message: "Limit reached", retryable: false } }),
        ]}
      />,
    );
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });
});
