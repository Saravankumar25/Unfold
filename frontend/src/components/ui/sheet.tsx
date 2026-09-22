"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export const Sheet = DialogPrimitive.Root;
export const SheetTrigger = DialogPrimitive.Trigger;
export const SheetClose = DialogPrimitive.Close;

type Side = "right" | "left" | "center";

const sideClasses: Record<Side, string> = {
  right: "right-0 top-0 h-dvh w-full sm:w-[min(560px,92vw)] border-l",
  left: "left-0 top-0 h-dvh w-[min(300px,86vw)] border-r",
  center:
    "left-1/2 top-1/2 max-h-[88dvh] w-[min(640px,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-m border",
};

export interface SheetContentProps {
  side?: Side;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  hideTitle?: boolean;
}

export function SheetContent({ side = "right", title, description, children, className, hideTitle }: SheetContentProps) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/30 backdrop-blur-[1px] data-[state=open]:animate-in" />
      <DialogPrimitive.Content
        className={cn(
          "fixed z-50 flex flex-col border-line bg-panel text-ink shadow-[0_8px_32px_rgba(0,0,0,0.18)] outline-none",
          sideClasses[side],
          className,
        )}
      >
        <div className={cn("flex items-start justify-between gap-4 border-b border-line px-5 py-4", hideTitle && "sr-only")}>
          <div className="min-w-0">
            <DialogPrimitive.Title className="font-serif text-[18px] font-medium leading-tight">{title}</DialogPrimitive.Title>
            {description ? (
              <DialogPrimitive.Description className="mt-1 text-[12.5px] text-ink-faint">
                {description}
              </DialogPrimitive.Description>
            ) : (
              <DialogPrimitive.Description className="sr-only">{typeof title === "string" ? title : "Panel"}</DialogPrimitive.Description>
            )}
          </div>
          <DialogPrimitive.Close
            className="rounded-s p-1.5 text-ink-soft hover:bg-paper-dim hover:text-ink focus-visible:outline-2 focus-visible:outline-signal"
            aria-label="Close"
          >
            <X size={18} aria-hidden />
          </DialogPrimitive.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
