/**
 * Markdown-lite for chat answers: paragraphs, headings (#–###), ordered/unordered lists,
 * block quotes, rules, fenced code (``` lang) and ```mermaid fences. Inline markup is handled by
 * `inline.ts`. Produces an AST — rendering never uses raw HTML. Tolerates partial (streaming) input:
 * an unterminated fence is reported with `closed: false`.
 */

export type MdBlock =
  | { type: "paragraph"; text: string }
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "quote"; text: string }
  | { type: "code"; language: string; code: string; closed: boolean }
  | { type: "mermaid"; code: string; closed: boolean }
  | { type: "rule" };

const FENCE_RE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const UL_RE = /^\s*[-*+]\s+(.*)$/;
const OL_RE = /^\s*\d+[.)]\s+(.*)$/;
const RULE_RE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;

export function parseMarkdown(src: string): MdBlock[] {
  const lines = (src ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks: MdBlock[] = [];
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      blocks.push({ type: "paragraph", text: para.join(" ").trim() });
      para = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";

    const fence = FENCE_RE.exec(line);
    if (fence) {
      flushPara();
      const marker = fence[1] ?? "```";
      const lang = (fence[2] ?? "").toLowerCase();
      const body: string[] = [];
      i++;
      let closed = false;
      while (i < lines.length) {
        const l = lines[i] ?? "";
        if (l.trim() === marker) {
          closed = true;
          i++;
          break;
        }
        body.push(l);
        i++;
      }
      const code = body.join("\n");
      if (lang === "mermaid") blocks.push({ type: "mermaid", code, closed });
      else blocks.push({ type: "code", language: lang, code, closed });
      continue;
    }

    if (line.trim() === "") {
      flushPara();
      i++;
      continue;
    }

    if (RULE_RE.test(line)) {
      flushPara();
      blocks.push({ type: "rule" });
      i++;
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      flushPara();
      const level = Math.min(3, (heading[1] ?? "#").length) as 1 | 2 | 3;
      blocks.push({ type: "heading", level, text: (heading[2] ?? "").trim() });
      i++;
      continue;
    }

    if (UL_RE.test(line) || OL_RE.test(line)) {
      flushPara();
      const ordered = !UL_RE.test(line);
      const re = ordered ? OL_RE : UL_RE;
      const items: string[] = [];
      while (i < lines.length) {
        const l = lines[i] ?? "";
        const m = re.exec(l);
        if (m) {
          items.push((m[1] ?? "").trim());
          i++;
        } else if (items.length && /^\s{2,}\S/.test(l)) {
          items[items.length - 1] += ` ${l.trim()}`;
          i++;
        } else break;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    if (/^\s*>/.test(line)) {
      flushPara();
      const q: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i] ?? "")) {
        q.push((lines[i] ?? "").replace(/^\s*>\s?/, ""));
        i++;
      }
      blocks.push({ type: "quote", text: q.join(" ").trim() });
      continue;
    }

    para.push(line.trim());
    i++;
  }
  flushPara();
  return blocks;
}
