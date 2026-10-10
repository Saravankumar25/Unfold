"use client";

import { FileCode } from "lucide-react";
import type { EvidenceRef } from "@/lib/api/types";
import { citationHref, compactLocation, evidenceLocation, type CitationSource } from "@/lib/citations";

/**
 * Evidence appendix: every location a chapter cites, once, outside the reading flow. Collapsed by default;
 * each row links to the file at the analysed commit (GitHub) and opens the in-app excerpt.
 */
export function EvidenceAppendix({
  items,
  source,
  onOpen,
}: {
  items: { label: string; ref: EvidenceRef }[];
  source?: CitationSource | null;
  onOpen: (item: { label: string; ref: EvidenceRef }) => void;
}) {
  if (!items.length) return null;
  return (
    <details className="mt-10 rounded-s border border-line bg-panel px-4 py-3" data-testid="evidence-appendix">
      <summary className="cursor-pointer font-mono text-[12px] uppercase tracking-[.1em] text-ink-soft">
        Evidence · {items.length} {items.length === 1 ? "location" : "locations"}
      </summary>
      <ul className="m-0 mt-3 flex list-none flex-col gap-1 p-0">
        {items.map((item) => {
          const href = citationHref(item.ref, source);
          return (
            <li key={`${item.ref.path}:${item.ref.start_line}:${item.ref.end_line}`} className="flex items-start gap-2">
              <FileCode size={14} className="mt-1 shrink-0 text-signal" aria-hidden />
              <span className="min-w-0 flex-1">
                <button
                  type="button"
                  onClick={() => onOpen(item)}
                  title={evidenceLocation(item.ref)}
                  className="cursor-pointer break-all bg-transparent p-0 text-left font-mono text-[12.5px] text-blueprint"
                >
                  {compactLocation(item.ref, 64)}
                </button>
                {item.ref.symbol ? (
                  <span className="ml-2 font-mono text-[11px] text-ink-faint">{item.ref.symbol}</span>
                ) : null}
                {href ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-2 text-[11.5px] text-ink-soft"
                  >
                    GitHub
                  </a>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
