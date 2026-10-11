"use client";

import { ArrowRight, Check, CircleDashed, GitBranch, LoaderCircle, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";
import { useProjectProgress } from "@/lib/hooks/use-project-progress";
import { projectDisplayName } from "@/lib/utils";
import { ExpiryBanner } from "./expiry-notice";
import { ProjectLoaded, useProject } from "./project-provider";
import { ThemeToggle } from "./theme-toggle";

function ProgressContent() {
  const { project, reload } = useProject();
  const progress = useProjectProgress(project!.id, project!.job);
  const settled = project!.job.status === "succeeded" || project!.job.status === "failed";
  // The project was loaded while the job was queued or running: refresh it once polling has seen the end, so the guide and chat gates open.
  useEffect(() => { if (progress.outcome !== "running" && !settled) reload(); }, [progress.outcome, settled, reload]);
  const percent = progress.total ? Math.round((progress.index / progress.total) * 100) : 0;
  const ready = project!.job.status === "succeeded" || progress.outcome === "ready";
  const failed = project!.job.status === "failed" || progress.outcome === "failed";
  return (
    <main className="min-h-screen bg-paper">
      <header className="border-b border-line bg-panel"><div className="mx-auto flex min-h-[62px] max-w-[1112px] items-center justify-between px-5 sm:px-8"><Link href="/" className="wordmark text-[19px] text-ink no-underline">Unfold</Link><ThemeToggle /></div></header>
      <section className="mx-auto max-w-[840px] px-5 py-12 sm:px-8 sm:py-20">
        <p className="m-0 flex items-center gap-2 font-mono text-[11px] uppercase tracking-[.13em] text-blueprint"><GitBranch size={13} />{projectDisplayName(project!)}</p>
        <h1 className="m-0 mt-4 font-serif text-[clamp(38px,7vw,68px)] font-[460] leading-[1.02]">{ready ? "Your project is unfolded." : failed ? "The fold stopped here." : "Reading between the lines."}</h1>
        <p className="mb-0 mt-5 max-w-[620px] text-[15px] leading-relaxed text-ink-soft">{ready ? "The guide, architecture map, and grounded project chat are ready to explore." : failed ? (project!.job.error_message || "The analysis could not be completed. Try again or use a different source.") : "Unfold is tracing the structure, relationships, and decisions in your repository. You can leave this tab open; progress updates live."}</p>

        <div className="mt-10 border border-line-strong bg-panel p-5 sm:p-7">
          <div className="flex items-center justify-between gap-4"><span className="font-mono text-[12px] text-ink-soft">{ready ? "Analysis complete" : failed ? "Analysis failed" : progress.label}</span><span className="font-mono text-[12px] text-blueprint">{ready ? "100" : percent}%</span></div>
          <div className="mt-4 h-1 overflow-hidden bg-paper-dim"><div className={`h-full transition-[width] duration-500 ${failed ? "bg-danger" : "bg-blueprint"}`} style={{ width: `${ready ? 100 : Math.max(3, percent)}%` }} /></div>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            {["Repository secured", "System reconstructed", "Guide assembled"].map((label, i) => { const done = ready || percent >= [10, 55, 90][i]!; return <div key={label} className="flex items-center gap-2 text-[12px] text-ink-soft">{done ? <Check size={14} className="text-ok" /> : i === Math.floor(percent / 35) ? <LoaderCircle size={14} className="animate-spin text-signal" /> : <CircleDashed size={14} className="text-ink-faint" />}{label}</div>; })}
          </div>
          {!ready && !failed && progress.connection !== "live" && progress.connection !== "connecting" ? <p role="status" className="mb-0 mt-5 border-t border-line pt-4 text-[12.5px] leading-relaxed text-ink-soft">{progress.connection === "waking" ? "Waking the service up. The first request after a quiet period can take a little longer." : progress.connection === "lost" ? "Can't reach the service. Still trying; this page will recover on its own." : "Connection interrupted. Retrying…"}</p> : null}
          {progress.history.length > 1 ? <ol aria-label="Pipeline stages" className="m-0 mt-6 grid list-none gap-x-6 gap-y-1.5 border-t border-line p-0 pt-5 font-mono text-[11.5px] sm:grid-cols-2">{progress.history.map((s) => { const current = !ready && !failed && s.stage === progress.stage; return <li key={s.stage} aria-current={current ? "step" : undefined} className={current ? "flex items-center gap-2 text-ink" : "flex items-center gap-2 text-ink-soft"}>{current ? <LoaderCircle size={12} className="shrink-0 animate-spin text-signal" aria-hidden /> : <Check size={12} className="shrink-0 text-ok" aria-hidden />}{s.label}</li>; })}</ol> : null}
          {progress.detail ? <p className="mb-0 mt-5 border-t border-line pt-4 font-mono text-[11px] leading-relaxed text-ink-faint">{progress.detail}</p> : null}
        </div>

        {ready ? <div className="mt-6 grid gap-3 sm:grid-cols-2"><Link href={`/projects/${project!.id}/guide`} className="flex items-center justify-between rounded-s bg-blueprint px-5 py-4 text-[14px] font-semibold text-paper no-underline hover:bg-blueprint-deep">Open project guide <ArrowRight size={17} /></Link><Link href={`/projects/${project!.id}/architecture`} className="flex items-center justify-between rounded-s border border-line-strong bg-panel px-5 py-4 text-[14px] font-semibold text-ink no-underline hover:border-blueprint hover:text-blueprint">Explore architecture <ArrowRight size={17} /></Link></div> : null}
        {!ready && !failed ? <div className="mt-7 flex items-start gap-3 text-[12px] leading-relaxed text-ink-faint"><ShieldCheck size={16} className="mt-0.5 shrink-0 text-signal" /><p className="m-0">Repository files are inspected as data, never executed. Secrets are redacted before indexing.</p></div> : null}
        <ExpiryBanner className="mt-8" />
      </section>
    </main>
  );
}

export function ProjectProgressPage() {
  return <ProjectLoaded>{() => <ProgressContent />}</ProjectLoaded>;
}
