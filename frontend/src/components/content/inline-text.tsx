"use client";

import { Fragment, useMemo, type ReactNode } from "react";
import type { EvidenceRef } from "@/lib/api/types";
import { evidenceLocation, type CitationResolver } from "@/lib/citations";
import { parseInline, type InlineNode } from "@/lib/inline";
import { cn } from "@/lib/utils";

export type CiteHandler = (label: string, ref: EvidenceRef) => void;

export function CiteChip({
  label,
  refItem,
  onCite,
  inverted,
}: {
  label: string;
  refItem: EvidenceRef | null;
  onCite?: CiteHandler;
  inverted?: boolean;
}) {
  if (!refItem || !onCite) {
    return (
      <span className="cite-missing font-mono text-[0.72em] text-ink-faint" title="Evidence reference unavailable">
        [{label}]
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onCite(label, refItem)}
      aria-label={`Evidence ${label}: ${evidenceLocation(refItem)}`}
      title={evidenceLocation(refItem)}
      className={cn(
        "cite mx-[1px] inline-flex -translate-y-[0.35em] cursor-pointer items-center rounded-full border px-[5px] py-0 align-baseline font-mono text-[0.66em] leading-[1.45] font-medium",
        inverted
          ? "border-paper/50 text-paper hover:bg-paper/15"
          : "border-line-strong bg-panel text-blueprint hover:border-blueprint hover:bg-paper-dim",
      )}
    >
      {label}
    </button>
  );
}

function renderNodes(nodes: InlineNode[], resolve: CitationResolver, onCite: CiteHandler | undefined, inverted: boolean): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.type) {
      case "text":
        return <Fragment key={i}>{n.text}</Fragment>;
      case "strong":
        return <strong key={i} className="font-semibold">{renderNodes(n.children, resolve, onCite, inverted)}</strong>;
      case "em":
        return <em key={i}>{renderNodes(n.children, resolve, onCite, inverted)}</em>;
      case "code":
        return <code key={i}>{n.text}</code>;
      case "cite":
        return <CiteChip key={i} label={n.label} refItem={resolve(n.label)} onCite={onCite} inverted={inverted} />;
      default:
        return null;
    }
  });
}

const noResolve: CitationResolver = () => null;

/** Inline markup (`**b**`, `*em*`, `` `code` ``, `[E3]`) rendered as React nodes — never as HTML. */
export function InlineText({
  text,
  resolve = noResolve,
  onCite,
  inverted = false,
}: {
  text: string;
  resolve?: CitationResolver;
  onCite?: CiteHandler;
  inverted?: boolean;
}) {
  const nodes = useMemo(() => parseInline(text), [text]);
  return <>{renderNodes(nodes, resolve, onCite, inverted)}</>;
}
