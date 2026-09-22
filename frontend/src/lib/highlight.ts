/**
 * Lazy Shiki highlighting (client only). Shiki escapes the code it tokenizes; we additionally pass the
 * generated HTML through DOMPurify before it is injected, so repository text can never become markup.
 */
import type { Highlighter } from "shiki";
import { sanitizeHtml, type SafeHtml } from "./sanitize";

const THEMES = { light: "github-light", dark: "github-dark" } as const;

const ALIASES: Record<string, string> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  ts: "typescript",
  sh: "bash",
  shell: "bash",
  zsh: "bash",
  yml: "yaml",
  md: "markdown",
  rs: "rust",
  rb: "ruby",
  kt: "kotlin",
  "c++": "cpp",
  cs: "csharp",
  dockerfile: "docker",
  tf: "hcl",
  golang: "go",
};

let highlighterPromise: Promise<Highlighter> | null = null;

async function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = (async () => {
      const shiki = await import("shiki");
      return shiki.createHighlighter({
        themes: [THEMES.light, THEMES.dark],
        langs: [],
        engine: shiki.createJavaScriptRegexEngine(),
      });
    })();
  }
  return highlighterPromise;
}

export function normalizeLanguage(language: string | null | undefined, path?: string | null): string {
  let lang = (language ?? "").trim().toLowerCase();
  if (!lang && path) {
    const base = path.split("/").pop() ?? "";
    if (/^dockerfile/i.test(base)) lang = "docker";
    else lang = (base.includes(".") ? base.split(".").pop() : "")?.toLowerCase() ?? "";
  }
  return ALIASES[lang] ?? lang;
}

/** Returns sanitized HTML, or null when highlighting is unavailable (caller shows plain text). */
export async function highlightToHtml(code: string, language: string): Promise<SafeHtml | null> {
  try {
    const highlighter = await getHighlighter();
    const shiki = await import("shiki");
    let lang = language;
    if (!lang || !(lang in shiki.bundledLanguages)) lang = "text";
    if (lang !== "text" && !highlighter.getLoadedLanguages().includes(lang)) {
      await highlighter.loadLanguage(lang as keyof typeof shiki.bundledLanguages);
    }
    const html = highlighter.codeToHtml(code, {
      lang,
      themes: THEMES,
      defaultColor: false,
    });
    return await sanitizeHtml(html);
  } catch {
    return null;
  }
}
