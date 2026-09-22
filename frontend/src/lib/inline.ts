/**
 * Inline markup used in guide text fields (guide/models.py): `**bold**`, `*em*`, `` `code` `` and
 * citation labels `[E3]` (also `[E3, E5]`). Produces a small AST rendered as React text nodes —
 * repository/LLM text is never interpreted as HTML.
 */

export type InlineNode =
  | { type: "text"; text: string }
  | { type: "strong"; children: InlineNode[] }
  | { type: "em"; children: InlineNode[] }
  | { type: "code"; text: string }
  | { type: "cite"; label: string };

const CITE_RE = /^\[(E\d+(?:\s*[,;]\s*E\d+)*)\]/;

function isSpace(ch: string | undefined): boolean {
  return ch === undefined || /\s/.test(ch);
}

function pushText(out: InlineNode[], text: string) {
  if (!text) return;
  const last = out[out.length - 1];
  if (last && last.type === "text") last.text += text;
  else out.push({ type: "text", text });
}

/** Find a closing `**` that is not preceded by whitespace. */
function findStrongClose(s: string, from: number): number {
  let j = s.indexOf("**", from);
  while (j !== -1) {
    if (j > from && !isSpace(s[j - 1])) return j;
    j = s.indexOf("**", j + 1);
  }
  return -1;
}

/** Find a closing single `*` (not part of `**`) not preceded by whitespace; skips code spans. */
function findEmClose(s: string, from: number): number {
  for (let j = from; j < s.length; j++) {
    if (s[j] === "`") {
      const close = s.indexOf("`", j + 1);
      if (close !== -1) j = close;
      continue;
    }
    if (s[j] !== "*") continue;
    if (s[j + 1] === "*") {
      j++;
      continue;
    }
    if (j > from && !isSpace(s[j - 1])) return j;
  }
  return -1;
}

export function parseInline(input: string, depth = 0): InlineNode[] {
  const out: InlineNode[] = [];
  const s = input ?? "";
  let i = 0;
  while (i < s.length) {
    const ch = s[i] as string;

    if (ch === "`") {
      const close = s.indexOf("`", i + 1);
      if (close > i + 1) {
        out.push({ type: "code", text: s.slice(i + 1, close) });
        i = close + 1;
        continue;
      }
      pushText(out, ch);
      i++;
      continue;
    }

    if (ch === "[") {
      const m = CITE_RE.exec(s.slice(i));
      if (m && m[1]) {
        for (const label of m[1].split(/\s*[,;]\s*/)) out.push({ type: "cite", label });
        i += m[0].length;
        continue;
      }
      pushText(out, ch);
      i++;
      continue;
    }

    if (ch === "*") {
      if (depth < 4) {
        if (s[i + 1] === "*" && !isSpace(s[i + 2])) {
          const close = findStrongClose(s, i + 2);
          if (close !== -1) {
            out.push({ type: "strong", children: parseInline(s.slice(i + 2, close), depth + 1) });
            i = close + 2;
            continue;
          }
        } else if (s[i + 1] !== "*" && !isSpace(s[i + 1])) {
          const close = findEmClose(s, i + 1);
          if (close !== -1) {
            out.push({ type: "em", children: parseInline(s.slice(i + 1, close), depth + 1) });
            i = close + 1;
            continue;
          }
        }
      }
      // unmatched delimiter(s): emit literally
      if (s[i + 1] === "*") {
        pushText(out, "**");
        i += 2;
      } else {
        pushText(out, "*");
        i++;
      }
      continue;
    }

    // plain run up to the next special character
    let j = i + 1;
    while (j < s.length && s[j] !== "`" && s[j] !== "[" && s[j] !== "*") j++;
    pushText(out, s.slice(i, j));
    i = j;
  }
  return out;
}

/** All citation labels used in a string, in order of first appearance. */
export function citationLabels(input: string): string[] {
  const seen = new Set<string>();
  const walk = (nodes: InlineNode[]) => {
    for (const n of nodes) {
      if (n.type === "cite") seen.add(n.label);
      else if (n.type === "strong" || n.type === "em") walk(n.children);
    }
  };
  walk(parseInline(input));
  return [...seen];
}

/** Plain text (for aria labels and prompts). */
export function inlineToPlain(input: string): string {
  const walk = (nodes: InlineNode[]): string =>
    nodes
      .map((n) => {
        if (n.type === "text" || n.type === "code") return n.text;
        if (n.type === "cite") return "";
        return walk(n.children);
      })
      .join("");
  return walk(parseInline(input)).replace(/\s+/g, " ").trim();
}
