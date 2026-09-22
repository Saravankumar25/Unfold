"use client";

import { RefreshCw, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { isApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/utils";

/** Error envelope surface: user-facing message + code + request id (for support), optional retry. */
export function ErrorPanel({
  error,
  title = "Something went wrong",
  onRetry,
  children,
  className,
  compact,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  children?: ReactNode;
  className?: string;
  compact?: boolean;
}) {
  const api = isApiError(error) ? error : null;
  return (
    <div
      role="alert"
      className={cn(
        "rounded-m border border-danger/40 bg-danger-soft/60 text-ink",
        compact ? "px-3.5 py-2.5 text-[13px]" : "px-5 py-4 text-[14px]",
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <TriangleAlert size={compact ? 15 : 18} className="mt-0.5 shrink-0 text-danger" aria-hidden />
        <div className="min-w-0 flex-1">
          {!compact && <p className="m-0 font-semibold text-danger">{title}</p>}
          <p className={cn("m-0 leading-relaxed", !compact && "mt-1")}>{errorMessage(error)}</p>
          {api && (api.code || api.requestId) ? (
            <p className="m-0 mt-1.5 font-mono text-[11px] text-ink-faint">
              {api.code}
              {api.requestId ? ` · request ${api.requestId}` : ""}
            </p>
          ) : null}
          {children}
          {onRetry && (!api || api.retryable || api.status === 0 || api.status >= 500) ? (
            <Button size="sm" className="mt-3" onClick={onRetry}>
              <RefreshCw size={13} aria-hidden /> Try again
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
