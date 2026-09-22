"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

type ThemeChoice = "system" | "light" | "dark";
const KEY = "unfold-theme";
const listeners = new Set<() => void>();

function read(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function apply(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

function write(choice: ThemeChoice) {
  try {
    if (choice === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, choice);
  } catch {
    /* storage unavailable: the choice still applies for this page view */
  }
  apply(choice);
  for (const l of listeners) l();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const NEXT: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<ThemeChoice, string> = { system: "System theme", light: "Light theme", dark: "Dark theme" };

/** Cycles system → light → dark; persisted per browser; honoured by the `[data-theme]` tokens in globals.css. */
export function ThemeToggle({ className }: { className?: string }) {
  const choice = useSyncExternalStore(subscribe, read, () => "system" as ThemeChoice);
  useEffect(() => apply(read()), []);
  const Icon = choice === "dark" ? Moon : choice === "light" ? Sun : Monitor;
  return (
    <button
      type="button"
      onClick={() => write(NEXT[choice])}
      aria-label={`${LABEL[choice]} (switch to ${LABEL[NEXT[choice]].toLowerCase()})`}
      title={LABEL[choice]}
      className={cn(
        "inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-s border border-line-strong bg-panel text-ink-soft hover:border-blueprint hover:text-blueprint",
        className,
      )}
    >
      <Icon size={16} aria-hidden />
    </button>
  );
}
