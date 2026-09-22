"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useState, type CSSProperties } from "react";
import { TrustedHtml } from "@/components/app/trusted-html";
import { highlightToHtml, normalizeLanguage } from "@/lib/highlight";
import type { SafeHtml } from "@/lib/sanitize";
import { cn } from "@/lib/utils";

/** Code with lazy Shiki highlighting (sanitized) and a plain-text fallback that is always correct. */
export function CodeView({
  code,
  language,
  path,
  startLine,
  caption,
  lineNumbers,
  className,
}: {
  code: string;
  language?: string | null;
  path?: string | null;
  startLine?: number | null;
  caption?: string | null;
  lineNumbers?: boolean;
  className?: string;
}) {
  const lang = normalizeLanguage(language, path);
  const [highlighted, setHighlighted] = useState<{ code: string; lang: string; html: SafeHtml } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    highlightToHtml(code, lang).then((html) => {
      if (alive && html) setHighlighted({ code, lang, html });
    });
    return () => {
      alive = false;
    };
  }, [code, lang]);

  const html = highlighted && highlighted.code === code && highlighted.lang === lang ? highlighted.html : null;
  const showLines = lineNumbers ?? startLine != null;
  const style = { "--start-line": Math.max(0, (startLine ?? 1) - 1) } as CSSProperties;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <figure className={cn("my-[18px] mb-[26px] min-w-0", className)}>
      <div className="overflow-hidden rounded-m border border-line bg-paper-dim">
        {path || lang ? (
          <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-1.5 font-mono text-[11.5px] text-ink-faint">
            <span className="min-w-0 truncate">
              {path ?? lang}
              {path && startLine ? `:${startLine}` : ""}
            </span>
            <button
              type="button"
              onClick={copy}
              aria-label={copied ? "Copied" : "Copy code"}
              className="inline-flex cursor-pointer items-center gap-1 rounded-s px-1.5 py-0.5 hover:text-blueprint"
            >
              {copied ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
              <span className="max-sm:sr-only">{copied ? "Copied" : "Copy"}</span>
            </button>
          </div>
        ) : null}
        <div
          className={cn("shiki-host overflow-x-auto px-[18px] py-4", showLines && "with-lines")}
          style={style}
          tabIndex={0}
          aria-label={path ? `Code from ${path}` : "Code"}
        >
          {html ? (
            <TrustedHtml html={html} />
          ) : showLines ? (
            <pre className="shiki m-0 font-mono text-[13px] leading-[1.6] text-ink">
              <code>
                {code.split("\n").map((line, i) => (
                  <span key={i} className="line">
                    {line}
                    {"\n"}
                  </span>
                ))}
              </code>
            </pre>
          ) : (
            <pre className="shiki m-0 font-mono text-[13px] leading-[1.6] text-ink">
              <code>{code}</code>
            </pre>
          )}
        </div>
      </div>
      {caption ? <figcaption className="mt-2 font-mono text-[11.5px] text-ink-faint">{caption}</figcaption> : null}
    </figure>
  );
}
