/** Typed endpoint functions (LLD §20). All paths are relative to `/api/v1`. */
import { apiClient, type ApiClient } from "./client";
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
  ProjectCreated,
  ProjectOut,
  SectionVariant,
  SectionVariantOut,
  SessionCurrentOut,
  SessionOut,
  TechnicalLevel,
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
    createZipProject: (input: { file: File; technical_level: TechnicalLevel; instruction: string | null }) => {
      const form = new FormData();
      form.append("file", input.file, input.file.name);
      form.append("technical_level", input.technical_level);
      if (input.instruction) form.append("instruction", input.instruction);
      return client.request<ProjectCreated>("POST", "/projects/zip", { body: form });
    },
    getProject: (projectId: string, signal?: AbortSignal) =>
      client.get<ProjectOut>(`/projects/${enc(projectId)}`, { signal }),
    deleteProject: (projectId: string) => client.delete<unknown>(`/projects/${enc(projectId)}`),

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

/** Path builders for the streaming endpoints (consumed by `sse.ts`). */
export const streamPaths = {
  projectEvents: (projectId: string) => `/projects/${enc(projectId)}/events`,
  chatMessages: (chatId: string) => `/chats/${enc(chatId)}/messages`,
};
