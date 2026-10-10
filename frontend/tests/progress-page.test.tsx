import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectProgressPage } from "@/components/app/project-progress-page";
import { ProjectProvider } from "@/components/app/project-provider";
import { SessionProvider } from "@/components/app/session-provider";
import { api } from "@/lib/api/endpoints";
import { WAKING_MS } from "@/lib/hooks/use-project-progress";
import type { JobOut, ProgressPage, ProjectOut } from "@/lib/api/types";

const job = (over: Partial<JobOut> = {}): JobOut => ({
  id: "j",
  status: "running",
  current_stage: "INGESTING",
  stage_label: "Fetching project",
  stage_index: 2,
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

const project = (): ProjectOut => ({
  id: "p1",
  name: "demo",
  input_type: "zip",
  source_locator: "demo.zip",
  commit_sha: null,
  default_branch: null,
  technical_level: "intermediate",
  instruction: null,
  project_type: null,
  stats: null,
  created_at: "2026-01-01T00:00:00Z",
  expires_at: "2026-01-04T00:00:00Z",
  job: job(),
});

function mount() {
  return render(
    <SessionProvider>
      <ProjectProvider projectId="p1">
        <ProjectProgressPage />
      </ProjectProvider>
    </SessionProvider>,
  );
}

describe("ProjectProgressPage", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(api, "currentSession").mockResolvedValue({
      session_id: "s",
      created_at: "2026-01-01T00:00:00Z",
      expires_at: "2026-01-02T00:00:00Z",
      projects: [],
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows the real stage and, when the first poll is slow, that the service is waking up", async () => {
    vi.spyOn(api, "getProject").mockResolvedValue(project());
    let answer: (page: ProgressPage) => void = () => undefined;
    vi.spyOn(api, "getProgress").mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getAllByText("Fetching project").length).toBeGreaterThan(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAKING_MS + 1);
    });
    expect(screen.getByRole("status")).toHaveTextContent(/waking the service up/i);

    await act(async () => {
      answer({ events: [], cursor: 0, job: job() });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByText(/waking the service up/i)).not.toBeInTheDocument();
  });

  it("explains that a project that cannot be loaded is unavailable, with a retry", async () => {
    vi.spyOn(api, "getProject").mockRejectedValue(new Error("gone"));
    mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("alert")).toHaveTextContent(/isn.t available/i);
  });
});
