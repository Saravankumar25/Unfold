"use client";

import { useId, useRef, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

/** Radio-group segmented control with roving arrow-key navigation. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  size = "md",
  className,
}: {
  value: T;
  onChange: (value: T) => void;
  options: readonly SegmentedOption<T>[];
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  const id = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (index + 1) % options.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (index - 1 + options.length) % options.length;
    else return;
    e.preventDefault();
    const opt = options[next];
    if (opt) {
      onChange(opt.value);
      refs.current[next]?.focus();
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      id={id}
      className={cn("inline-flex flex-wrap rounded-s border border-line-strong bg-panel p-0.5", className)}
    >
      {options.map((opt, i) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            title={opt.hint}
            onClick={() => onChange(opt.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={cn(
              "cursor-pointer rounded-[3px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-signal",
              size === "sm" ? "px-2.5 py-1 text-[12px]" : "px-3.5 py-2 text-[13px]",
              selected ? "bg-blueprint text-paper" : "text-ink-soft hover:text-ink",
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
