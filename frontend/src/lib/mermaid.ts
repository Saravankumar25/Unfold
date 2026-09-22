/** Lazy Mermaid rendering (client only), strict security level, SVG sanitized before injection. */
import type MermaidDefault from "mermaid";
import { sanitizeSvg, type SafeHtml } from "./sanitize";

type MermaidApi = typeof MermaidDefault;

let mermaidPromise: Promise<MermaidApi> | null = null;
let initializedTheme: string | null = null;
let counter = 0;

function prefersDark(): boolean {
  if (typeof window === "undefined") return false;
  const forced = document.documentElement.dataset.theme;
  if (forced === "dark") return true;
  if (forced === "light") return false;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

async function getMermaid(): Promise<MermaidApi> {
  if (!mermaidPromise) mermaidPromise = import("mermaid").then((m) => m.default);
  const mermaid = await mermaidPromise;
  const theme = prefersDark() ? "dark" : "neutral";
  if (initializedTheme !== theme) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme,
      htmlLabels: false,
      fontFamily: "var(--font-sans), system-ui, sans-serif",
      flowchart: { htmlLabels: false, curve: "basis" },
      sequence: { useMaxWidth: true, mirrorActors: false },
    });
    initializedTheme = theme;
  }
  return mermaid;
}

/** Render Mermaid source to sanitized SVG markup. Throws on syntax errors. */
export async function renderMermaid(source: string): Promise<SafeHtml> {
  const mermaid = await getMermaid();
  counter += 1;
  const id = `unfold-mmd-${Date.now().toString(36)}-${counter}`;
  try {
    const { svg } = await mermaid.render(id, source);
    return await sanitizeSvg(svg);
  } finally {
    // mermaid leaves a temporary container behind on failure
    document.getElementById(`d${id}`)?.remove();
  }
}
