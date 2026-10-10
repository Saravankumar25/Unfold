"use client";

import { LoaderCircle, RefreshCw } from "lucide-react";
import { errorMessage } from "@/lib/api/errors";
import { useRegenerate } from "@/lib/hooks/use-regenerate";

/**
 * "Regenerate this chapter": research, writing and verification run again in a worker for this chapter only.
 * The chapter on screen stays as it is until the new one is verified; `onDone` then reloads it.
 */
export function RegenerateButton({
  projectId,
  sectionKey,
  onDone,
}: {
  projectId: string;
  sectionKey: string;
  onDone: () => void;
}) {
  const regen = useRegenerate(projectId, sectionKey, onDone);
  const busy = regen.phase === "running";
  const status =
    regen.phase === "running"
      ? "Regenerating this chapter. This can take a minute or two."
      : regen.phase === "done"
        ? "Chapter regenerated."
        : regen.phase === "failed"
          ? `Regeneration failed: ${errorMessage(regen.error)}`
          : "";
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={() => void regen.run()}
        disabled={busy}
        aria-busy={busy}
        className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-line-strong px-3 py-1.5 text-[12.5px] font-medium text-ink-soft hover:border-blueprint hover:text-blueprint disabled:cursor-default disabled:opacity-60"
      >
        {busy ? <LoaderCircle size={13} className="animate-spin" aria-hidden /> : <RefreshCw size={13} aria-hidden />}
        {regen.phase === "failed" ? "Retry regenerating" : "Regenerate this chapter"}
      </button>
      <span className="text-[12.5px] text-ink-faint" role="status" aria-live="polite">
        {status}
      </span>
    </span>
  );
}
