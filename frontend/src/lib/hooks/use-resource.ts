"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface ResourceState<T> {
  key: string | null;
  nonce: number;
  data: T | undefined;
  error: unknown;
}

export interface Resource<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
  mutate: (fn: (current: T | undefined) => T | undefined) => void;
}

/**
 * Minimal fetch-on-key hook: loads when `key` changes (null = don't load), aborts stale requests,
 * keeps the previous data for the same key while reloading.
 */
export function useResource<T>(key: string | null, loader: (signal: AbortSignal) => Promise<T>): Resource<T> {
  const loaderRef = useRef(loader);
  useEffect(() => {
    loaderRef.current = loader;
  });
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<ResourceState<T>>({ key: null, nonce: -1, data: undefined, error: undefined });

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    loaderRef.current(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ key, nonce, data, error: undefined });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setState((s) => ({ key, nonce, data: s.key === key ? s.data : undefined, error }));
      },
    );
    return () => controller.abort();
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const mutate = useCallback(
    (fn: (current: T | undefined) => T | undefined) => setState((s) => ({ ...s, data: fn(s.data) })),
    [],
  );

  const sameKey = key !== null && state.key === key;
  const settled = sameKey && state.nonce === nonce;
  return {
    data: sameKey ? state.data : undefined,
    error: settled ? state.error : undefined,
    loading: key !== null && !settled,
    reload,
    mutate,
  };
}
