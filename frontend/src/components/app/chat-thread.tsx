"use client";

import { LoaderCircle, RefreshCw } from "lucide-react";
import { MarkdownView } from "@/components/content/markdown-view";
import { Button } from "@/components/ui/button";
import type { EvidenceRef } from "@/lib/api/types";
import { listResolver } from "@/lib/citations";
import type { ChatItem } from "@/lib/chat-state";

export type OpenEvidence = (items: { label: string; ref: EvidenceRef }[], active?: number | null) => void;

/** The messages of one chat thread: user bubbles, answers with citations, the waiting note and failures. */
export function ChatMessages({
  items,
  slow,
  onOpenEvidence,
  onRetry,
}: {
  items: ChatItem[];
  /** The pending answer has taken long enough to say the service may be waking up. */
  slow: boolean;
  onOpenEvidence: OpenEvidence;
  /** Ask the same question again (offered on a retryable failure). */
  onRetry?: (question: string) => void;
}) {
  return (
    <ol className="m-0 list-none space-y-6 p-0">
      {items.map((item, index) => (
        <li key={item.key}>
          {item.role === "user" ? (
            <div className="ml-auto max-w-[82%] rounded-[14px_14px_3px_14px] bg-blueprint px-4 py-3 text-[14px] leading-relaxed text-paper">
              <span className="sr-only">You asked: </span>
              {item.text}
            </div>
          ) : (
            <article aria-label="Unfold's answer" className="flex items-start gap-3">
              <div className="mt-1 shrink-0 font-serif text-signal" aria-hidden>
                ◤
              </div>
              <div className="min-w-0 flex-1">
                <div className="rounded-[3px_14px_14px_14px] border border-line bg-panel px-4 py-3 text-[14px] leading-relaxed">
                  {item.status === "pending" ? (
                    <p role="status" className="m-0 flex items-center gap-2 text-ink-soft">
                      <LoaderCircle size={14} className="animate-spin" aria-hidden />
                      {slow
                        ? "Still working. The service may be waking up, this can take a little longer…"
                        : "Searching the project and writing an answer…"}
                    </p>
                  ) : null}
                  {item.status !== "pending" && item.text ? (
                    <MarkdownView
                      text={item.text}
                      resolve={listResolver(item.evidence)}
                      onCite={(label, ref) => onOpenEvidence([{ label, ref }])}
                    />
                  ) : null}
                  {item.error ? (
                    <div role="alert">
                      <p className="m-0 text-danger">{item.error.message}</p>
                      {item.error.retryable && onRetry && items[index - 1]?.role === "user" ? (
                        <Button size="sm" className="mt-3" onClick={() => onRetry(items[index - 1]!.text)}>
                          <RefreshCw size={13} aria-hidden /> Try again
                        </Button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                {item.evidence.length ? (
                  <button
                    type="button"
                    onClick={() => onOpenEvidence(item.evidence.map((ref, i) => ({ label: `E${i + 1}`, ref })), null)}
                    className="mt-2 cursor-pointer bg-transparent p-0 font-mono text-[10.5px] text-blueprint"
                  >
                    ⌁ {item.evidence.length} source{item.evidence.length === 1 ? "" : "s"}
                  </button>
                ) : null}
              </div>
            </article>
          )}
        </li>
      ))}
    </ol>
  );
}
