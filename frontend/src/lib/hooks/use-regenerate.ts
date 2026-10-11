"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isApiError } from "@/lib/api/client";
import { api } from "@/lib/api/endpoints";

export type RegeneratePhase = "idle" | "running" | "done" | "failed";

export interface RegenerateState {
  phase: RegeneratePhase;
  error: unknown;
}

const IDLE: RegenerateState = { phase: "idle", error: null };

export const REGENERATE_POLL_MS = 2000;
const MAX_POLL_MS = 10 * 60 * 1000;
/** Consecutive transient poll failures (proxy 502, network) tolerated: the job runs on in the worker meanwhile. */
const MAX_POLL_FAILURES = 5;
/** `GET /progress?after=` with a cursor past every event: only the durable job state is wanted here. */
const NO_EVENTS = Number.MAX_SAFE_INTEGER;

/**
 * Single-chapter regeneration: `POST …/sections/{key}/regenerate` -> 202 `{job_id}`, then poll the project
 * progress until its `regeneration` job (the one we started; a repeat click returns the same job) is terminal.
 * `onDone` runs once when the chapter was swapped in, so the caller can reload it.
 */
export function useRegenerate(projectId: string, sectionKey: string, onDone: () => void) {
  // the state belongs to one chapter: another chapter (or project) reads as idle without any reset
  const id = `${projectId}:${sectionKey}`;
  const [held, setHeld] = useState<RegenerateState & { id: string }>({ ...IDLE, id });
  const state = held.id === id ? held : IDLE;
  const doneRef = useRef(onDone);
  useEffect(() => {
    doneRef.current = onDone;
  });
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => () => cancelRef.current?.(), [id]);

  const run = useCallback(async () => {
    cancelRef.current?.();
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();
    cancelRef.current = () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
    setHeld({ id, phase: "running", error: null });
    try {
      const { job_id } = await api.regenerateSection(projectId, sectionKey);
      const began = Date.now();
      let failures = 0;
      for (;;) {
        let page: Awaited<ReturnType<typeof api.getProgress>> | null = null;
        try {
          page = await api.getProgress(projectId, NO_EVENTS, controller.signal);
          failures = 0;
        } catch (error) {
          if (cancelled || !isApiError(error) || !error.retryable || ++failures >= MAX_POLL_FAILURES) throw error;
        }
        if (cancelled) return;
        const job = page?.regeneration;
        if (job && job.id === job_id && job.status === "succeeded") {
          setHeld({ id, phase: "done", error: null });
          doneRef.current();
          return;
        }
        if (job && job.id === job_id && job.status === "failed") {
          setHeld({ id, phase: "failed", error: new Error(job.error_message || "Regenerating the chapter failed.") });
          return;
        }
        if (Date.now() - began > MAX_POLL_MS) {
          setHeld({ id, phase: "failed", error: new Error("Regenerating the chapter is taking too long.") });
          return;
        }
        await new Promise<void>((resolve) => {
          timer = setTimeout(resolve, REGENERATE_POLL_MS);
        });
        if (cancelled) return;
      }
    } catch (error) {
      if (!cancelled) setHeld({ id, phase: "failed", error });
    }
  }, [id, projectId, sectionKey]);

  return { phase: state.phase, error: state.error, run };
}
