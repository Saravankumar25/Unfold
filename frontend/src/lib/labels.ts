/** Display metadata for enums (claim status, levels, chat modes, section states). */
import type { ChatMode, ClaimStatus, TechnicalLevel } from "./api/types";

export type Tone = "neutral" | "blueprint" | "signal" | "danger" | "ok" | "muted";

export const CLAIM_STATUS_META: Record<ClaimStatus, { label: string; tone: Tone; dashed: boolean; help: string }> = {
  VERIFIED: {
    label: "Verified",
    tone: "ok",
    dashed: false,
    help: "Checked against the cited source code.",
  },
  STRONGLY_INFERRED: {
    label: "Strongly inferred",
    tone: "blueprint",
    dashed: false,
    help: "Not stated directly, but strongly supported by the cited evidence.",
  },
  INFERRED: {
    label: "Inferred",
    tone: "signal",
    dashed: true,
    help: "A reasonable inference from the repository — not stated directly in the code.",
  },
  UNSUPPORTED: {
    label: "Unsupported",
    tone: "danger",
    dashed: false,
    help: "The evidence found does not support this statement. Treat it with caution.",
  },
  UNKNOWN: {
    label: "Unknown",
    tone: "muted",
    dashed: true,
    help: "The repository does not contain enough information to confirm this.",
  },
  PROPOSED: {
    label: "Proposed improvement",
    tone: "neutral",
    dashed: true,
    help: "A suggestion for improving the project — not something the repository currently does.",
  },
};

export function claimMeta(status: string) {
  return CLAIM_STATUS_META[status as ClaimStatus] ?? CLAIM_STATUS_META.UNKNOWN;
}

export const LEVEL_OPTIONS: readonly { value: TechnicalLevel; label: string; hint: string }[] = [
  { value: "beginner", label: "Beginner", hint: "New to this kind of project — explain concepts from the ground up." },
  { value: "intermediate", label: "Intermediate", hint: "Comfortable with code — focus on how this project fits together." },
  { value: "experienced", label: "Experienced", hint: "Skip the basics — go straight to design decisions and tradeoffs." },
];

export function levelLabel(level: string | null | undefined): string {
  return LEVEL_OPTIONS.find((l) => l.value === level)?.label ?? "Intermediate";
}

export const CHAT_MODE_META: Record<ChatMode, { label: string; hint: string }> = {
  default: { label: "Explain", hint: "Grounded answers at your level" },
  simpler: { label: "Simpler", hint: "Plainer language, fewer assumptions" },
  deeper: { label: "Deeper", hint: "More detail, internals and tradeoffs" },
  quiz: { label: "Quiz me", hint: "Unfold asks you questions about the project" },
  interview: { label: "Interview me", hint: "Mock technical interview grounded in this repository" },
};

export const QA_KIND_LABEL: Record<string, string> = {
  repository: "Repository",
  design: "Design",
  failure: "Failure",
  security: "Security",
  extension: "Extension",
};
