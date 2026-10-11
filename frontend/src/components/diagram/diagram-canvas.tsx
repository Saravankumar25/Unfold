"use client";

import "@xyflow/react/dist/style.css";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import { useMemo, type KeyboardEvent } from "react";
import type { DiagramNode, DiagramSpec } from "@/lib/api/types";
import { layoutDiagram, type UnfoldGroupData, type UnfoldNodeData } from "@/lib/diagram-layout";
import { cn } from "@/lib/utils";

const KIND_ACCENT: Record<string, string> = {
  datastore: "border-l-signal",
  queue: "border-l-signal",
  external: "border-l-ink-faint",
  actor: "border-l-ok",
  endpoint: "border-l-blueprint",
  service: "border-l-blueprint",
};

function UnfoldNode({ data, selected, sourcePosition, targetPosition }: NodeProps<Node<UnfoldNodeData, "unfold">>) {
  const n = data.node;
  return (
    <div
      className={cn(
        "flex h-full w-full flex-col justify-center rounded-[6px] border border-l-[3px] bg-panel px-3 py-1.5 text-left shadow-[0_1px_2px_rgba(0,0,0,0.05)]",
        n.inferred ? "border-dashed border-crease" : "border-line-strong",
        KIND_ACCENT[n.kind] ?? "border-l-line-strong",
        selected && "outline-2 outline-offset-2 outline-signal",
      )}
      title={n.detail ?? n.label}
    >
      <Handle type="target" position={targetPosition ?? Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-crease" />
      <span className="truncate text-[12.5px] font-semibold leading-tight text-ink">{n.label}</span>
      <span className="truncate font-mono text-[10px] leading-tight text-ink-faint">
        {n.kind}
        {n.inferred ? " · inferred" : ""}
      </span>
      <Handle type="source" position={sourcePosition ?? Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-crease" />
    </div>
  );
}

function UnfoldGroup({ data }: NodeProps<Node<UnfoldGroupData, "unfoldGroup">>) {
  return (
    <div className="h-full w-full rounded-m border border-dashed border-crease bg-paper-dim/50">
      <span className="absolute left-3 top-2 font-mono text-[10.5px] uppercase tracking-wide text-ink-faint">{data.label}</span>
    </div>
  );
}

const nodeTypes: NodeTypes = { unfold: UnfoldNode, unfoldGroup: UnfoldGroup };

/** Interactive DiagramSpec renderer (React Flow + dagre). Reading-only: pan/zoom and node selection. */
export default function DiagramCanvas({
  spec,
  direction,
  onSelect,
}: {
  spec: DiagramSpec;
  direction?: "LR" | "TB";
  onSelect: (node: DiagramNode | null) => void;
}) {
  const layout = useMemo(() => layoutDiagram(spec, direction), [spec, direction]);
  const height = Math.min(560, Math.max(260, layout.height + 40));

  // Nodes are focusable (Tab); Enter / Space opens the focused node's details, as a click does.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const id = (event.target as HTMLElement).closest(".react-flow__node-unfold")?.getAttribute("data-id");
    const hit = spec.nodes.find((n) => n.id === id);
    if (!hit) return;
    event.preventDefault();
    onSelect(hit);
  };

  return (
    <div style={{ height }} className="w-full" onKeyDown={onKeyDown}>
      <ReactFlow
        nodes={layout.nodes}
        edges={layout.edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.2}
        maxZoom={2}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        elementsSelectable
        panOnScroll={false}
        zoomOnScroll={false}
        preventScrolling={false}
        onNodeClick={(_, node) => {
          if (node.type === "unfold") onSelect((node.data as UnfoldNodeData).node);
        }}
        onPaneClick={() => onSelect(null)}
        aria-label={`${spec.title} — interactive diagram`}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--line-strong)" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}
