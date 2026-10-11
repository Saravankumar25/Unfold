"use client";

import { MotionConfig } from "motion/react";
import type { ReactNode } from "react";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SessionProvider } from "./session-provider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <TooltipProvider>
          <SessionProvider>{children}</SessionProvider>
        </TooltipProvider>
      </ToastProvider>
    </MotionConfig>
  );
}
