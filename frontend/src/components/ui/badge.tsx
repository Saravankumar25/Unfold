import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Tone = "neutral" | "blueprint" | "signal" | "danger" | "ok" | "muted";

const tones: Record<Tone, string> = {
  neutral: "text-ink-soft border-line-strong",
  blueprint: "text-blueprint border-line-strong",
  signal: "text-signal border-signal/60 bg-signal-soft/50",
  danger: "text-danger border-danger/50 bg-danger-soft",
  ok: "text-ok border-ok/40 bg-ok-soft",
  muted: "text-ink-faint border-line",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  dashed?: boolean;
}

export function Badge({ className, tone = "neutral", dashed, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-[3px] font-mono text-[11px] leading-none",
        dashed && "border-dashed",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}
