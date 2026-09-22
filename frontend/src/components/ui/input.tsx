import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cn(
        "w-full min-w-0 rounded-s border border-line-strong bg-panel px-3.5 py-2.5 text-[14px] text-ink",
        "placeholder:text-ink-faint focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-signal",
        "aria-[invalid=true]:border-danger",
        className,
      )}
      {...props}
    />
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn(
          "w-full min-w-0 rounded-s border border-line-strong bg-panel px-3.5 py-2.5 text-[14px] leading-relaxed text-ink",
          "placeholder:text-ink-faint focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-signal",
          "aria-[invalid=true]:border-danger",
          className,
        )}
        {...props}
      />
    );
  },
);
