"use client";

import { ArrowRight, GitBranch, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { api } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/errors";
import type { TechnicalLevel } from "@/lib/api/types";
import { validateGithubUrl, validateZipFile } from "@/lib/validation";

/** The only interactive part of the landing page; everything around it is server-rendered and never hydrated. */
export function LandingForm() {
  const router = useRouter();
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
  );
}
