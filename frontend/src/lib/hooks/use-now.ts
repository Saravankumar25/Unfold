"use client";

import { useSyncExternalStore } from "react";

/** Shared wall clock (ticks every 30 s). Returns 0 during SSR/hydration so markup stays deterministic. */
let now = 0;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (!timer) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      for (const l of listeners) l();
    }, 30_000);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function getSnapshot(): number {
  if (!now) now = Date.now();
  return now;
}

function getServerSnapshot(): number {
  return 0;
}

export function useNow(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
