/**
 * DiagramSpec (LLD §17) → React Flow nodes/edges with a dagre layout.
 * - groups become React Flow parent nodes (compound dagre layout); children are positioned relative to them;
 * - inferred nodes/edges are flagged so the renderer can dash them (HLD D8, PRD "INFERRED" labels);
 * - edges referencing unknown nodes are dropped (the backend validates, this is defence in depth).
 */
import dagre from "@dagrejs/dagre";
import { MarkerType, Position, type Edge, type Node } from "@xyflow/react";
import type { DiagramEdge, DiagramKind, DiagramNode, DiagramSpec } from "./api/types";

export interface UnfoldNodeData extends Record<string, unknown> {
  node: DiagramNode;
}
export interface UnfoldGroupData extends Record<string, unknown> {
  label: string;
}
export interface UnfoldEdgeData extends Record<string, unknown> {
  inferred: boolean;
  kind: string | null;
}

export type UnfoldFlowNode = Node<UnfoldNodeData, "unfold"> | Node<UnfoldGroupData, "unfoldGroup">;
export type UnfoldFlowEdge = Edge<UnfoldEdgeData>;

export interface LayoutResult {
  nodes: UnfoldFlowNode[];
  edges: UnfoldFlowEdge[];
  width: number;
  height: number;
}

export const GROUP_PREFIX = "group:";
const GROUP_PAD_X = 18;
const GROUP_PAD_TOP = 34;
const GROUP_PAD_BOTTOM = 16;
const NODE_HEIGHT = 56;

export function nodeSize(node: Pick<DiagramNode, "label">): { width: number; height: number } {
  const chars = Math.max(4, node.label.length);
  return { width: Math.round(Math.min(240, Math.max(132, chars * 7.6 + 44))), height: NODE_HEIGHT };
}

export function defaultDirection(kind: DiagramKind): "LR" | "TB" {
  return kind === "data_model" || kind === "deployment" ? "TB" : "LR";
}

export function layoutDiagram(spec: Pick<DiagramSpec, "kind" | "nodes" | "edges" | "groups">, direction?: "LR" | "TB"): LayoutResult {
  const rankdir = direction ?? defaultDirection(spec.kind);
  const nodeIds = new Set(spec.nodes.map((n) => n.id));
  const groupLabels = new Map(spec.groups.map((g) => [g.id, g.label]));
  const usedGroups = new Set(spec.nodes.map((n) => n.group).filter((g): g is string => !!g && groupLabels.has(g)));

  const g = new dagre.graphlib.Graph({ compound: true, multigraph: true });
  g.setGraph({ rankdir, nodesep: 36, ranksep: 72, edgesep: 18, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const gid of usedGroups) g.setNode(GROUP_PREFIX + gid, { label: groupLabels.get(gid) ?? gid });
  for (const n of spec.nodes) {
    g.setNode(n.id, { label: n.label, ...nodeSize(n) });
    if (n.group && usedGroups.has(n.group)) g.setParent(n.id, GROUP_PREFIX + n.group);
  }
  const validEdges: DiagramEdge[] = spec.edges.filter(
    (e) => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target,
  );
  validEdges.forEach((e, i) => g.setEdge(e.source, e.target, { label: e.label ?? "" }, `e${i}`));

  dagre.layout(g);

  // absolute top-left boxes
  const box = (id: string) => {
    const v = g.node(id) as { x: number; y: number; width: number; height: number } | undefined;
    if (!v) return { x: 0, y: 0, width: 0, height: 0 };
    return { x: v.x - v.width / 2, y: v.y - v.height / 2, width: v.width, height: v.height };
  };

  const groupBoxes = new Map<string, { x: number; y: number; width: number; height: number }>();
  for (const gid of usedGroups) {
    const b = box(GROUP_PREFIX + gid);
    groupBoxes.set(gid, {
      x: b.x - GROUP_PAD_X,
      y: b.y - GROUP_PAD_TOP,
      width: b.width + GROUP_PAD_X * 2,
      height: b.height + GROUP_PAD_TOP + GROUP_PAD_BOTTOM,
    });
  }

  const sourcePosition = rankdir === "LR" ? Position.Right : Position.Bottom;
  const targetPosition = rankdir === "LR" ? Position.Left : Position.Top;
  const nodes: UnfoldFlowNode[] = [];
  // parents must precede children in the React Flow node array
  for (const [gid, b] of groupBoxes) {
    nodes.push({
      id: GROUP_PREFIX + gid,
      type: "unfoldGroup",
      position: { x: b.x, y: b.y },
      data: { label: groupLabels.get(gid) ?? gid },
      style: { width: b.width, height: b.height },
      width: b.width,
      height: b.height,
      selectable: false,
      draggable: false,
      focusable: false,
      zIndex: 0,
    });
  }
  for (const n of spec.nodes) {
    const b = box(n.id);
    const parent = n.group && groupBoxes.get(n.group);
    nodes.push({
      id: n.id,
      type: "unfold",
      position: parent ? { x: b.x - parent.x, y: b.y - parent.y } : { x: b.x, y: b.y },
      ...(parent ? { parentId: GROUP_PREFIX + n.group } : {}),
      data: { node: n },
      sourcePosition,
      targetPosition,
      width: b.width,
      height: b.height,
      draggable: false,
      zIndex: 1,
    });
  }

  const edges: UnfoldFlowEdge[] = validEdges.map((e, i) => ({
    id: `e${i}:${e.source}->${e.target}`,
    source: e.source,
    target: e.target,
    label: e.label ?? undefined,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    data: { inferred: e.inferred, kind: e.kind },
    className: e.inferred ? "edge-inferred" : undefined,
    style: e.inferred ? { strokeDasharray: "6 4" } : undefined,
    zIndex: 2,
  }));

  const graph = g.graph() as { width?: number; height?: number };
  return { nodes, edges, width: graph.width ?? 0, height: graph.height ?? 0 };
}

/** Accessible text alternative for a diagram. */
export function describeDiagram(spec: Pick<DiagramSpec, "nodes" | "edges">): string[] {
  const label = new Map(spec.nodes.map((n) => [n.id, n.label]));
  return spec.edges
    .filter((e) => label.has(e.source) && label.has(e.target))
    .map(
      (e) =>
        `${label.get(e.source)} → ${label.get(e.target)}${e.label ? ` (${e.label})` : ""}${e.inferred ? " [inferred]" : ""}`,
    );
}
