import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RegenerateButton } from "@/components/app/regenerate-button";
import { api } from "@/lib/api/endpoints";
import type { JobOut, ProgressPage } from "@/lib/api/types";
import { REGENERATE_POLL_MS } from "@/lib/hooks/use-regenerate";

const job = (over: Partial<JobOut> = {}): JobOut => ({
  id: "j-regen",
  status: "running",
  current_stage: "RESEARCHING_GUIDE",
  stage_label: "Researching each chapter",
  stage_index: 14,
  stage_total: 21,
  attempt: 1,
  error_code: null,
  error_message: null,
  retryable: false,
  degraded: [],
  kind: "regenerate",
  section_key: "overview",
  started_at: null,
  completed_at: null,
  ...over,
});

const page = (regeneration: JobOut | null): ProgressPage => ({
  events: [],
  cursor: 5,
  job: job({ id: "j-analysis", status: "succeeded", kind: "analysis", section_key: null }),
  regeneration,
});

describe("Regenerate this chapter", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("starts one regeneration, waits for its own job and reloads the chapter once", async () => {
    const start = vi
      .spyOn(api, "regenerateSection")
      .mockResolvedValue({ job_id: "j-regen", section_key: "overview" });
    const pages = [
      page(job({ id: "an-older-job", status: "succeeded" })), // a previous regeneration is not ours
      page(job({ status: "running" })),
      page(job({ status: "succeeded", current_stage: "READY" })),
    ];
    const progress = vi.spyOn(api, "getProgress").mockImplementation(async () => pages.shift() ?? page(null));
    const onDone = vi.fn();

    render(<RegenerateButton projectId="p1" sectionKey="overview" onDone={onDone} />);
    const button = screen.getByRole("button", { name: "Regenerate this chapter" });
    await userEvent.click(button);

    expect(start).toHaveBeenCalledTimes(1);
    expect(start).toHaveBeenCalledWith("p1", "overview");
    expect(button).toBeDisabled(); // a second click cannot start a second one
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Regenerating this chapter");
    expect(onDone).not.toHaveBeenCalled();

    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REGENERATE_POLL_MS);
      });
    }
    expect(progress.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("Chapter regenerated.");
    expect(screen.getByRole("button", { name: "Regenerate this chapter" })).toBeEnabled();
  });

  it("reports a failed regeneration, keeps the chapter and offers a retry", async () => {
    vi.spyOn(api, "regenerateSection").mockResolvedValue({ job_id: "j-regen", section_key: "overview" });
    vi.spyOn(api, "getProgress").mockResolvedValue(
      page(job({ status: "failed", error_code: "PROVIDER_UNAVAILABLE", error_message: "The model provider is down." })),
    );
    const onDone = vi.fn();

    render(<RegenerateButton projectId="p1" sectionKey="overview" onDone={onDone} />);
    await userEvent.click(screen.getByRole("button", { name: "Regenerate this chapter" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("The model provider is down.");
    expect(screen.getByRole("button", { name: "Retry regenerating" })).toBeEnabled();
  });

  it("starts from idle again when the reader moves to another chapter", async () => {
    vi.spyOn(api, "regenerateSection").mockResolvedValue({ job_id: "j-regen", section_key: "overview" });
    vi.spyOn(api, "getProgress").mockResolvedValue(page(job({ status: "running" })));
    const { rerender } = render(<RegenerateButton projectId="p1" sectionKey="overview" onDone={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Regenerate this chapter" }));
    expect(screen.getByRole("status")).toHaveTextContent("Regenerating");

    rerender(<RegenerateButton projectId="p1" sectionKey="request-lifecycle" onDone={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Regenerate this chapter" })).toBeEnabled();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });
});
