"use client";

import { useMemo } from "react";
import type { CitationResolver } from "@/lib/citations";
import { parseMarkdown } from "@/lib/markdown";
import { CodeView } from "./code-view";
import { InlineText, type CiteHandler } from "./inline-text";
import { MermaidView } from "./mermaid-view";

/** Chat answers: markdown-lite AST → React (no raw HTML). */
export function MarkdownView({
  text,
  resolve,
  onCite,
}: {
  text: string;
  resolve?: CitationResolver;
  onCite?: CiteHandler;
}) {
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  return (
    <div className="prose-chat">
      {blocks.map((b, i) => {
        switch (b.type) {
          case "paragraph":
            return (
              <p key={i}>
                <InlineText text={b.text} resolve={resolve} onCite={onCite} />
              </p>
            );
          case "heading": {
            const cls = "m-0 mb-2 mt-3 font-serif font-medium first:mt-0";
            return b.level === 1 ? (
              <h3 key={i} className={`${cls} text-[17px]`}>
                <InlineText text={b.text} resolve={resolve} onCite={onCite} />
              </h3>
            ) : (
              <h4 key={i} className={`${cls} text-[15.5px]`}>
                <InlineText text={b.text} resolve={resolve} onCite={onCite} />
              </h4>
            );
          }
          case "list": {
            const items = b.items.map((item, j) => (
              <li key={j}>
                <InlineText text={item} resolve={resolve} onCite={onCite} />
              </li>
            ));
            return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
          }
          case "quote":
            return (
              <blockquote key={i} className="m-0 mb-2.5 border-l-2 border-signal pl-3 text-ink-soft">
                <InlineText text={b.text} resolve={resolve} onCite={onCite} />
              </blockquote>
            );
          case "code":
            return <CodeView key={i} code={b.code} language={b.language} className="my-2.5 mb-2.5" />;
          case "mermaid":
            return (
              <div key={i} className="my-2.5 rounded-m border border-line bg-panel p-3">
                <MermaidView source={b.code} label="Diagram from the answer" />
              </div>
            );
          case "rule":
            return <hr key={i} className="my-3 border-0 border-t border-line" />;
          default:
            return null;
        }
      })}
    </div>
  );
}
