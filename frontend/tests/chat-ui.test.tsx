import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatMessages } from "@/components/app/chat-thread";
import { ApiError } from "@/lib/api/client";
import { api } from "@/lib/api/endpoints";
import type { ChatReplyOut } from "@/lib/api/types";
import type { ChatItem } from "@/lib/chat-state";
import { useChatThread } from "@/lib/hooks/use-chat-thread";

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
