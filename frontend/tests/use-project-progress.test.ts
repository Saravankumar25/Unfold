import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/endpoints";
import type { JobOut, ProgressPage } from "@/lib/api/types";
import { nextPollDelay, POLL_MS, useProjectProgress, WAKING_MS } from "@/lib/hooks/use-project-progress";

const job = (over: Partial<JobOut> = {}): JobOut => ({
  id: "j",
  status: "running",
  current_stage: "RECEIVED",
  stage_label: "Queued",
  stage_index: 0,
  stage_total: 19,
  attempt: 1,
  error_code: null,
  error_message: null,
  retryable: false,
  degraded: [],
  started_at: null,
  completed_at: null,
  ...over,
});

const event = (seq: number, type: "stage" | "ready", stage: string, index: number) => ({
  seq,
  type,
  stage,
  label: stage,
  index,
  total: 19,
  detail: null,
  error_code: null,
  message: null,
  retryable: null,
  at: "2026-01-01T00:00:00Z",
});

describe("nextPollDelay", () => {
  it("is steady when healthy and backs off (capped) on failures", () => {
    expect(nextPollDelay(0)).toBe(POLL_MS);
    expect(nextPollDelay(1)).toBe(POLL_MS * 2);
    expect(nextPollDelay(10)).toBe(10_000);
  });
});

describe("useProjectProgress", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("polls with the returned cursor and stops at a terminal job", async () => {
    const pages: ProgressPage[] = [
      { events: [event(1, "stage", "INGESTING", 2)], cursor: 1, job: job({ current_stage: "INGESTING", stage_index: 2 }) },
      {
        events: [event(2, "ready", "READY", 18)],
        cursor: 2,
        job: job({ status: "succeeded", current_stage: "READY", stage_index: 18 }),
      },
    ];
    const spy = vi.spyOn(api, "getProgress").mockImplementation(async () => pages.shift()!);

    const { result } = renderHook(() => useProjectProgress("p1", job()));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(spy).toHaveBeenNthCalledWith(1, "p1", 0, expect.anything());
    expect(result.current.stage).toBe("INGESTING");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(spy).toHaveBeenNthCalledWith(2, "p1", 1, expect.anything());
    expect(result.current.outcome).toBe("ready");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 5);
    });
    expect(spy).toHaveBeenCalledTimes(2); // no polling after the job finished
  });

  it("reports reconnecting after a failed poll and recovers", async () => {
    const spy = vi
      .spyOn(api, "getProgress")
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ events: [], cursor: 0, job: job() });

    const { result } = renderHook(() => useProjectProgress("p1", job()));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.connection).toBe("reconnecting");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 2);
    });
    expect(spy).toHaveBeenCalledTimes(2);
    expect(result.current.connection).toBe("live");
  });

  it("says the service is waking up when the first poll is slow, then goes live", async () => {
    let answer: (page: ProgressPage) => void = () => undefined;
    vi.spyOn(api, "getProgress").mockImplementation(
      () => new Promise<ProgressPage>((resolve) => (answer = resolve)),
    );
    const { result } = renderHook(() => useProjectProgress("p1", job()));
    expect(result.current.connection).toBe("connecting");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAKING_MS + 1);
    });
    expect(result.current.connection).toBe("waking");

    await act(async () => {
      answer({ events: [], cursor: 0, job: job() });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.connection).toBe("live");
  });

  it("backs off between failed polls instead of hammering a cold service", async () => {
    const spy = vi.spyOn(api, "getProgress").mockRejectedValue(new Error("offline"));
    renderHook(() => useProjectProgress("p1", job()));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0); // first call fails, the next is due in 2x POLL_MS
    });
    expect(spy).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 2 - 1);
    });
    expect(spy).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1); // second call fails, the next is due in 4x POLL_MS
    });
    expect(spy).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 4 - 1);
    });
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
