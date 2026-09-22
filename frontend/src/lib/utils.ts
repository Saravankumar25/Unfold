import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function formatRemaining(ms: number): string {
  if (ms <= 0) return "now";
  const minutes = Math.max(1, Math.round(ms / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  if (hours < 24) return rem ? `${hours} h ${rem} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  const h = hours % 24;
  return h ? `${days} d ${h} h` : `${days} d`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Human project name from a summary/project. */
export function projectDisplayName(p: { name: string | null; source_locator: string | null }): string {
  if (p.name) return p.name;
  if (p.source_locator) {
    const cleaned = p.source_locator
      .replace(/^https?:\/\//, "")
      .replace(/\.git$/, "")
      .replace(/\/+$/, "");
    const parts = cleaned.split("/");
    return parts.slice(-2).join("/") || cleaned;
  }
  return "Untitled project";
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
