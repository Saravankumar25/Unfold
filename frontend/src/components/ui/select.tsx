import { forwardRef, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/** Native select, styled — accessible and mobile-friendly by default. */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <span className={cn("relative inline-flex min-w-0 items-center", className)}>
      <select
        ref={ref}
        className="w-full min-w-0 cursor-pointer appearance-none truncate rounded-s border border-line-strong bg-panel py-1.5 pl-2.5 pr-7 text-[12.5px] text-ink focus-visible:outline-2 focus-visible:outline-signal"
        {...props}
      >
        {children}
      </select>
      <ChevronDown size={14} aria-hidden className="pointer-events-none absolute right-2 text-ink-faint" />
    </span>
  );
});
