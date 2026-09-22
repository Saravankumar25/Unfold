import type { SafeHtml } from "@/lib/sanitize";

/**
 * The single place markup is injected. Only accepts `SafeHtml`, which can only be produced by `lib/sanitize`
 * (DOMPurify) — i.e. Shiki output and strict-mode Mermaid SVG. Repository/LLM text never comes through here.
 */
export function TrustedHtml({ html, className }: { html: SafeHtml; className?: string }) {
  // eslint-disable-next-line react/no-danger -- `SafeHtml` is DOMPurify output from lib/sanitize
  return <div className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}
