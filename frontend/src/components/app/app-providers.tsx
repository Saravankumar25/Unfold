"use client";

import type { ReactNode } from "react";
import { ToastProvider } from "@/components/ui/toast";
import { SessionProvider } from "./session-provider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <SessionProvider>{children}</SessionProvider>
    </ToastProvider>
  );
}
