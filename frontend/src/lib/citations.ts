/**
 * Resolving `[E3]` citation labels to evidence references.
 * - Guide sections/variants carry an explicit map `{"E3": EvidenceRef}` (GuideSectionOut.evidence).
 * - Chat messages carry an ordered `EvidenceRef[]`; label `E{n}` refers to the n-th entry (1-based).
 */
import type { EvidenceRef } from "./api/types";

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
