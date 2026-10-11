import { describe, expect, it } from "vitest";
import { layoutDiagram } from "@/lib/diagram-layout";

describe("diagram nodes for assistive technology", () => {
  it("name every node with its label, kind and whether it is inferred", () => {
    const layout = layoutDiagram({
      kind: "architecture",
      nodes: [
        { id: "api", label: "API", kind: "service", detail: null, group: null, inferred: false },
        { id: "q", label: "Queue", kind: "queue", detail: null, group: null, inferred: true },
      ],
      edges: [{ id: "e", source: "api", target: "q", label: null, kind: "calls", inferred: false }],
      groups: [],
    } as unknown as Parameters<typeof layoutDiagram>[0]);
    expect(layout.nodes.map((n) => n.ariaLabel)).toEqual(["API, service", "Queue, queue, inferred"]);
  });
});
