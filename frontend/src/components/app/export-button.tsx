"use client";

import { Download, FileDown, LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useRef } from "react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { isApiError } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import type { ExportCreated } from "@/lib/api/types";
import { useExport } from "@/lib/hooks/use-export";
import { cn, formatBytes } from "@/lib/utils";

/**
 * "Export PDF" with real artifact status: requests the export, polls until the worker has rendered it,
 * then downloads it. Status is announced politely for screen readers.
 */
export function ExportButton({
  start,
  filename,
  label = "Export PDF",
  className,
  variant = "default",
  size = "md",
}: {
  start: () => Promise<ExportCreated>;
  filename: string;
  label?: string;
  className?: string;
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
}) {
  const exp = useExport(start, filename);
  const kind = filename.endsWith(".md") ? "Markdown" : "PDF";
  const { toast } = useToast();
  const lastPhase = useRef(exp.phase);

  useEffect(() => {
    if (lastPhase.current === exp.phase) return;
    lastPhase.current = exp.phase;
    if (exp.phase === "failed") {
      const reqId = isApiError(exp.error) && exp.error.requestId ? `Request ${exp.error.requestId}` : undefined;
      toast({ title: errorMessage(exp.error), description: reqId, tone: "error", durationMs: 9000 });
    } else if (exp.phase === "ready") {
      toast({ title: `Your ${kind} export is ready`, description: "The download should start automatically.", tone: "success" });
    }
  }, [exp.phase, exp.error, toast, kind]);

  const busy = exp.phase === "starting" || exp.phase === "pending";
  const status =
    exp.phase === "starting"
      ? "Requesting export…"
      : exp.phase === "pending"
        ? `Preparing ${kind} export…`
        : exp.phase === "ready"
          ? `${kind} export ready`
          : exp.phase === "failed"
            ? "Export failed"
            : "";

  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      {exp.phase === "ready" && exp.artifact?.download_url ? (
        <a
          href={exp.artifact.download_url}
          download={filename}
          className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-s border border-ok/50 bg-ok-soft px-3 py-1.5 text-[12.5px] font-semibold text-ok hover:border-ok"
        >
          <Download size={14} aria-hidden /> Download
          {exp.artifact.size_bytes ? <span className="font-mono text-[11px] font-normal">{formatBytes(exp.artifact.size_bytes)}</span> : null}
        </a>
      ) : (
        <Button variant={variant} size={size} onClick={() => void exp.run()} disabled={busy} aria-busy={busy}>
          {busy ? (
            <LoaderCircle size={14} className="animate-spin" aria-hidden />
          ) : exp.phase === "failed" ? (
            <RefreshCw size={14} aria-hidden />
          ) : (
            <FileDown size={14} aria-hidden />
          )}
          <span className="max-[480px]:sr-only">{busy ? status : exp.phase === "failed" ? "Retry export" : label}</span>
        </Button>
      )}
      <span className="sr-only" aria-live="polite">
        {status}
      </span>
    </span>
  );
}
