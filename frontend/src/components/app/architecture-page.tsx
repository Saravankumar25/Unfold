"use client";

import { Boxes, Braces, GitFork, Layers3, type LucideIcon } from "lucide-react";
import { DiagramView } from "@/components/diagram/diagram-view";
import { api } from "@/lib/api/endpoints";
import { useResource } from "@/lib/hooks/use-resource";
import { titleCase } from "@/lib/utils";
import { ErrorPanel } from "./error-panel";
import { ProjectReadyGate, useProject } from "./project-provider";
import { ProjectTopbar } from "./project-topbar";
import { LoadingLines } from "./skeleton";

function ArchitectureWorkspace() {
  const { project } = useProject();
  const architecture = useResource(`architecture:${project!.id}`, (signal) => api.getArchitecture(project!.id, signal));
  const data = architecture.data;
  return <main className="min-h-screen bg-paper"><ProjectTopbar project={project!} current="architecture" /><section className="mx-auto max-w-[1112px] px-5 py-10 sm:px-8 lg:py-14">
    <div className="flex flex-wrap items-end justify-between gap-5"><div><p className="m-0 font-mono text-[11px] uppercase tracking-[.14em] text-signal">System map</p><h1 className="m-0 mt-2 font-serif text-[clamp(38px,6vw,62px)] font-[470] leading-none">Architecture</h1></div><p className="m-0 max-w-[430px] text-[14px] leading-relaxed text-ink-soft">Explore the boundaries, technologies, and relationships reconstructed from the repository.</p></div>
    {architecture.error ? <ErrorPanel className="mt-9" error={architecture.error} onRetry={architecture.reload} title="Couldn't load the architecture" /> : !data ? <LoadingLines className="mt-9" label="Mapping the architecture…" lines={8} /> : <>
      <div className="mt-9 grid grid-cols-2 border border-line bg-panel lg:grid-cols-4">{([
        [Layers3, "Subsystems", data.subsystems.length], [Braces, "Languages", data.languages.length], [Boxes, "Frameworks", data.frameworks.length], [GitFork, "Relationships", data.counts.relationships ?? data.counts.edges ?? 0],
      ] as [LucideIcon, string, number][]).map(([Icon, label, value]) => <div key={label} className="border-b border-r border-line p-5 last:border-r-0 lg:border-b-0"><Icon size={17} className="mb-5 text-signal" /><p className="m-0 font-serif text-[30px] font-medium">{value}</p><p className="m-0 mt-1 font-mono text-[10px] uppercase tracking-[.1em] text-ink-faint">{label}</p></div>)}</div>
      {data.diagram ? <DiagramView projectId={project!.id} diagram={data.diagram} /> : <div className="mt-6 border border-line bg-panel p-6 text-ink-faint">No overview diagram was generated for this repository.</div>}
      <div className="mt-10 grid gap-4 md:grid-cols-2">{data.subsystems.map((system, i) => <article key={system.key} className="border border-line bg-panel p-5"><p className="m-0 font-mono text-[10px] text-signal">{String(i + 1).padStart(2, "0")} · subsystem</p><h2 className="m-0 mt-2 font-serif text-[23px] font-medium">{system.title}</h2>{system.summary ? <p className="mb-0 mt-3 text-[13.5px] leading-relaxed text-ink-soft">{system.summary}</p> : null}<details className="mt-4 border-t border-line pt-3"><summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[.08em] text-ink-faint">{system.members.length} members</summary><ul className="mb-0 mt-3 list-none p-0 font-mono text-[11px] text-ink-soft">{system.members.slice(0, 30).map((m) => <li key={m} className="border-b border-line py-1.5 last:border-0">{m}</li>)}</ul></details></article>)}</div>
      <div className="mt-10 border-t border-line pt-6"><p className="font-mono text-[10px] uppercase tracking-[.12em] text-ink-faint">Detected stack</p><div className="mt-3 flex flex-wrap gap-2">{data.frameworks.map((f) => <span key={f} className="rounded-full border border-line-strong bg-panel px-3 py-1.5 text-[12px] text-blueprint">{titleCase(f)}</span>)}</div></div>
    </>}
  </section></main>;
}

export function ArchitecturePage() { return <ProjectReadyGate>{() => <ArchitectureWorkspace />}</ProjectReadyGate>; }
