/**
 * Progress state for `GET /projects/{id}/progress` (LLD §5, HLD D10). Labels always come from the server
 * (`JobStage.user_label`); the client only orders and remembers what it has been told.
 */
import type { JobOut, ProgressEvent } from "./api/types";

/** `waking`: the first request has not been answered yet (a scaled-to-zero service is starting). */
export type ConnectionState = "connecting" | "waking" | "live" | "reconnecting" | "lost";
export type Outcome = "running" | "ready" | "failed";

export interface StageEntry {
  stage: string;
  label: string;
  index: number;
  at: string | null;
}

export interface ProgressState {
  stage: string;
  label: string;
  index: number;
  total: number;
  detail: string | null;
  outcome: Outcome;
  error: { code: string | null; message: string | null; retryable: boolean } | null;
  /** Stages seen, in pipeline order (deduplicated, re-entry on resume collapses). */
  history: StageEntry[];
  connection: ConnectionState;
  lastEventAt: string | null;
}

export function initialProgress(job?: JobOut | null): ProgressState {
  const failed = job?.status === "failed";
  const ready = job?.status === "succeeded";
  const base: ProgressState = {
    stage: job?.current_stage ?? "RECEIVED",
    label: job?.stage_label ?? "Connecting…",
    index: job?.stage_index ?? 0,
    total: job?.stage_total ?? 21,
    detail: null,
    outcome: ready ? "ready" : failed ? "failed" : "running",
    error: failed ? { code: job?.error_code ?? null, message: job?.error_message ?? null, retryable: !!job?.retryable } : null,
    history: [],
    connection: "connecting",
    lastEventAt: null,
  };
  if (job && job.stage_index >= 0) {
    base.history = [{ stage: job.current_stage, label: job.stage_label, index: job.stage_index, at: null }];
  }
  return base;
}

function withHistory(history: StageEntry[], entry: StageEntry): StageEntry[] {
  if (entry.index < 0) return history;
  const existing = history.findIndex((h) => h.stage === entry.stage);
  const next = existing === -1 ? [...history, entry] : history.map((h, i) => (i === existing ? { ...h, ...entry } : h));
  return next.sort((a, b) => a.index - b.index);
}

export function applyProgressEvent(state: ProgressState, event: ProgressEvent): ProgressState {
  if (event.type === "heartbeat") {
    return { ...state, connection: "live", lastEventAt: event.at ?? state.lastEventAt };
  }
  if (state.outcome !== "running" && event.type === "stage") return state; // terminal already reached

  const entry: StageEntry = { stage: event.stage, label: event.label, index: event.index, at: event.at ?? null };
  const common = {
    ...state,
    connection: "live" as const,
    lastEventAt: event.at ?? state.lastEventAt,
    total: event.total || state.total,
  };

  if (event.type === "failed") {
    return {
      ...common,
      stage: event.stage || state.stage,
      label: event.label || state.label,
      detail: event.detail,
      outcome: "failed",
      error: { code: event.error_code, message: event.message, retryable: Boolean(event.retryable) },
    };
  }
  if (event.type === "ready") {
    return {
      ...common,
      stage: event.stage || "READY",
      label: event.label || "Ready",
      index: event.index >= 0 ? event.index : common.total - 1,
      detail: event.detail,
      outcome: "ready",
      error: null,
      history: withHistory(state.history, entry),
    };
  }
  return {
    ...common,
    stage: event.stage,
    label: event.label,
    index: event.index,
    detail: event.detail,
    history: withHistory(state.history, entry),
  };
}

/** 0–100, based on pipeline position (READY is last). */
export function progressPercent(state: Pick<ProgressState, "index" | "total" | "outcome">): number {
  if (state.outcome === "ready") return 100;
  const denom = Math.max(1, state.total - 1);
  return Math.max(2, Math.min(99, Math.round((Math.max(0, state.index) / denom) * 100)));
}
