/**
 * HTTP DTOs — mirrors `backend/src/unfold/api/schemas.py` plus the models it references
 * (`guide/models.py`, `diagrams/models.py`, `domain/models.py`, `domain/enums.py`) EXACTLY.
 * Field names are snake_case as they appear in JSON. `datetime` → ISO-8601 string.
 * Change this file in the same change as the Python schemas.
 */

// ---- enums (domain/enums.py) -------------------------------------------------------------------------------

export type TechnicalLevel = "beginner" | "intermediate" | "experienced";
export const TECHNICAL_LEVELS: readonly TechnicalLevel[] = ["beginner", "intermediate", "experienced"];

export type InputType = "github" | "zip";

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export type JobStage =
  | "RECEIVED"
  | "VALIDATING_INPUT"
  | "INGESTING"
  | "SECURITY_SCANNING"
  | "INVENTORY"
  | "STRUCTURAL_ANALYSIS"
  | "BUILDING_IR"
  | "BUILDING_GRAPH"
  | "INDEXING_LEXICAL"
  | "INDEXING_DENSE"
  | "SUMMARIZING_HIERARCHY"
  | "DISCOVERING_SUBSYSTEMS"
  | "INVESTIGATING_PROJECT"
  | "PLANNING_GUIDE"
  | "GENERATING_GUIDE"
  | "VERIFYING_GUIDE"
  | "GENERATING_VISUALS"
  | "FINALIZING"
  | "READY"
  | "FAILED_INPUT"
  | "FAILED_SECURITY"
  | "FAILED_INGESTION"
  | "FAILED_ANALYSIS"
  | "FAILED_PROVIDER_CAPACITY"
  | "FAILED_GENERATION"
  | "FAILED_EXPORT";

/** `PIPELINE` order from domain/enums.py (used only for ordering; labels always come from the server). */
export const PIPELINE: readonly JobStage[] = [
  "RECEIVED",
  "VALIDATING_INPUT",
  "INGESTING",
  "SECURITY_SCANNING",
  "INVENTORY",
  "STRUCTURAL_ANALYSIS",
  "BUILDING_IR",
  "BUILDING_GRAPH",
  "INDEXING_LEXICAL",
  "INDEXING_DENSE",
  "SUMMARIZING_HIERARCHY",
  "DISCOVERING_SUBSYSTEMS",
  "INVESTIGATING_PROJECT",
  "PLANNING_GUIDE",
  "GENERATING_GUIDE",
  "VERIFYING_GUIDE",
  "GENERATING_VISUALS",
  "FINALIZING",
  "READY",
];

export type SectionStatus = "pending" | "generating" | "ready" | "degraded" | "failed";

export type ClaimStatus = "VERIFIED" | "STRONGLY_INFERRED" | "INFERRED" | "UNSUPPORTED" | "UNKNOWN" | "PROPOSED";

export type ClaimKind = "project_fact" | "inference" | "improvement";

export type ChatMode = "default" | "simpler" | "deeper" | "quiz" | "interview";
export const CHAT_MODES: readonly ChatMode[] = ["default", "simpler", "deeper", "quiz", "interview"];

export type ArtifactKind = "guide_pdf" | "chat_pdf";

export type ArtifactStatus = "pending" | "ready" | "failed";

// ---- domain/models.py --------------------------------------------------------------------------------------

export interface ClaimRecord {
  text: string;
  kind: ClaimKind;
  status: ClaimStatus;
  evidence_ids: string[];
  graph_node_ids: string[];
  confidence: number;
  note: string | null;
}

export interface EvidenceRef {
  chunk_id: string;
  path: string;
  start_line: number;
  end_line: number;
  symbol: string | null;
  score: number | null;
}

// ---- guide/models.py (content blocks) ----------------------------------------------------------------------

export interface HeadingBlock {
  type: "heading";
  text: string;
  level: 2 | 3;
}

export interface ParagraphBlock {
  type: "paragraph";
  text: string;
}

export interface BulletsBlock {
  type: "bullets";
  items: string[];
  ordered: boolean;
}

export type CalloutTone = "note" | "tip" | "warning" | "insight";

export interface CalloutBlock {
  type: "callout";
  tone: CalloutTone;
  text: string;
}

export interface CodeBlock {
  type: "code";
  language: string;
  code: string;
  path: string | null;
  start_line: number | null;
  caption: string | null;
}

export interface DiagramBlock {
  type: "diagram";
  diagram_id: string;
  caption: string | null;
}

export interface TableBlock {
  type: "table";
  headers: string[];
  rows: string[][];
}

export type QAKind = "repository" | "design" | "failure" | "security" | "extension";

export interface QABlock {
  type: "qa";
  question: string;
  answer: string;
  kind: QAKind;
}

export interface GlossaryItem {
  term: string;
  definition: string;
}

export interface GlossaryBlock {
  type: "glossary";
  items: GlossaryItem[];
}

export type Block =
  | HeadingBlock
  | ParagraphBlock
  | BulletsBlock
  | CalloutBlock
  | CodeBlock
  | DiagramBlock
  | TableBlock
  | QABlock
  | GlossaryBlock;

export type BlockType = Block["type"];

export interface SectionContent {
  summary: string;
  blocks: Block[];
}

// ---- diagrams/models.py ------------------------------------------------------------------------------------

export type DiagramKind =
  | "architecture"
  | "module_dependencies"
  | "data_model"
  | "request_flow"
  | "deployment"
  | "ai_pipeline"
  | "workflow_sequence";

export interface DiagramNode {
  id: string;
  label: string;
  /** service | module | datastore | endpoint | external | queue | actor | file | component | ... */
  kind: string;
  group: string | null;
  ref_uid: string | null;
  detail: string | null;
  inferred: boolean;
}

export interface DiagramEdge {
  source: string;
  target: string;
  label: string | null;
  kind: string | null;
  inferred: boolean;
}

export interface DiagramGroup {
  id: string;
  label: string;
}

export interface DiagramSpec {
  id: string;
  kind: DiagramKind;
  title: string;
  description: string;
  nodes: DiagramNode[];
  edges: DiagramEdge[];
  groups: DiagramGroup[];
  annotations: string[];
  section_key: string | null;
}

// ---- api/schemas.py ----------------------------------------------------------------------------------------

export interface ErrorBody {
  code: string;
  message: string;
  retryable: boolean;
  request_id: string | null;
}

export interface ErrorEnvelope {
  error: ErrorBody;
}

// sessions

export interface SessionOut {
  session_id: string;
  created_at: string;
  expires_at: string;
}

export interface ProjectSummary {
  id: string;
  name: string | null;
  input_type: string;
  source_locator: string | null;
  status: string;
  current_stage: string;
  created_at: string;
}

export interface SessionCurrentOut extends SessionOut {
  projects: ProjectSummary[];
}

// projects & jobs

export interface CreateGithubProject {
  url: string;
  technical_level: TechnicalLevel;
  instruction: string | null;
}

export interface ProjectCreated {
  project_id: string;
  job_id: string;
}

export interface JobOut {
  id: string;
  status: string;
  current_stage: string;
  stage_label: string;
  stage_index: number;
  stage_total: number;
  attempt: number;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
  degraded: string[];
  started_at: string | null;
  completed_at: string | null;
}

export interface ProjectOut {
  id: string;
  name: string | null;
  input_type: string;
  source_locator: string | null;
  commit_sha: string | null;
  default_branch: string | null;
  technical_level: string;
  instruction: string | null;
  project_type: Record<string, unknown> | null;
  stats: Record<string, unknown> | null;
  created_at: string;
  expires_at: string;
  job: JobOut;
}

export type ProgressEventType = "stage" | "ready" | "failed" | "heartbeat";

export interface ProgressEvent {
  type: ProgressEventType;
  stage: string;
  label: string;
  index: number;
  total: number;
  detail: string | null;
  error_code: string | null;
  message: string | null;
  retryable: boolean | null;
  at: string;
}

export interface ProgressItem extends ProgressEvent {
  seq: number;
}

export interface ProgressPage {
  events: ProgressItem[];
  cursor: number;
  job: JobOut;
}

// guide

export interface GuideSectionSummary {
  key: string;
  title: string;
  position: number;
  status: string;
  kind: string;
  target_minutes: number;
  summary: string | null;
}

export interface GuideOut {
  project_id: string;
  estimated_read_minutes: number;
  sections: GuideSectionSummary[];
}

export interface GuideSectionOut {
  key: string;
  title: string;
  position: number;
  status: string;
  kind: string;
  target_minutes: number;
  content: SectionContent | null;
  claims: ClaimRecord[];
  /** "E3" -> reference (labels used inside the content text) */
  evidence: Record<string, EvidenceRef>;
  quality: Record<string, unknown>;
}

export type SectionVariant = "simpler" | "deeper";

export interface SectionVariantOut {
  key: string;
  variant: SectionVariant;
  content: SectionContent;
  evidence: Record<string, EvidenceRef>;
}

export interface EvidenceOut {
  chunk_id: string;
  path: string;
  start_line: number;
  end_line: number;
  symbol: string | null;
  language: string | null;
  /** redacted */
  text: string;
  trust_label: string;
}

// architecture & diagrams

export interface DiagramOut extends DiagramSpec {
  mermaid: string;
  origin: string;
}

export interface SubsystemOut {
  key: string;
  title: string;
  summary: string | null;
  members: string[];
}

export interface ArchitectureOut {
  subsystems: SubsystemOut[];
  /** files, symbols, endpoints, schemas, services, datastores, nodes, relationships */
  counts: Record<string, number>;
  languages: Record<string, unknown>[];
  frameworks: string[];
  diagram: DiagramOut | null;
}

// chat

export interface ChatSessionOut {
  id: string;
  project_id: string;
  title: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageOut {
  id: string;
  seq: number;
  role: "user" | "assistant";
  mode: ChatMode;
  text: string;
  evidence: EvidenceRef[];
  created_at: string;
}

export interface ChatDetailOut {
  chat: ChatSessionOut;
  messages: ChatMessageOut[];
}

export interface PostChatMessage {
  text: string;
  mode: ChatMode;
  section_key: string | null;
}

/**
 * SSE `POST /chats/{id}/messages` — event names equal `type`:
 * chat.started {message_id} · retrieval.started · retrieval.completed {detail} · agent.started ·
 * agent.tool {tool} · agent.completed · response.delta {text} · response.evidence {evidence} ·
 * response.completed {message_id, seq} · error {code, message, retryable}
 */
export interface ChatEvent {
  type: string;
  text: string | null;
  tool: string | null;
  detail: string | null;
  evidence: EvidenceRef[] | null;
  message_id: string | null;
  seq: number | null;
  code: string | null;
  message: string | null;
  retryable: boolean | null;
}

export type ChatEventType =
  | "chat.started"
  | "retrieval.started"
  | "retrieval.completed"
  | "agent.started"
  | "agent.tool"
  | "agent.completed"
  | "response.delta"
  | "response.evidence"
  | "response.completed"
  | "error";

// exports

export interface ExportCreated {
  artifact_id: string;
}

export interface ArtifactOut {
  id: string;
  kind: string;
  status: string;
  error: string | null;
  size_bytes: number | null;
  created_at: string;
  expires_at: string;
  /** relative, e.g. /api/v1/exports/{id}/download when ready */
  download_url: string | null;
}

/** `POST /projects/zip/upload-url`: a presigned POST for the browser (send `fields`, then the file, to `url`). */
export interface UploadTicket {
  project_id: string;
  url: string;
  fields: Record<string, string>;
  max_bytes: number;
  expires_at: string;
}
