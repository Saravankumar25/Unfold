import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-s bg-line", className)} />;
}

/** Screen-reader friendly loading block. */
export function LoadingLines({ label = "Loading…", lines = 4, className }: { label?: string; lines?: number; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={cn("flex flex-col gap-2.5", className)}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn("h-3", i % 3 === 2 ? "w-3/5" : "w-full")} />
      ))}
    </div>
  );
}
