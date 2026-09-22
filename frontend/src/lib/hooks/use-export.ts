"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/endpoints";
import type { ArtifactOut, ExportCreated } from "@/lib/api/types";

export type ExportPhase = "idle" | "starting" | "pending" | "ready" | "failed";

export interface ExportState {
  phase: ExportPhase;
  artifact: ArtifactOut | null;
  error: unknown;
}

const POLL_MS = 2000;
const MAX_POLL_MS = 10 * 60 * 1000;

export function triggerDownload(url: string, filename?: string) {
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/**
 * Export lifecycle (LLD §19/§20): `POST …/exports` → 202 `{artifact_id}` → poll `GET /exports/{id}` until
 * `ready` (then download via `download_url`) or `failed`.
 */
export function useExport(start: () => Promise<ExportCreated>, filename: string) {
  const [state, setState] = useState<ExportState>({ phase: "idle", artifact: null, error: null });
  const startRef = useRef(start);
  useEffect(() => {
    startRef.current = start;
  });
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => () => cancelRef.current?.(), []);

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

    setState({ phase: "starting", artifact: null, error: null });
    try {
      const { artifact_id } = await startRef.current();
      const began = Date.now();
      const poll = async (): Promise<void> => {
        if (cancelled) return;
        const artifact = await api.getExport(artifact_id, controller.signal);
        if (cancelled) return;
        if (artifact.status === "ready") {
          setState({ phase: "ready", artifact, error: null });
          if (artifact.download_url) triggerDownload(artifact.download_url, filename);
          return;
        }
        if (artifact.status === "failed") {
          setState({ phase: "failed", artifact, error: new Error(artifact.error || "The PDF export failed. Please try again.") });
          return;
        }
        setState({ phase: "pending", artifact, error: null });
        if (Date.now() - began > MAX_POLL_MS) {
          setState({ phase: "failed", artifact, error: new Error("The export is taking too long. Please try again later.") });
          return;
        }
        await new Promise<void>((resolve) => {
          timer = setTimeout(resolve, POLL_MS);
        });
        return poll();
      };
      await poll();
    } catch (error) {
      if (cancelled) return;
      setState((s) => ({ phase: "failed", artifact: s.artifact, error }));
    }
  }, [filename]);

  return { ...state, run };
}
