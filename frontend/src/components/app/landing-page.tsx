"use client";

import { ArrowRight, BookOpen, GitBranch, LockKeyhole, MessageSquareText, Network, Upload, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { api } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import type { TechnicalLevel } from "@/lib/api/types";
import { validateGithubUrl, validateZipFile } from "@/lib/validation";
import { ThemeToggle } from "./theme-toggle";
import { useSession } from "./session-provider";

const stages = ["Inspect repository", "Reconstruct workflows", "Map architecture", "Ground every claim"];

export function LandingPage() {
  const router = useRouter();
  const { session } = useSession();
  const fileRef = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [level, setLevel] = useState<TechnicalLevel>("intermediate");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    let githubUrl = url.trim();
    if (file) {
      const validation = validateZipFile(file);
      if (!validation.ok) return setError(validation.error);
    } else {
      const validation = validateGithubUrl(url);
      if (!validation.ok) return setError(validation.error);
      githubUrl = validation.value.url;
    }
    setBusy(true);
    setError(null);
    try {
      const created = file
        ? await api.createZipProject({ file, technical_level: level, instruction: instruction.trim() || null })
        : await api.createGithubProject({ url: githubUrl, technical_level: level, instruction: instruction.trim() || null });
      router.push(`/projects/${encodeURIComponent(created.project_id)}`);
    } catch (cause) {
      setError(errorMessage(cause));
      setBusy(false);
    }
  }

  return (
    <main className="theme-landing min-h-screen overflow-hidden">
      <div className="crease-field" aria-hidden />
      <header className="relative z-10 mx-auto flex max-w-[1240px] items-center justify-between px-5 py-6 sm:px-10 lg:px-16">
        <Link href="/" className="wordmark text-[22px] text-ink no-underline">Unfold</Link>
        <div className="flex items-center gap-2">
          <a href="#inside" className="hidden px-3 py-2 text-[13px] text-ink-soft hover:text-ink sm:block">What you get</a>
          <ThemeToggle />
        </div>
      </header>

      <section className="relative z-[1] mx-auto grid max-w-[1240px] gap-12 px-5 pb-16 pt-10 sm:px-10 lg:grid-cols-[1.08fr_.92fr] lg:px-16 lg:pb-24 lg:pt-20">
        <div>
          <p className="mb-6 flex items-center gap-3 font-mono text-[12px] uppercase tracking-[.16em] text-blueprint"><span className="h-px w-7 bg-blueprint" />repository → understanding</p>
          <h1 className="m-0 max-w-[760px] font-serif text-[clamp(54px,8.5vw,116px)] font-[390] leading-[.9] tracking-[-.035em]">
            Prepare your <span className="block font-[640] not-italic text-blueprint">project.</span>
          </h1>
          <p className="mb-0 mt-8 max-w-[570px] text-[clamp(16px,1.8vw,20px)] leading-[1.6] text-ink-soft">
            Unfold studies the codebase, reconstructs how it works, and turns it into a visual guide you can explain with confidence.
          </p>
          <div className="mt-8 flex flex-wrap gap-x-6 gap-y-3 font-mono text-[11px] uppercase tracking-[.08em] text-ink-faint">
            <span className="flex items-center gap-2"><LockKeyhole size={13} className="text-signal" />Static analysis only</span>
            <span className="flex items-center gap-2"><GitBranch size={13} className="text-signal" />Public repos & ZIPs</span>
            <span className="flex items-center gap-2"><BookOpen size={13} className="text-signal" />Evidence grounded</span>
          </div>
        </div>

        <form onSubmit={submit} className="self-start border border-line-strong bg-panel/90 p-5 shadow-[12px_12px_0_var(--paper-dim)] backdrop-blur sm:p-7">
          <div className="mb-6 flex items-start justify-between gap-4">
            <div><p className="m-0 font-mono text-[11px] uppercase tracking-[.12em] text-signal">Start here</p><h2 className="m-0 mt-1 font-serif text-[27px] font-medium">What should we unfold?</h2></div>
            <span className="font-serif text-[42px] leading-none text-line-strong">01</span>
          </div>
          <label className="mb-2 block text-[12px] font-semibold text-ink-soft" htmlFor="repo">Public GitHub repository</label>
          <div className="flex rounded-s border border-line-strong bg-paper focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-signal">
            <span className="flex items-center pl-3 text-ink-faint"><GitBranch size={16} /></span>
            <input id="repo" value={url} disabled={Boolean(file)} onChange={(e) => { setUrl(e.target.value); setError(null); }} placeholder="github.com/owner/repository" className="min-w-0 flex-1 bg-transparent px-3 py-3 font-mono text-[13px] outline-none placeholder:text-ink-faint disabled:opacity-50" />
          </div>
          <div className="my-4 flex items-center gap-3 text-[11px] text-ink-faint"><span className="h-px flex-1 bg-line" />OR<span className="h-px flex-1 bg-line" /></div>
          <input ref={fileRef} type="file" accept=".zip,application/zip" aria-label="Project ZIP file" tabIndex={-1} className="sr-only" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setUrl(""); setError(null); }} />
          <button type="button" onClick={() => fileRef.current?.click()} className="flex w-full cursor-pointer items-center justify-center gap-2 rounded-s border border-dashed border-line-strong bg-paper-dim px-4 py-3 text-[13px] font-medium text-ink-soft hover:border-blueprint hover:text-blueprint">
            <Upload size={15} /> {file ? file.name : "Choose a project ZIP"}
          </button>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <label className="text-[12px] font-semibold text-ink-soft">Your level<select value={level} onChange={(e) => setLevel(e.target.value as TechnicalLevel)} className="mt-2 w-full rounded-s border border-line-strong bg-paper px-3 py-2.5 text-[13px] text-ink"><option value="beginner">Beginner</option><option value="intermediate">Intermediate</option><option value="experienced">Experienced</option></select></label>
            <label className="text-[12px] font-semibold text-ink-soft">Focus <span className="font-normal text-ink-faint">(optional)</span><input value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="e.g. architecture" className="mt-2 w-full rounded-s border border-line-strong bg-paper px-3 py-2.5 text-[13px] text-ink placeholder:text-ink-faint" /></label>
          </div>
          {error ? <p role="alert" className="mb-0 mt-4 border-l-2 border-danger pl-3 text-[12.5px] text-danger">{error}</p> : null}
          <button type="submit" disabled={busy} className="mt-6 flex w-full cursor-pointer items-center justify-between rounded-s bg-blueprint px-5 py-3.5 text-[14px] font-semibold text-paper transition hover:bg-blueprint-deep disabled:cursor-wait disabled:opacity-60">
            {busy ? "Opening the folds…" : "Unfold this project"}<ArrowRight size={17} />
          </button>
          <p className="mb-0 mt-3 text-center font-mono text-[10.5px] text-ink-faint">No sign-in · temporary private session · nothing is executed</p>
        </form>
      </section>

      <div className="relative z-[1] border-y border-line bg-paper-dim/80">
        <div className="mx-auto flex max-w-[1112px] flex-wrap items-center gap-x-7 gap-y-3 px-5 py-4 font-mono text-[11px] text-ink-faint sm:px-10">
          <span className="text-signal">✛</span>{stages.map((stage, i) => <span key={stage} className="flex items-center gap-2"><b className="font-normal text-blueprint">0{i + 1}</b>{stage}</span>)}
        </div>
      </div>

      <section id="inside" className="relative z-[1] mx-auto max-w-[1112px] px-5 py-20 sm:px-10 lg:py-28">
        <div className="mb-12 flex flex-wrap items-end justify-between gap-6"><h2 className="m-0 max-w-[560px] font-serif text-[clamp(34px,5vw,58px)] font-[460] leading-[1.02]">Not another README generator.</h2><p className="m-0 max-w-[390px] text-[15px] leading-relaxed text-ink-soft">A connected learning environment built from the real repository—so every explanation has receipts.</p></div>
        <div className="grid border border-line md:grid-cols-3">
          {([
            [Network, "See the system", "Interactive architecture, dependency and workflow diagrams expose how the pieces connect."],
            [BookOpen, "Learn the reasoning", "A chaptered guide explains structure, tradeoffs, security, infrastructure and failure modes."],
            [MessageSquareText, "Question the code", "Ask follow-ups, rehearse interviews, and open the exact evidence behind each answer."],
          ] as [LucideIcon, string, string][]).map(([Icon, title, copy], i) => <article key={title} className="border-line bg-paper p-7 max-md:border-b md:border-r last:border-0 lg:p-9"><p className="m-0 font-mono text-[11px] text-signal">0{i + 1}</p><Icon className="my-8 text-blueprint" size={30} strokeWidth={1.4} /><h3 className="m-0 font-serif text-[23px] font-medium">{title}</h3><p className="mb-0 mt-3 text-[14px] leading-[1.65] text-ink-soft">{copy}</p></article>)}
        </div>
        {session?.projects?.length ? <div className="mt-12 border-t border-line pt-6"><p className="font-mono text-[11px] uppercase tracking-[.12em] text-ink-faint">Continue this session</p><div className="flex flex-wrap gap-2">{session.projects.slice(0, 4).map((p) => <Link key={p.id} href={`/projects/${encodeURIComponent(p.id)}`} className="rounded-full border border-line-strong bg-panel px-4 py-2 text-[12.5px] text-ink-soft no-underline hover:border-blueprint hover:text-blueprint">{p.name || "Untitled project"} · {p.status}</Link>)}</div></div> : null}
      </section>
      <footer className="relative z-[1] mx-auto flex max-w-[1112px] items-center justify-between border-t border-line px-5 py-8 text-[12px] text-ink-faint sm:px-10"><span className="wordmark text-[16px] text-ink">Unfold</span><span>Prepare your project.</span></footer>
    </main>
  );
}
