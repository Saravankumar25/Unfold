/** Typed endpoint functions (LLD §20). All paths are relative to `/api/v1`. */
import { ApiError, apiClient, type ApiClient } from "./client";
import type {
  ArchitectureOut,
  ArtifactOut,
  ChatDetailOut,
  ChatSessionOut,
  CreateGithubProject,
  DiagramOut,
  EvidenceOut,
  ExportCreated,
  GuideOut,
  GuideSectionOut,
  ProgressPage,
  ProjectCreated,
  ProjectOut,
  SectionVariant,
  SectionVariantOut,
  SessionCurrentOut,
  SessionOut,
  TechnicalLevel,
  UploadTicket,
} from "./types";

const enc = encodeURIComponent;

export function createEndpoints(client: ApiClient) {
  return {
    // sessions
    createSession: (): Promise<SessionOut> => client.createSession(),
    currentSession: (signal?: AbortSignal) => client.get<SessionCurrentOut>("/sessions/current", { signal }),
    touchSession: () => client.post<SessionOut>("/sessions/current/touch"),

    // projects
    createGithubProject: (body: CreateGithubProject) => client.post<ProjectCreated>("/projects/github", body),
    createZipProject: async (input: { file: File; technical_level: TechnicalLevel; instruction: string | null }) => {
      // Preferred: browser -> S3 with a presigned POST (the API and the proxy never carry the file). A deployment
      // without it answers 404 and the multipart route below is used (local stack).
      let ticket: UploadTicket | null = null;
      try {
        ticket = await client.post<UploadTicket>("/projects/zip/upload-url", {
          filename: input.file.name,
          size: input.file.size,
          technical_level: input.technical_level,
          instruction: input.instruction,
        });
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error;
      }
      if (ticket) {
        const form = new FormData();
        for (const [name, value] of Object.entries(ticket.fields)) form.append(name, value);
        form.append("file", input.file, input.file.name); // the file must be the last field
        const uploaded = await fetch(ticket.url, { method: "POST", body: form });
        if (!uploaded.ok) {
          throw new ApiError({
            code: "UPLOAD_FAILED",
            message:
              uploaded.status === 400 || uploaded.status === 403 || uploaded.status === 413
                ? "The upload was rejected (too large, not a ZIP, or the link expired)."
                : "The upload could not be completed. Please try again.",
            status: uploaded.status,
            retryable: uploaded.status >= 500,
          });
        }
        return client.post<ProjectCreated>(`/projects/${enc(ticket.project_id)}/zip/complete`);
      }
      const form = new FormData();
      form.append("file", input.file, input.file.name);
      form.append("technical_level", input.technical_level);
      if (input.instruction) form.append("instruction", input.instruction);
      return client.request<ProjectCreated>("POST", "/projects/zip", { body: form });
    },
    getProject: (projectId: string, signal?: AbortSignal) =>
      client.get<ProjectOut>(`/projects/${enc(projectId)}`, { signal }),
    deleteProject: (projectId: string) => client.delete<unknown>(`/projects/${enc(projectId)}`),

    getProgress: (projectId: string, after: number, signal?: AbortSignal) =>
      client.get<ProgressPage>(`/projects/${enc(projectId)}/progress?after=${after}`, { signal }),

    // guide
    getGuide: (projectId: string, signal?: AbortSignal) =>
      client.get<GuideOut>(`/projects/${enc(projectId)}/guide`, { signal }),
    getSection: (projectId: string, key: string, signal?: AbortSignal) =>
      client.get<GuideSectionOut>(`/projects/${enc(projectId)}/guide/sections/${enc(key)}`, { signal }),
    sectionVariant: (projectId: string, key: string, variant: SectionVariant) =>
      client.post<SectionVariantOut>(
        `/projects/${enc(projectId)}/guide/sections/${enc(key)}/${variant === "simpler" ? "simplify" : "deepen"}`,
      ),
    getEvidence: (projectId: string, chunkId: string, signal?: AbortSignal) =>
      client.get<EvidenceOut>(`/projects/${enc(projectId)}/evidence/${enc(chunkId)}`, { signal }),

    // architecture & diagrams
    getArchitecture: (projectId: string, signal?: AbortSignal) =>
      client.get<ArchitectureOut>(`/projects/${enc(projectId)}/architecture`, { signal }),
    getDiagram: (projectId: string, diagramId: string, signal?: AbortSignal) =>
      client.get<DiagramOut>(`/projects/${enc(projectId)}/diagrams/${enc(diagramId)}`, { signal }),

    // chat
    listChats: (projectId: string, signal?: AbortSignal) =>
      client.get<ChatSessionOut[]>(`/projects/${enc(projectId)}/chats`, { signal }),
    createChat: (projectId: string) => client.post<ChatSessionOut>(`/projects/${enc(projectId)}/chats`),
    getChat: (chatId: string, signal?: AbortSignal) => client.get<ChatDetailOut>(`/chats/${enc(chatId)}`, { signal }),

    // exports
    exportGuide: (projectId: string) => client.post<ExportCreated>(`/projects/${enc(projectId)}/exports/guide`),
    exportChat: (chatId: string) => client.post<ExportCreated>(`/chats/${enc(chatId)}/exports`),
    getExport: (artifactId: string, signal?: AbortSignal) =>
      client.get<ArtifactOut>(`/exports/${enc(artifactId)}`, { signal }),
  };
}

export const api = createEndpoints(apiClient);
export type Api = ReturnType<typeof createEndpoints>;

/** Path builders for the streaming endpoints (consumed by `sse.ts`); progress is polled, not streamed. */
export const streamPaths = {
  chatMessages: (chatId: string) => `/chats/${enc(chatId)}/messages`,
};
