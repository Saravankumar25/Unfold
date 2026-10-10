"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { createContext, useContext, useMemo, type ReactNode } from "react";
import { api } from "@/lib/api/endpoints";
import { messageForCode } from "@/lib/api/errors";
import type { ProjectOut } from "@/lib/api/types";
import { useResource } from "@/lib/hooks/use-resource";
import { useSlow } from "@/lib/hooks/use-slow";
import { ErrorPanel } from "./error-panel";
import { LoadingLines } from "./skeleton";

interface ProjectContextValue {
  projectId: string;
  project: ProjectOut | undefined;
  loading: boolean;
  error: unknown;
  reload: () => void;
}

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function ProjectProvider({ projectId, children }: { projectId: string; children: ReactNode }) {
  const res = useResource(`project:${projectId}`, (signal) => api.getProject(projectId, signal));
  const value = useMemo(
    () => ({ projectId, project: res.data, loading: res.loading, error: res.error, reload: res.reload }),
    [projectId, res.data, res.loading, res.error, res.reload],
  );
  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProject(): ProjectContextValue {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error("useProject must be used inside <ProjectProvider>");
  return ctx;
}

function CenteredPanel({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-[640px] px-4 py-16 sm:px-6">{children}</div>;
}

/** Loads the project; renders the error envelope (404 = expired/foreign) or children with the project. */
export function ProjectLoaded({ children }: { children: (project: ProjectOut) => ReactNode }) {
  const { project, error, loading, reload } = useProject();
  const slow = useSlow(!project && !error);
  if (project) return <>{children(project)}</>;
  if (error) {
    return (
      <CenteredPanel>
        <ErrorPanel error={error} title="This project isn't available" onRetry={reload}>
          <Link href="/" className="mt-3 inline-flex items-center gap-1.5 text-[13px] font-semibold text-blueprint">
            <ArrowLeft size={14} aria-hidden /> Back to Unfold
          </Link>
        </ErrorPanel>
      </CenteredPanel>
    );
  }
  return (
    <CenteredPanel>
      <LoadingLines
        label={slow ? "Waking the service up… this can take a few seconds." : loading ? "Loading project…" : "Preparing…"}
        lines={5}
      />
      {slow ? <p className="mt-4 text-[13px] text-ink-soft">Waking the service up. The first request after a quiet period can take a few seconds.</p> : null}
    </CenteredPanel>
  );
}

/** Guide / chat / architecture need a finished analysis; otherwise point back to the progress view. */
export function ProjectReadyGate({ children }: { children: (project: ProjectOut) => ReactNode }) {
  return (
    <ProjectLoaded>
      {(project) => {
        if (project.job.status === "succeeded") return children(project);
        const failed = project.job.status === "failed";
        return (
          <CenteredPanel>
            <div className="rounded-m border border-line bg-panel px-6 py-6">
              <p className="m-0 font-mono text-[12px] text-blueprint">{failed ? "analysis failed" : "still unfolding"}</p>
              <h1 className="m-0 mt-2 font-serif text-[26px] font-medium leading-tight">
                {failed ? project.job.stage_label : "Your guide isn't ready yet"}
              </h1>
              <p className="m-0 mt-2 text-[14.5px] leading-relaxed text-ink-soft">
                {failed
                  ? messageForCode(project.job.error_code, project.job.error_message)
                  : `Current stage: ${project.job.stage_label}. This page becomes available when the analysis finishes.`}
              </p>
              <Link
                href={`/projects/${encodeURIComponent(project.id)}`}
                className="mt-5 inline-flex items-center gap-2 rounded-s bg-blueprint px-4 py-2.5 text-[13.5px] font-semibold text-paper hover:bg-blueprint-deep"
              >
                {failed ? "See what happened" : "Watch progress"}
              </Link>
            </div>
          </CenteredPanel>
        );
      }}
    </ProjectLoaded>
  );
}
