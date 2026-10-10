/**
 * Resolving `[E3]` citation labels to evidence references.
 * - Guide sections/variants carry an explicit map `{"E3": EvidenceRef}` (GuideSectionOut.evidence).
 * - Chat messages carry an ordered `EvidenceRef[]`; label `E{n}` refers to the n-th entry (1-based).
 */
import type { EvidenceRef, ProjectOut, SectionContent } from "./api/types";
import { validateGithubUrl } from "./validation";

export type CitationResolver = (label: string) => EvidenceRef | null;

export function mapResolver(map: Record<string, EvidenceRef> | null | undefined): CitationResolver {
  return (label) => (map && map[label]) || null;
}

export function listResolver(list: EvidenceRef[] | null | undefined): CitationResolver {
  return (label) => {
    const m = /^E(\d+)$/.exec(label);
    if (!m || !list) return null;
    return list[Number(m[1]) - 1] ?? null;
  };
}

export function evidenceLocation(ref: Pick<EvidenceRef, "path" | "start_line" | "end_line">): string {
  if (!ref.path) return "source";
  if (!ref.start_line) return ref.path;
  return ref.end_line && ref.end_line !== ref.start_line
    ? `${ref.path}:${ref.start_line}–${ref.end_line}`
    : `${ref.path}:${ref.start_line}`;
}

export function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

/**
 * Claim `evidence_ids` may be citation labels ("E3") or chunk ids ("ch:…"); normalise to labelled refs using
 * the section's evidence map when possible.
 */
export function claimEvidence(
  ids: string[],
  map: Record<string, EvidenceRef>,
): { label: string; ref: EvidenceRef }[] {
  const byChunk = new Map(Object.entries(map).map(([label, ref]) => [ref.chunk_id, { label, ref }]));
  return ids.map((id) => {
    if (map[id]) return { label: id, ref: map[id] };
    const hit = byChunk.get(id);
    if (hit) return hit;
    return {
      label: id.startsWith("ch:") ? "source" : id,
      ref: { chunk_id: id, path: "", start_line: 0, end_line: 0, symbol: null, score: null },
    };
  });
}

// ---- reading experience: compact, commit-pinned citations (investigation engine §5 Publish) ---------------------

/** Where a project's files can be linked: a GitHub repo pinned to the analysed commit, or nowhere (ZIP upload). */
export interface CitationSource {
  repoUrl: string | null;
  sha: string | null;
}

const SHA_RE = /^[0-9a-f]{7,64}$/i;

export function citationSource(
  project: Pick<ProjectOut, "input_type" | "source_locator" | "commit_sha"> | null | undefined,
): CitationSource {
  if (!project || project.input_type !== "github" || !project.source_locator) return { repoUrl: null, sha: null };
  const v = validateGithubUrl(project.source_locator);
  const sha = project.commit_sha && SHA_RE.test(project.commit_sha) ? project.commit_sha : null;
  return { repoUrl: v.ok ? `https://github.com/${v.value.owner}/${v.value.repo}` : null, sha };
}

function lineSuffix(ref: Pick<EvidenceRef, "start_line" | "end_line">, sep: string, prefix: string): string {
  if (!ref.start_line) return "";
  return ref.end_line && ref.end_line !== ref.start_line
    ? `${sep}${prefix}${ref.start_line}-${prefix}${ref.end_line}`
    : `${sep}${prefix}${ref.start_line}`;
}

/** `path:L12-L40`; a long path keeps its trailing segments (`…/services/retriever.py:L12-L40`). */
export function compactLocation(ref: Pick<EvidenceRef, "path" | "start_line" | "end_line">, max = 44): string {
  const suffix = lineSuffix(ref, ":", "L");
  if (ref.path.length <= max) return ref.path + suffix;
  const parts = ref.path.split("/");
  let tail = parts.pop() ?? ref.path;
  if (tail.length >= max) return `…${tail.slice(tail.length - max + 1)}${suffix}`;
  while (parts.length && tail.length + 1 + (parts[parts.length - 1] as string).length <= max - 2) {
    tail = `${parts.pop()}/${tail}`;
  }
  return `…/${tail}${suffix}`;
}

/** GitHub blob URL at the analysed commit (`…/blob/<sha>/<path>#L12-L40`), or null when it cannot be pinned. */
export function citationHref(
  ref: Pick<EvidenceRef, "path" | "start_line" | "end_line">,
  source: CitationSource | null | undefined,
): string | null {
  if (!source?.repoUrl || !source.sha || !ref.path) return null;
  const path = ref.path.split("/").filter(Boolean).map(encodeURIComponent).join("/");
  return `${source.repoUrl}/blob/${source.sha}/${path}${lineSuffix(ref, "#", "L")}`;
}

/** Statements the verifier could not confirm carry this prefix (backend `guide.verification_models`). */
export const NOT_CONFIRMED = "Not confirmed in the code:";

export function splitNotConfirmed(text: string): { unconfirmed: boolean; text: string } {
  const t = text.trimStart();
  return t.startsWith(NOT_CONFIRMED)
    ? { unconfirmed: true, text: t.slice(NOT_CONFIRMED.length).trimStart() }
    : { unconfirmed: false, text };
}

export function isUnconfirmed(text: string): boolean {
  return splitNotConfirmed(text).unconfirmed;
}

const LABELS_RE = /\[(E\d+(?:\s*[,;]\s*E\d+)*)\]/g;

function blockTexts(b: SectionContent["blocks"][number]): string[] {
  switch (b.type) {
    case "heading":
    case "paragraph":
    case "callout":
      return [b.text];
    case "bullets":
      return b.items;
    case "table":
      return [...b.headers, ...b.rows.flat()];
    case "qa":
      return [b.question, b.answer];
    case "glossary":
      return b.items.map((g) => g.definition);
    default:
      return [];
  }
}

/** Evidence labels used in a chapter, in order of first use. */
export function citedLabels(content: SectionContent | null | undefined): string[] {
  const out = new Set<string>();
  for (const b of content?.blocks ?? []) {
    for (const text of blockTexts(b)) {
      for (const m of text.matchAll(LABELS_RE)) {
        for (const l of (m[1] as string).split(/[,;\s]+/)) if (l) out.add(l);
      }
    }
  }
  return [...out];
}

/** Distinct locations a chapter cites, ordered by path and line (the evidence appendix). */
export function appendixItems(
  content: SectionContent | null | undefined,
  evidence: Record<string, EvidenceRef>,
): { label: string; ref: EvidenceRef }[] {
  const seen = new Map<string, { label: string; ref: EvidenceRef }>();
  for (const label of citedLabels(content)) {
    const ref = evidence[label];
    if (!ref?.path) continue;
    const key = `${ref.path}:${ref.start_line}:${ref.end_line}`;
    if (!seen.has(key)) seen.set(key, { label, ref });
  }
  return [...seen.values()].sort(
    (a, b) => a.ref.path.localeCompare(b.ref.path) || a.ref.start_line - b.ref.start_line,
  );
}
