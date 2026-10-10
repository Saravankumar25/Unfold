/**
 * Typed fetch wrapper over the same-origin `/api/v1` proxy.
 *
 * - Always `credentials: "include"` (the HttpOnly `unfold_session` cookie is set by the API).
 * - Error envelope `{error:{code,message,retryable,request_id}}` → `ApiError`.
 * - On 401 `SESSION_REQUIRED` / `SESSION_EXPIRED` it creates a session (`POST /sessions`, single-flight)
 *   and retries the original request exactly once.
 */
import type { ErrorEnvelope, SessionOut } from "./types";

export const API_PREFIX = "/api/v1";

export const SESSION_ERROR_CODES = new Set(["SESSION_REQUIRED", "SESSION_EXPIRED"]);

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  readonly requestId: string | null;

  constructor(init: { code: string; message: string; status: number; retryable: boolean; requestId?: string | null }) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status;
    this.retryable = init.retryable;
    this.requestId = init.requestId ?? null;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const err = (value as { error: unknown }).error;
  return (
    typeof err === "object" &&
    err !== null &&
    typeof (err as { code?: unknown }).code === "string" &&
    typeof (err as { message?: unknown }).message === "string"
  );
}

/** Build an `ApiError` from a non-OK response (reads the body). */
export async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  const text = await response.text().catch(() => "");
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (isErrorEnvelope(body)) {
    return new ApiError({
      code: body.error.code,
      message: body.error.message,
      retryable: Boolean(body.error.retryable),
      requestId: body.error.request_id ?? response.headers.get("x-request-id"),
      status: response.status,
    });
  }
  return new ApiError({
    code: response.status >= 500 ? "UPSTREAM_ERROR" : `HTTP_${response.status}`,
    message:
      response.status === 502 || response.status === 503 || response.status === 504
        ? "The Unfold service is unreachable right now. Please try again shortly."
        : `Request failed (${response.status}).`,
    retryable: response.status >= 500 || response.status === 429,
    requestId: response.headers.get("x-request-id"),
    status: response.status,
  });
}

export function networkError(cause: unknown): ApiError {
  const message = cause instanceof Error ? cause.message : String(cause);
  return new ApiError({
    code: "NETWORK_ERROR",
    message: `Could not reach Unfold (${message}). Check your connection and try again.`,
    retryable: true,
    status: 0,
  });
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface RequestOptions {
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Set false for the session bootstrap call itself. */
  sessionRetry?: boolean;
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetch?: FetchLike;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;
  private sessionPromise: Promise<SessionOut> | null = null;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? API_PREFIX;
    this.fetchImpl = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
  }

  url(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  /** Single-flight session creation shared by concurrent 401s. */
  createSession(): Promise<SessionOut> {
    if (!this.sessionPromise) {
      this.sessionPromise = this.request<SessionOut>("POST", "/sessions", { sessionRetry: false }).finally(() => {
        this.sessionPromise = null;
      });
    }
    return this.sessionPromise;
  }

  private buildInit(method: string, options: RequestOptions): RequestInit {
    const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
    let body: BodyInit | undefined = options.body;
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.json);
    }
    return { method, headers, body, credentials: "include", cache: "no-store", signal: options.signal };
  }

  /**
   * Perform a request and return the raw OK `Response` (`request` parses it).
   * Non-OK responses are converted to `ApiError`, with one session-bootstrap retry.
   */
  async send(method: string, path: string, options: RequestOptions = {}): Promise<Response> {
    const attempt = async (): Promise<Response> => {
      try {
        return await this.fetchImpl(this.url(path), this.buildInit(method, options));
      } catch (cause) {
        if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
        throw networkError(cause);
      }
    };

    let response = await attempt();
    if (response.ok) return response;
    let error = await toApiError(response);

    if (options.sessionRetry !== false && response.status === 401 && SESSION_ERROR_CODES.has(error.code)) {
      await this.createSession();
      response = await attempt();
      if (response.ok) return response;
      error = await toApiError(response);
    }
    throw error;
  }

  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const response = await this.send(method, path, options);
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new ApiError({
        code: "INVALID_RESPONSE",
        message: "The server returned an unexpected response.",
        retryable: true,
        status: response.status,
      });
    }
  }

  get<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>("GET", path, options);
  }

  post<T>(path: string, json?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>("POST", path, { ...options, json: json ?? undefined });
  }

  delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>("DELETE", path, options);
  }
}

export const apiClient = new ApiClient();
