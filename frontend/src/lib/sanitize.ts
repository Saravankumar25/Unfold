/**
 * DOMPurify wrappers — the ONLY path by which generated markup (Shiki HTML, Mermaid SVG) reaches the DOM.
 * Repository / LLM text is otherwise always rendered as React text nodes.
 *
 * Results are branded `SafeHtml`; `components/app/trusted-html.tsx` only accepts that brand, so markup that did
 * not pass through this module cannot be injected.
 */
import type DOMPurifyDefault from "dompurify";

type Purify = typeof DOMPurifyDefault;

declare const safeHtmlBrand: unique symbol;
export type SafeHtml = string & { readonly [safeHtmlBrand]: true };

let purifyPromise: Promise<Purify> | null = null;

async function getPurify(): Promise<Purify> {
  if (!purifyPromise) purifyPromise = import("dompurify").then((m) => m.default);
  return purifyPromise;
}

export async function sanitizeHtml(html: string): Promise<SafeHtml> {
  const purify = await getPurify();
  return purify.sanitize(html, {
    ALLOWED_TAGS: ["pre", "code", "span", "div"],
    ALLOWED_ATTR: ["class", "style", "tabindex"],
  }) as SafeHtml;
}

export async function sanitizeSvg(svg: string): Promise<SafeHtml> {
  const purify = await getPurify();
  return purify.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    ADD_TAGS: ["style"],
    FORBID_TAGS: ["script", "foreignObject", "iframe", "object", "embed"],
    FORBID_ATTR: ["onload", "onclick", "onerror", "href", "xlink:href"],
  }) as SafeHtml;
}
