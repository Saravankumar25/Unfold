"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { api } from "@/lib/api/endpoints";
import type { SessionCurrentOut } from "@/lib/api/types";
import { useResource } from "@/lib/hooks/use-resource";

interface SessionContextValue {
  session: SessionCurrentOut | undefined;
  loading: boolean;
  error: unknown;
  refresh: () => void;
  /** Sliding expiry (`POST /sessions/current/touch`), capped server-side by the max lifetime. */
  extend: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/**
 * Anonymous session bootstrap (HLD D9): `GET /sessions/current`; when the API answers 401
 * SESSION_REQUIRED/EXPIRED the client creates a session (`POST /sessions`, sets the HttpOnly cookie) and retries.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const res = useResource("session", (signal) => api.currentSession(signal));
  const { reload, mutate } = res;

  const extend = useCallback(async () => {
    const touched = await api.touchSession();
    mutate((s) => (s ? { ...s, expires_at: touched.expires_at } : s));
  }, [mutate]);

  const value = useMemo<SessionContextValue>(
    () => ({ session: res.data, loading: res.loading, error: res.error, refresh: reload, extend }),
    [res.data, res.loading, res.error, reload, extend],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>");
  return ctx;
}
