"use client";

import { X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

type ToastTone = "info" | "error" | "success";

interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
}

interface ToastApi {
  toast: (t: { title: string; description?: string; tone?: ToastTone; durationMs?: number }) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);

  const toast = useCallback<ToastApi["toast"]>(
    ({ title, description, tone = "info", durationMs = 6000 }) => {
      const id = nextId.current++;
      setItems((xs) => [...xs.slice(-3), { id, title, description, tone }]);
      window.setTimeout(() => dismiss(id), durationMs);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        role="status"
        className="pointer-events-none fixed bottom-4 left-1/2 z-[70] flex w-[min(420px,calc(100vw-32px))] -translate-x-1/2 flex-col gap-2"
      >
        <AnimatePresence initial={false}>
          {items.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              transition={{ duration: 0.18 }}
              className={cn(
                "pointer-events-auto flex items-start gap-3 rounded-m border bg-panel px-4 py-3 text-[13.5px] shadow-lg",
                t.tone === "error" && "border-danger/50",
                t.tone === "success" && "border-ok/50",
                t.tone === "info" && "border-line-strong",
              )}
            >
              <div className="min-w-0 flex-1">
                <p className={cn("m-0 font-semibold", t.tone === "error" && "text-danger")}>{t.title}</p>
                {t.description ? <p className="m-0 mt-0.5 text-ink-soft">{t.description}</p> : null}
              </div>
              <button
                type="button"
                aria-label="Dismiss notification"
                onClick={() => dismiss(t.id)}
                className="cursor-pointer rounded-s p-1 text-ink-faint hover:text-ink"
              >
                <X size={14} aria-hidden />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) return { toast: () => undefined };
  return ctx;
}
