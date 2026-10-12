"use client";

import Link from "next/link";
import { useSession } from "./session-provider";

export function ContinueSession() {
  const { session } = useSession();
  return (
    session?.projects?.length ? <div className="mt-12 border-t border-line pt-6"><p className="font-mono text-[11px] uppercase tracking-[.12em] text-ink-faint">Continue this session</p><div className="flex flex-wrap gap-2">{session.projects.slice(0, 4).map((p) => <Link key={p.id} href={`/projects/${encodeURIComponent(p.id)}`} className="rounded-full border border-line-strong bg-panel px-4 py-2 text-[12.5px] text-ink-soft no-underline hover:border-blueprint hover:text-blueprint">{p.name || "Untitled project"} · {p.status}</Link>)}</div></div> : null
  );
}
