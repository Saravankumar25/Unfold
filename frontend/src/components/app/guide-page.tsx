"use client";

import { BookOpen, ChevronLeft, ChevronRight, Menu, MessageSquareText, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { EvidenceProvider, useEvidence } from "@/components/content/evidence-drawer";
import { CodeView } from "@/components/content/code-view";
import { InlineText } from "@/components/content/inline-text";
import { DiagramView } from "@/components/diagram/diagram-view";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/endpoints";
import type { Block, GuideSectionOut, SectionContent } from "@/lib/api/types";
import { mapResolver } from "@/lib/citations";
import { useResource } from "@/lib/hooks/use-resource";
import { cn, titleCase } from "@/lib/utils";
import { ErrorPanel } from "./error-panel";
import { ExportButton } from "./export-button";
import { ProjectReadyGate, useProject } from "./project-provider";
import { ProjectTopbar } from "./project-topbar";
import { LoadingLines } from "./skeleton";

function SectionBlocks({ projectId, section, content }: { projectId: string; section: GuideSectionOut; content: SectionContent }) {
  const { openEvidence } = useEvidence();
  const resolve = mapResolver(section.evidence);
  const cite = (label: string, ref: Parameters<typeof openEvidence>[0][number]["ref"]) => openEvidence([{ label, ref }]);
  return <div className="prose-guide">{content.blocks.map((block: Block, i) => {
    switch (block.type) {
      case "heading": return block.level === 2 ? <h2 key={i} className="mb-4 mt-9 font-serif text-[28px] font-medium">{block.text}</h2> : <h3 key={i} className="mb-3 mt-7 font-serif text-[21px] font-medium">{block.text}</h3>;
      case "paragraph": return <p key={i}><InlineText text={block.text} resolve={resolve} onCite={cite} /></p>;
      case "bullets": { const items = block.items.map((x, j) => <li key={j}><InlineText text={x} resolve={resolve} onCite={cite} /></li>); return block.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>; }
      case "callout": return <aside key={i} className={cn("my-6 border-l-2 bg-paper-dim px-5 py-4 text-[14px] leading-relaxed", block.tone === "warning" ? "border-signal" : "border-blueprint")}><p className="mb-1 font-mono text-[10px] uppercase tracking-[.12em] text-ink-faint">{block.tone}</p><InlineText text={block.text} resolve={resolve} onCite={cite} /></aside>;
      case "code": return <CodeView key={i} code={block.code} language={block.language} path={block.path ?? undefined} startLine={block.start_line ?? undefined} caption={block.caption ?? undefined} lineNumbers />;
      case "diagram": return <DiagramView key={i} projectId={projectId} diagramId={block.diagram_id} caption={block.caption} />;
      case "table": return <div key={i} className="my-6 overflow-x-auto"><table className="w-full border-collapse text-left text-[13px]"><thead><tr>{block.headers.map((h) => <th key={h} className="border-b border-line-strong bg-paper-dim px-3 py-2 font-semibold">{h}</th>)}</tr></thead><tbody>{block.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} className="border-b border-line px-3 py-2.5 align-top text-ink-soft"><InlineText text={cell} resolve={resolve} onCite={cite} /></td>)}</tr>)}</tbody></table></div>;
      case "qa": return <details key={i} className="my-3 rounded-s border border-line bg-panel px-4 py-3"><summary className="cursor-pointer font-semibold">{block.question}</summary><p className="mb-0 mt-3 text-ink-soft"><InlineText text={block.answer} resolve={resolve} onCite={cite} /></p></details>;
      case "glossary": return <dl key={i} className="grid gap-3 sm:grid-cols-2">{block.items.map((item) => <div key={item.term} className="border-l border-line-strong pl-3"><dt className="font-semibold">{item.term}</dt><dd className="m-0 mt-1 text-[13px] leading-relaxed text-ink-soft">{item.definition}</dd></div>)}</dl>;
    }
  })}</div>;
}

function GuideWorkspace() {
  const { project } = useProject();
  const guide = useResource(`guide:${project!.id}`, (signal) => api.getGuide(project!.id, signal));
  const [sectionKey, setSectionKey] = useState<string | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [variant, setVariant] = useState<SectionContent | null>(null);
  const [variantBusy, setVariantBusy] = useState(false);
  useEffect(() => { if (!sectionKey && guide.data?.sections.length) setSectionKey(guide.data.sections[0]!.key); }, [guide.data, sectionKey]);
  useEffect(() => setVariant(null), [sectionKey]);
  const section = useResource(sectionKey ? `section:${project!.id}:${sectionKey}` : null, (signal) => api.getSection(project!.id, sectionKey!, signal));
  const sections = guide.data?.sections ?? [];
  const currentIndex = sections.findIndex((s) => s.key === sectionKey);
  async function transform(kind: "simpler" | "deeper") { if (!sectionKey) return; setVariantBusy(true); try { const next = await api.sectionVariant(project!.id, sectionKey, kind); setVariant(next.content); } finally { setVariantBusy(false); } }
  const nav = <nav aria-label="Guide chapters" className="flex h-full flex-col"><div className="border-b border-line p-5"><p className="m-0 font-mono text-[10px] uppercase tracking-[.12em] text-ink-faint">Project guide</p><p className="mb-0 mt-1 font-serif text-[20px] font-medium">{guide.data?.estimated_read_minutes ?? "—"} min read</p></div><ol className="m-0 flex-1 list-none overflow-y-auto p-2">{sections.map((s, i) => <li key={s.key}><button type="button" onClick={() => { setSectionKey(s.key); setMobileNav(false); }} className={cn("flex w-full cursor-pointer items-start gap-3 rounded-s px-3 py-2.5 text-left", s.key === sectionKey ? "bg-paper-dim text-blueprint" : "text-ink-soft hover:bg-paper-dim")}><span className="mt-0.5 font-mono text-[10px] text-ink-faint">{String(i + 1).padStart(2, "0")}</span><span className="text-[13px] font-medium leading-snug">{s.title}</span></button></li>)}</ol></nav>;
  return <EvidenceProvider projectId={project!.id}>
    <ProjectTopbar project={project!} current="guide" leading={<Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open chapters" onClick={() => setMobileNav((v) => !v)}><Menu size={17} /></Button>} actions={<ExportButton start={() => api.exportGuide(project!.id)} filename={`${project!.name || "unfold"}-guide.pdf`} size="sm" />} />
    <div className="grid min-h-[calc(100vh-var(--topbar-h))] lg:grid-cols-[var(--sidebar-w)_1fr]">
      <aside className="hidden border-r border-line bg-panel lg:block">{nav}</aside>
      {mobileNav ? <aside className="fixed inset-x-0 top-[calc(var(--topbar-h)+42px)] z-30 max-h-[70vh] border-b border-line bg-panel shadow-xl lg:hidden">{nav}</aside> : null}
      <main className="min-w-0 px-5 py-9 sm:px-10 lg:px-[clamp(48px,8vw,110px)] lg:py-14">
        <div className="mx-auto max-w-[780px]">
          {guide.error ? <ErrorPanel error={guide.error} onRetry={guide.reload} title="Couldn't load the guide" /> : !guide.data || !sectionKey ? <LoadingLines label="Opening your guide…" lines={9} /> : section.error ? <ErrorPanel error={section.error} onRetry={section.reload} title="Couldn't load this chapter" /> : !section.data ? <LoadingLines label="Loading chapter…" lines={9} /> : <>
            <p className="m-0 font-mono text-[11px] uppercase tracking-[.14em] text-signal">Chapter {currentIndex + 1} · {titleCase(section.data.kind)}</p>
            <h1 className="m-0 mt-3 font-serif text-[clamp(36px,6vw,58px)] font-[470] leading-[1.05]">{section.data.title}</h1>
            {section.data.content?.summary ? <p className="mb-0 mt-5 border-l-2 border-blueprint pl-4 text-[16px] leading-relaxed text-ink-soft">{section.data.content.summary}</p> : null}
            <div className="mt-6 flex flex-wrap gap-2"><Button size="sm" variant="chip" disabled={variantBusy} onClick={() => void transform("simpler")}><Sparkles size={13} />Explain simpler</Button><Button size="sm" variant="chip" disabled={variantBusy} onClick={() => void transform("deeper")}><BookOpen size={13} />Go deeper</Button><Link href={`/projects/${project!.id}/chat?section=${encodeURIComponent(section.data.key)}`} className="inline-flex items-center gap-1.5 rounded-full border border-signal px-3 py-1.5 text-[12.5px] font-medium text-signal no-underline hover:bg-signal-soft"><MessageSquareText size={13} />Ask about this</Link></div>
            <div className="mt-9">{(variant ?? section.data.content) ? <SectionBlocks projectId={project!.id} section={section.data} content={(variant ?? section.data.content)!} /> : <p className="text-ink-faint">This chapter is still being assembled.</p>}</div>
            <div className="mt-12 flex items-center justify-between border-t border-line pt-5"><Button variant="ghost" disabled={currentIndex <= 0} onClick={() => setSectionKey(sections[currentIndex - 1]!.key)}><ChevronLeft size={15} />Previous</Button><Button variant="ghost" disabled={currentIndex >= sections.length - 1} onClick={() => setSectionKey(sections[currentIndex + 1]!.key)}>Next<ChevronRight size={15} /></Button></div>
          </>}
        </div>
      </main>
    </div>
  </EvidenceProvider>;
}

export function GuidePage() { return <ProjectReadyGate>{() => <GuideWorkspace />}</ProjectReadyGate>; }
