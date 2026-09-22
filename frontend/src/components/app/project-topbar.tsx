"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import type { ProjectOut } from "@/lib/api/types";
import { levelLabel } from "@/lib/labels";
import { cn, projectDisplayName } from "@/lib/utils";
import { ExpiryChip } from "./expiry-notice";
import { ThemeToggle } from "./theme-toggle";

export type ProjectSection = "guide" | "architecture" | "chat";

const SECTIONS: { key: ProjectSection; label: string; crumb: string; href: (id: string) => string }[] = [
  { key: "guide", label: "Guide", crumb: "Project Guide", href: (id) => `/projects/${id}/guide` },
  { key: "architecture", label: "Architecture", crumb: "Architecture", href: (id) => `/projects/${id}/architecture` },
  { key: "chat", label: "Ask Unfold", crumb: "Ask Unfold", href: (id) => `/projects/${id}/chat` },
];

export function ProjectNav({ project, current, className }: { project: ProjectOut; current: ProjectSection; className?: string }) {
  const id = encodeURIComponent(project.id);
  return (
    <nav aria-label="Project" className={cn("flex items-center gap-1", className)}>
      {SECTIONS.map((s) => (
        <Link
          key={s.key}
          href={s.href(id)}
          aria-current={s.key === current ? "page" : undefined}
          className={cn(
            "whitespace-nowrap rounded-s px-2.5 py-1.5 text-[13px] font-medium",
            s.key === current ? "bg-paper-dim text-blueprint" : "text-ink-soft hover:bg-paper-dim hover:text-ink",
          )}
        >
          {s.label}
        </Link>
      ))}
    </nav>
  );
}

/** Sticky top bar shared by the project pages (project-guide.html `.topbar`). */
export function ProjectTopbar({
  project,
  current,
  leading,
  actions,
}: {
  project: ProjectOut;
  current: ProjectSection;
  leading?: ReactNode;
  actions?: ReactNode;
}) {
  const crumb = SECTIONS.find((s) => s.key === current)?.crumb ?? "";
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-panel">
      <div className="flex min-h-[var(--topbar-h)] items-center justify-between gap-3 px-3 py-2 sm:px-[22px]">
        <div className="flex min-w-0 items-center gap-3">
          {leading}
          <Link href="/" className="wordmark shrink-0 text-[17px] text-ink no-underline">
            Unfold
          </Link>
          <p className="m-0 hidden min-w-0 truncate text-[13px] text-ink-faint md:block">
            {crumb} / <b className="font-medium text-ink-soft">{projectDisplayName(project)}</b>
          </p>
          <ProjectNav project={project} current={current} className="ml-1 hidden lg:flex" />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge tone="blueprint" className="hidden sm:inline-flex">
            {levelLabel(project.technical_level)} level
          </Badge>
          <ExpiryChip className="hidden md:inline-flex" />
          {actions}
          <ThemeToggle />
        </div>
      </div>
      <ProjectNav project={project} current={current} className="overflow-x-auto border-t border-line px-2 py-1 lg:hidden" />
    </header>
  );
}
