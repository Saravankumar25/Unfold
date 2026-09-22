import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "primary" | "ghost" | "chip" | "chip-signal" | "danger";
type Size = "sm" | "md" | "lg" | "icon";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const variants: Record<Variant, string> = {
  default:
    "border border-line-strong bg-panel text-ink hover:border-blueprint hover:text-blueprint font-semibold rounded-s",
  primary:
    "border border-blueprint bg-blueprint text-paper hover:bg-blueprint-deep hover:border-blueprint-deep font-semibold rounded-s",
  ghost: "border border-transparent bg-transparent text-ink-soft hover:text-blueprint hover:bg-paper-dim rounded-s",
  chip: "border border-line-strong bg-transparent text-ink-soft hover:border-blueprint hover:text-blueprint font-medium rounded-full",
  "chip-signal": "border border-signal bg-transparent text-signal hover:bg-signal-soft font-medium rounded-full",
  danger: "border border-danger bg-transparent text-danger hover:bg-danger-soft font-semibold rounded-s",
};

const sizes: Record<Size, string> = {
  sm: "text-[12.5px] px-3 py-1.5 gap-1.5",
  md: "text-[13px] px-3.5 py-2 gap-2",
  lg: "text-[14.5px] px-[22px] py-3 gap-2",
  icon: "h-9 w-9 justify-center p-0",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "default", size = "md", type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        "inline-flex cursor-pointer items-center whitespace-nowrap font-sans transition-colors duration-150",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal",
        "disabled:cursor-not-allowed disabled:opacity-55",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
});
