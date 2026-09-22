"use client";

import { useEffect, useState } from "react";
import { TrustedHtml } from "@/components/app/trusted-html";
import { LoadingLines } from "@/components/app/skeleton";
import { renderMermaid } from "@/lib/mermaid";
import type { SafeHtml } from "@/lib/sanitize";

/** Mermaid (strict securityLevel) → sanitized SVG; on syntax errors shows the source instead. */
export function MermaidView({ source, label }: { source: string; label?: string }) {
  const [result, setResult] = useState<{ source: string; svg: SafeHtml | null; error: string | null } | null>(null);

  useEffect(() => {
    let alive = true;
    renderMermaid(source).then(
      (svg) => alive && setResult({ source, svg, error: null }),
      (err: unknown) =>
        alive && setResult({ source, svg: null, error: err instanceof Error ? err.message : "Invalid diagram" }),
    );
    return () => {
      alive = false;
    };
  }, [source]);

  const current = result && result.source === source ? result : null;
  if (!current) return <LoadingLines label="Rendering diagram…" lines={3} />;
  if (current.svg) {
    return (
      <div role="img" aria-label={label ?? "Diagram"} className="mermaid-host overflow-x-auto">
        <TrustedHtml html={current.svg} />
      </div>
    );
  }
  return (
    <div>
      <p className="m-0 mb-2 text-[12.5px] text-ink-faint">This diagram could not be rendered; showing its source.</p>
      <pre className="m-0 overflow-x-auto rounded-s bg-paper-dim p-3 font-mono text-[12px] text-ink-soft">
        <code>{source}</code>
      </pre>
    </div>
  );
}
