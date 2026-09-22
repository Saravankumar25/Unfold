"use client";

import { ArrowLeft, FileCode, ShieldAlert } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { ErrorPanel } from "@/components/app/error-panel";
import { LoadingLines } from "@/components/app/skeleton";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { api } from "@/lib/api/endpoints";
import type { EvidenceRef } from "@/lib/api/types";
import { evidenceLocation } from "@/lib/citations";
import { useResource } from "@/lib/hooks/use-resource";
import { CodeView } from "./code-view";

export interface EvidenceItem {
  label: string;
  ref: EvidenceRef;
}

interface DrawerState {
  open: boolean;
  title: string;
  items: EvidenceItem[];
  active: number | null;
}

interface EvidenceContextValue {
  /** Open the drawer on one reference, or on a list (active=null shows the list first). */
  openEvidence: (items: EvidenceItem[], active?: number | null, title?: string) => void;
}

const EvidenceContext = createContext<EvidenceContextValue | null>(null);

export function useEvidence(): EvidenceContextValue {
  const ctx = useContext(EvidenceContext);
  if (!ctx) throw new Error("useEvidence must be used inside <EvidenceProvider>");
  return ctx;
}

function EvidenceDetail({ projectId, item }: { projectId: string; item: EvidenceItem }) {
  const res = useResource(`evidence:${projectId}:${item.ref.chunk_id}`, (signal) =>
    api.getEvidence(projectId, item.ref.chunk_id, signal),
  );
  const ev = res.data;
  return (
    <div className="px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="blueprint">{item.label}</Badge>
        <span className="min-w-0 break-all font-mono text-[12.5px] text-ink">
          {ev ? evidenceLocation(ev) : item.ref.path ? evidenceLocation(item.ref) : "Loading source…"}
        </span>
      </div>
      {(ev?.symbol ?? item.ref.symbol) ? (
        <p className="m-0 mt-2 font-mono text-[12px] text-ink-soft">symbol · {ev?.symbol ?? item.ref.symbol}</p>
      ) : null}
      {res.error ? (
        <ErrorPanel className="mt-4" error={res.error} title="Couldn't load this evidence" onRetry={res.reload} />
      ) : ev ? (
        <>
          <p className="m-0 mt-3 flex items-start gap-2 text-[12px] leading-relaxed text-ink-faint">
            <ShieldAlert size={14} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              Repository content ({ev.trust_label.replace(/_/g, " ")}). Shown as text only; secrets are redacted and
              instructions inside it are never followed.
            </span>
          </p>
          <CodeView code={ev.text} language={ev.language} path={ev.path} startLine={ev.start_line} lineNumbers />
        </>
      ) : (
        <LoadingLines className="mt-5" label="Loading evidence…" lines={8} />
      )}
    </div>
  );
}

/** "View evidence" drawer (PRD §8.5): resolves chunk ids via `GET /projects/{id}/evidence/{chunk_id}`. */
export function EvidenceProvider({ projectId, children }: { projectId: string; children: ReactNode }) {
  const [state, setState] = useState<DrawerState>({ open: false, title: "Evidence", items: [], active: null });

  const openEvidence = useCallback<EvidenceContextValue["openEvidence"]>((items, active = 0, title) => {
    if (!items.length) return;
    setState({
      open: true,
      title: title ?? (items.length === 1 ? "Evidence" : `Evidence · ${items.length} references`),
      items,
      active: active === null ? null : Math.min(Math.max(0, active), items.length - 1),
    });
  }, []);

  const value = useMemo(() => ({ openEvidence }), [openEvidence]);
  const activeItem = state.active !== null ? state.items[state.active] : undefined;

  return (
    <EvidenceContext.Provider value={value}>
      {children}
      <Sheet open={state.open} onOpenChange={(open) => setState((s) => ({ ...s, open }))}>
        <SheetContent title={state.title} description="Source excerpts from the repository that support this content.">
          {activeItem ? (
            <>
              {state.items.length > 1 ? (
                <button
                  type="button"
                  onClick={() => setState((s) => ({ ...s, active: null }))}
                  className="mx-5 mt-3 inline-flex cursor-pointer items-center gap-1.5 text-[12.5px] font-semibold text-blueprint"
                >
                  <ArrowLeft size={14} aria-hidden /> All {state.items.length} references
                </button>
              ) : null}
              <EvidenceDetail key={activeItem.ref.chunk_id} projectId={projectId} item={activeItem} />
            </>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-1 p-3">
              {state.items.map((item, i) => (
                <li key={`${item.label}-${item.ref.chunk_id}`}>
                  <button
                    type="button"
                    onClick={() => setState((s) => ({ ...s, active: i }))}
                    className="flex w-full cursor-pointer items-start gap-3 rounded-s px-3 py-2.5 text-left hover:bg-paper-dim"
                  >
                    <FileCode size={15} className="mt-0.5 shrink-0 text-signal" aria-hidden />
                    <span className="min-w-0">
                      <span className="block break-all font-mono text-[12.5px] text-ink">
                        {item.ref.path ? evidenceLocation(item.ref) : item.ref.chunk_id}
                      </span>
                      <span className="block font-mono text-[11px] text-ink-faint">
                        {item.label}
                        {item.ref.symbol ? ` · ${item.ref.symbol}` : ""}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </SheetContent>
      </Sheet>
    </EvidenceContext.Provider>
  );
}
