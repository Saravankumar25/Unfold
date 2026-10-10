"use client";

import { useEffect, useReducer } from "react";
import { api } from "@/lib/api/endpoints";
import type { JobOut, ProgressEvent } from "@/lib/api/types";
import { applyProgressEvent, initialProgress, type ConnectionState, type ProgressState } from "@/lib/progress";

type Action = { type: "event"; event: ProgressEvent } | { type: "connection"; connection: ConnectionState };

function reducer(state: ProgressState, action: Action): ProgressState {
  if (action.type === "event") return applyProgressEvent(state, action.event);
  if (state.outcome !== "running" && action.connection !== "live") return state;
  return { ...state, connection: action.connection };
}

export const POLL_MS = 1500;
export const MAX_BACKOFF_MS = 10_000;
/** How long the first poll may take before the UI says the service is waking up. */
export const WAKING_MS = 4000;

/** Delay before the next poll: steady while healthy, doubling (capped) after consecutive failures. */
export function nextPollDelay(failures: number): number {
  return failures === 0 ? POLL_MS : Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** failures);
}

/**
 * Progress by short polling (`GET /projects/{id}/progress?after=<seq>`): the cursor makes every poll cheap and
 * resumable, and each response carries the durable job, so a client that was away converges on the real state.
 */
export function useProjectProgress(projectId: string, job: JobOut | null): ProgressState {
  const [state, dispatch] = useReducer(reducer, job, initialProgress);
  const terminal = job?.status === "succeeded" || job?.status === "failed";

  useEffect(() => {
    if (terminal) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cursor = 0;
    let failures = 0;
    const waking = setTimeout(() => dispatch({ type: "connection", connection: "waking" }), WAKING_MS);

    const tick = async () => {
      try {
        const page = await api.getProgress(projectId, cursor, controller.signal);
        if (controller.signal.aborted) return;
        clearTimeout(waking);
        failures = 0;
        cursor = page.cursor;
        dispatch({ type: "connection", connection: "live" });
        for (const item of page.events) dispatch({ type: "event", event: item });
        if (page.job.status === "succeeded" || page.job.status === "failed") {
          if (!page.events.some((e) => e.type === "ready" || e.type === "failed")) {
            dispatch({ type: "event", event: finalEvent(page.job) });
          }
          return;
        }
      } catch {
        if (controller.signal.aborted) return;
        clearTimeout(waking); // an answer that is an error is not "still waking up"
        failures += 1;
        dispatch({ type: "connection", connection: failures >= 5 ? "lost" : "reconnecting" });
      }
      timer = setTimeout(tick, nextPollDelay(failures));
    };

    void tick();
    return () => {
      controller.abort();
      clearTimeout(waking);
      if (timer) clearTimeout(timer);
    };
  }, [projectId, terminal]);

  return state;
}

/** When the log has no terminal event (it was never written), synthesise one from the durable job. */
function finalEvent(job: JobOut): ProgressEvent {
  const failed = job.status === "failed";
  return {
    type: failed ? "failed" : "ready",
    stage: job.current_stage,
    label: job.stage_label,
    index: job.stage_index,
    total: job.stage_total,
    detail: null,
    error_code: job.error_code,
    message: job.error_message,
    retryable: job.retryable,
    at: job.completed_at ?? new Date().toISOString(),
  };
}
