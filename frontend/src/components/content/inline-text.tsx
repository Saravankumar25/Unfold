"use client";

import { Fragment, useMemo, type ReactNode } from "react";
import type { EvidenceRef } from "@/lib/api/types";
import {
  citationHref,
  compactLocation,
  evidenceLocation,
  splitNotConfirmed,
  type CitationResolver,
  type CitationSource,
} from "@/lib/citations";
import { parseInline, type InlineNode } from "@/lib/inline";
import { cn } from "@/lib/utils";

export type CiteHandler = (label: string, ref: EvidenceRef) => void;

const CITE_BASE =
  "cite mx-[1px] inline-flex max-w-full items-center break-all rounded-s border px-[5px] align-baseline font-mono text-[0.78em] leading-[1.5]";

/**
 * A citation as its location (`path:L12-L40`), never as its `[E3]` label. A GitHub project links to the file at the
 * analysed commit; otherwise (ZIP upload, no commit) it is a non-link label that opens the in-app evidence view.
 * A label with no resolvable location renders nothing.
 */
export function CiteChip({
  label,
  refItem,
  onCite,
  inverted,
  source,
}: {
  label: string;
  refItem: EvidenceRef | null;
  onCite?: CiteHandler;
  inverted?: boolean;
  source?: CitationSource | null;
}) {
  if (!refItem || !refItem.path) return null;
  const text = compactLocation(refItem);
  const full = evidenceLocation(refItem);
  const tone = inverted
    ? "border-paper/50 text-paper hover:bg-paper/15"
    : "border-line-strong bg-panel text-blueprint hover:border-blueprint hover:bg-paper-dim";
  const href = citationHref(refItem, source);
  if (href) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        title={`${full} on GitHub at the analysed commit`}
        aria-label={`Source ${full}`}
        className={cn(CITE_BASE, tone, "no-underline")}
      >
        {text}
      </a>
    );
  }
  if (!onCite) {
    return (
      <span title={full} className={cn(CITE_BASE, "border-line text-ink-soft")}>
        {text}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onCite(label, refItem)}
      aria-label={`Evidence: ${full}`}
      title={full}
      className={cn(CITE_BASE, tone, "cursor-pointer")}
    >
      {text}
    </button>
  );
}

function renderNodes(
  nodes: InlineNode[],
  resolve: CitationResolver,
  onCite: CiteHandler | undefined,
  inverted: boolean,
  source: CitationSource | null | undefined,
): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.type) {
      case "text":
        return <Fragment key={i}>{n.text}</Fragment>;
      case "strong":
        return <strong key={i} className="font-semibold">{renderNodes(n.children, resolve, onCite, inverted, source)}</strong>;
      case "em":
        return <em key={i}>{renderNodes(n.children, resolve, onCite, inverted, source)}</em>;
      case "code":
        return <code key={i}>{n.text}</code>;
      case "cite":
        return <CiteChip key={i} label={n.label} refItem={resolve(n.label)} onCite={onCite} inverted={inverted} source={source} />;
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
  source,
}: {
  text: string;
  resolve?: CitationResolver;
  onCite?: CiteHandler;
  inverted?: boolean;
  source?: CitationSource | null;
}) {
  const nodes = useMemo(() => parseInline(text), [text]);
  return <>{renderNodes(nodes, resolve, onCite, inverted, source)}</>;
}

/** Marks a statement the verifier could not confirm (`Not confirmed in the code: …`) and renders the rest as usual. */
export function Statement(props: Parameters<typeof InlineText>[0]) {
  const { unconfirmed, text } = splitNotConfirmed(props.text);
  if (!unconfirmed) return <InlineText {...props} />;
  return (
    <span data-unconfirmed="true">
      <span className="mr-2 inline-block rounded-s border border-dashed border-signal px-1.5 font-mono text-[0.72em] uppercase tracking-[.06em] text-signal">
        Not confirmed in the code
      </span>
      <span className="italic text-ink-soft">
        <InlineText {...props} text={text} />
      </span>
    </span>
  );
}
