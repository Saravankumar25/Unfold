/** Typed endpoint functions (LLD §20). All paths are relative to `/api/v1`. */
import { apiClient, type ApiClient } from "./client";
import type {
  ArchitectureOut,
  ArtifactOut,
  ChatDetailOut,
  ChatReplyOut,
  ChatSessionOut,
  CreateGithubProject,
  DiagramOut,
  EvidenceOut,
  ExportCreated,
  ExportFormat,
  GuideOut,
  GuideSectionOut,
  PostChatMessage,
  ProgressPage,
  ProjectCreated,
  ProjectOut,
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

    getProgress: (projectId: string, after: number, signal?: AbortSignal) =>
      client.get<ProgressPage>(`/projects/${enc(projectId)}/progress?after=${after}`, { signal }),

    // guide
    getGuide: (projectId: string, signal?: AbortSignal) =>
      client.get<GuideOut>(`/projects/${enc(projectId)}/guide`, { signal }),
    getSection: (projectId: string, key: string, signal?: AbortSignal) =>
      client.get<GuideSectionOut>(`/projects/${enc(projectId)}/guide/sections/${enc(key)}`, { signal }),
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
    postMessage: (chatId: string, body: PostChatMessage, signal?: AbortSignal) =>
      client.post<ChatReplyOut>(`/chats/${enc(chatId)}/messages`, body, { signal }),

    // exports
    exportGuide: (projectId: string, format: ExportFormat = "pdf") =>
      client.post<ExportCreated>(`/projects/${enc(projectId)}/exports/guide?format=${format}`),
    exportChat: (chatId: string, format: ExportFormat = "pdf") =>
      client.post<ExportCreated>(`/chats/${enc(chatId)}/exports?format=${format}`),
    getExport: (artifactId: string, signal?: AbortSignal) =>
      client.get<ArtifactOut>(`/exports/${enc(artifactId)}`, { signal }),
  };
}

export const api = createEndpoints(apiClient);
export type Api = ReturnType<typeof createEndpoints>;
