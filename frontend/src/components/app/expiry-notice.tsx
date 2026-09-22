"use client";

import { Clock } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { useToast } from "@/components/ui/toast";
import { errorMessage } from "@/lib/api/errors";
import { useNow } from "@/lib/hooks/use-now";
import { cn, formatRemaining, formatTime } from "@/lib/utils";
import { useSession } from "./session-provider";

/** Warn when fewer than this many ms remain (PRD §7.6). */
export const EXPIRY_WARNING_MS = 3 * 60 * 60 * 1000;

export const EXPIRY_COPY =
  "Your Unfold session is temporary. Download your Project Guide and any chat notes you want to keep before this session expires.";

function useRemaining(): { remaining: number | null; expiresAt: string | null } {
  const { session } = useSession();
  const now = useNow();
  if (!session || !now) return { remaining: null, expiresAt: session?.expires_at ?? null };
  return { remaining: new Date(session.expires_at).getTime() - now, expiresAt: session.expires_at };
}

/** Compact chip for top bars. */
export function ExpiryChip({ className }: { className?: string }) {
  const { remaining, expiresAt } = useRemaining();
  if (remaining === null || !expiresAt) return null;
  const warn = remaining < EXPIRY_WARNING_MS;
  return (
    <Tooltip content={`${EXPIRY_COPY} Expires ${formatTime(expiresAt)}.`}>
      <span
        tabIndex={0}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[5px] font-mono text-[11px] leading-none",
          warn ? "border-signal/60 bg-signal-soft/50 text-signal" : "border-line-strong text-ink-faint",
          className,
        )}
        aria-label={`Session expires in ${formatRemaining(remaining)}`}
      >
        <Clock size={12} aria-hidden />
        {remaining <= 0 ? "expired" : `${formatRemaining(remaining)} left`}
      </span>
    </Tooltip>
  );
}

/** Banner: always shown on the landing page; elsewhere only close to expiry. */
export function ExpiryBanner({ always = false, className }: { always?: boolean; className?: string }) {
  const { session, extend } = useSession();
  const { remaining, expiresAt } = useRemaining();
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  if (remaining === null || !expiresAt || !session) return null;
  const warn = remaining < EXPIRY_WARNING_MS;
  if (!always && !warn) return null;

  const onExtend = async () => {
    setBusy(true);
    try {
      await extend();
      toast({ title: "Session extended", tone: "success" });
    } catch (error) {
      toast({ title: "Could not extend the session", description: errorMessage(error), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role={warn ? "alert" : "note"}
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-2 rounded-m border px-4 py-3 text-[13px] leading-relaxed",
        warn ? "border-signal/60 bg-signal-soft/60 text-ink" : "border-line bg-panel/60 text-ink-soft",
        className,
      )}
    >
      <Clock size={15} className={cn("shrink-0", warn ? "text-signal" : "text-ink-faint")} aria-hidden />
      <p className="m-0 min-w-0 flex-1">
        {EXPIRY_COPY}{" "}
        <span className="font-mono text-[11.5px] text-ink-faint">
          {remaining <= 0 ? "Expired." : `Expires in ${formatRemaining(remaining)} (${formatTime(expiresAt)}).`}
        </span>
      </p>
      {remaining > 0 ? (
        <Button size="sm" variant={warn ? "primary" : "default"} onClick={onExtend} disabled={busy}>
          {busy ? "Extending…" : "Keep session active"}
        </Button>
      ) : null}
    </div>
  );
}
