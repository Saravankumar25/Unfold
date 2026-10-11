"use client";

import { MessageSquare, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import { ErrorPanel } from "@/components/app/error-panel";
import { LoadingLines } from "@/components/app/skeleton";
import { MermaidView } from "@/components/content/mermaid-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { api } from "@/lib/api/endpoints";
import type { DiagramNode, DiagramOut } from "@/lib/api/types";
import { describeDiagram } from "@/lib/diagram-layout";
import { useResource } from "@/lib/hooks/use-resource";
import { titleCase } from "@/lib/utils";

const DiagramCanvas = dynamic(() => import("./diagram-canvas"), {
  ssr: false,
  loading: () => <LoadingLines label="Laying out diagram…" lines={4} className="p-4" />,
});

type ViewMode = "interactive" | "mermaid";
const VIEW_OPTIONS = [
  { value: "interactive" as const, label: "Interactive" },
  { value: "mermaid" as const, label: "Mermaid" },
];

export type AskAboutNode = (node: DiagramNode, diagram: DiagramOut) => void;

function DiagramBody({ diagram, caption, onAskNode }: { diagram: DiagramOut; caption?: string | null; onAskNode?: AskAboutNode }) {
  const [view, setView] = useState<ViewMode>(diagram.nodes.length ? "interactive" : "mermaid");
  const [selected, setSelected] = useState<DiagramNode | null>(null);
  const relations = describeDiagram(diagram);
  const hasInferred = diagram.nodes.some((n) => n.inferred) || diagram.edges.some((e) => e.inferred);

  return (
    <figure className="m-0 my-[22px] mb-[26px] min-w-0 rounded-m border border-line bg-panel">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3 sm:px-[22px]">
        <div className="min-w-0">
          <p className="m-0 font-mono text-[11px] text-ink-faint">{titleCase(diagram.kind)}</p>
          <h2 className="m-0 mt-0.5 font-serif text-[17px] font-medium leading-snug">{diagram.title}</h2>
        </div>
        {diagram.mermaid && diagram.nodes.length ? (
          <Segmented size="sm" label="Diagram view" value={view} onChange={setView} options={VIEW_OPTIONS} />
        ) : null}
      </div>
      {diagram.description ? (
        <p className="m-0 px-4 pt-3 text-[13.5px] leading-relaxed text-ink-soft sm:px-[22px]">{diagram.description}</p>
      ) : null}

      <div className="px-2 py-2 sm:px-3">
        {view === "interactive" && diagram.nodes.length ? (
          <DiagramCanvas spec={diagram} onSelect={setSelected} />
        ) : diagram.mermaid ? (
          <div className="p-3">
            <MermaidView source={diagram.mermaid} label={diagram.title} />
          </div>
        ) : (
          <p className="m-0 p-4 text-[13px] text-ink-faint">This diagram has no content.</p>
        )}
      </div>

      {selected ? (
        <div className="mx-3 mb-3 rounded-m border border-line bg-paper-dim px-4 py-3" aria-live="polite">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="m-0 flex flex-wrap items-center gap-2">
                <b className="text-[14px]">{selected.label}</b>
                <Badge tone="muted">{selected.kind}</Badge>
                {selected.inferred ? (
                  <Badge tone="signal" dashed>
                    Inferred
                  </Badge>
                ) : null}
              </p>
              {selected.detail ? <p className="m-0 mt-1.5 text-[13px] leading-relaxed text-ink-soft">{selected.detail}</p> : null}
              {selected.group ? (
                <p className="m-0 mt-1 font-mono text-[11px] text-ink-faint">
                  group · {diagram.groups.find((g) => g.id === selected.group)?.label ?? selected.group}
                </p>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="Close node details"
              onClick={() => setSelected(null)}
              className="cursor-pointer rounded-s p-1 text-ink-faint hover:text-ink"
            >
              <X size={14} aria-hidden />
            </button>
          </div>
          {onAskNode ? (
            <Button size="sm" variant="chip" className="mt-2.5" onClick={() => onAskNode(selected, diagram)}>
              <MessageSquare size={13} aria-hidden /> Ask about this
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 sm:px-[22px]">
        <figcaption className="font-mono text-[11.5px] text-ink-faint">
          ◇ {caption || "generated from the repository's structure"}
        </figcaption>
        {hasInferred ? (
          <span className="flex items-center gap-2 font-mono text-[10.5px] text-ink-faint">
            <span aria-hidden className="inline-block w-6 border-t border-dashed border-ink-faint" /> inferred
          </span>
        ) : null}
      </div>

      {diagram.annotations.length ? (
        <ul className="m-0 list-none border-t border-line px-4 py-2.5 text-[12.5px] text-ink-soft sm:px-[22px]">
          {diagram.annotations.map((a, i) => (
            <li key={i}>· {a}</li>
          ))}
        </ul>
      ) : null}

      <details className="border-t border-line px-4 py-2 text-[12.5px] text-ink-soft sm:px-[22px]">
        <summary className="cursor-pointer font-mono text-[11px] text-ink-faint">Diagram as text</summary>
        <ul className="m-0 mt-2 list-none pl-0">
          {diagram.nodes.map((n) => (
            <li key={n.id}>
              {n.label} <span className="font-mono text-[11px] text-ink-faint">({n.kind}{n.inferred ? ", inferred" : ""})</span>
            </li>
          ))}
        </ul>
        {relations.length ? (
          <ul className="m-0 mt-2 list-none pl-0">
            {relations.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
      </details>
    </figure>
  );
}

/** A diagram by id (`GET /projects/{id}/diagrams/{diagram_id}`) or a preloaded `DiagramOut`. */
export function DiagramView({
  projectId,
  diagramId,
  diagram,
  caption,
  onAskNode,
}: {
  projectId: string;
  diagramId?: string;
  diagram?: DiagramOut | null;
  caption?: string | null;
  onAskNode?: AskAboutNode;
}) {
  const res = useResource(diagram || !diagramId ? null : `diagram:${projectId}:${diagramId}`, (signal) =>
    api.getDiagram(projectId, diagramId as string, signal),
  );
  const data = diagram ?? res.data;
  if (data) return <DiagramBody diagram={data} caption={caption} onAskNode={onAskNode} />;
  if (res.error) return <ErrorPanel className="my-5" error={res.error} title="Couldn't load this diagram" onRetry={res.reload} compact />;
  return (
    <div className="my-5 rounded-m border border-line bg-panel p-5">
      <LoadingLines label="Loading diagram…" lines={4} />
    </div>
  );
}
