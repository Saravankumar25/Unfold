/**
 * Server-Sent Events over `fetch` + ReadableStream.
 *
 * `EventSource` cannot POST (chat) nor survive our session-bootstrap flow, so we parse the
 * `text/event-stream` format ourselves (WHATWG HTML §9.2.6):
 * - lines end with CRLF, LF or CR (a CR at a chunk boundary is handled);
 * - `data:` lines accumulate joined by "\n"; `event:` sets the type; `id:` sets the last event id;
 *   `retry:` sets the reconnection delay; lines starting with ":" are comments;
 * - a blank line dispatches the event (no dispatch when there was no data).
 */
import { ApiError, apiClient, type ApiClient } from "./client";

export interface SSEMessage {
  event: string;
  data: string;
  id: string;
}

export class SSEParser {
  private buffer = "";
  private dataLines: string[] = [];
  private eventType = "";
  private sawData = false;
  private pendingCR = false;
  /** Last event id seen (persists across dispatches, as the spec requires). */
  lastEventId = "";
  /** Server-requested reconnection delay in ms, if any. */
  retry: number | null = null;

  /** Feed a decoded text chunk; returns the events completed by it. */
  feed(chunk: string): SSEMessage[] {
    const out: SSEMessage[] = [];
    let text = chunk;
    if (this.pendingCR) {
      // previous chunk ended with "\r": swallow a directly following "\n" (CRLF split across chunks)
      if (text.startsWith("\n")) text = text.slice(1);
      this.pendingCR = false;
    }
    this.buffer += text;

    let start = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i];
      if (ch !== "\n" && ch !== "\r") continue;
      const line = this.buffer.slice(start, i);
      if (ch === "\r") {
        if (i + 1 < this.buffer.length) {
          if (this.buffer[i + 1] === "\n") i++;
        } else {
          this.pendingCR = true;
        }
      }
      start = i + 1;
      const msg = this.processLine(line);
      if (msg) out.push(msg);
    }
    this.buffer = this.buffer.slice(start);
    return out;
  }

  /** Flush at end of stream. Per spec, an incomplete trailing event is discarded. */
  end(): SSEMessage[] {
    this.buffer = "";
    this.resetEvent();
    return [];
  }

  private resetEvent() {
    this.dataLines = [];
    this.eventType = "";
    this.sawData = false;
  }

  private processLine(line: string): SSEMessage | null {
    if (line === "") {
      if (!this.sawData) {
        this.resetEvent();
        return null;
      }
      const msg: SSEMessage = {
        event: this.eventType || "message",
        data: this.dataLines.join("\n"),
        id: this.lastEventId,
      };
      this.resetEvent();
      return msg;
    }
    if (line.startsWith(":")) return null;

    const colon = line.indexOf(":");
    let field: string;
    let value: string;
    if (colon === -1) {
      field = line;
      value = "";
    } else {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
    }

    switch (field) {
      case "data":
        this.dataLines.push(value);
        this.sawData = true;
        break;
      case "event":
        this.eventType = value;
        break;
      case "id":
        if (!value.includes("\u0000")) this.lastEventId = value;
        break;
      case "retry":
        if (/^\d+$/.test(value)) this.retry = Number(value);
        break;
      default:
        break;
    }
    return null;
  }
}

/** Read an SSE response body to completion, invoking `onMessage` per event. Returns the last event id. */
export async function readEventStream(
  response: Response,
  onMessage: (msg: SSEMessage) => void | "stop",
  parser: SSEParser = new SSEParser(),
): Promise<{ lastEventId: string; stopped: boolean; retry: number | null }> {
  if (!response.body) return { lastEventId: parser.lastEventId, stopped: false, retry: parser.retry };
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const msg of parser.feed(decoder.decode(value, { stream: true }))) {
        if (onMessage(msg) === "stop") {
          await reader.cancel().catch(() => undefined);
          return { lastEventId: parser.lastEventId, stopped: true, retry: parser.retry };
        }
      }
    }
    const tail = decoder.decode();
    for (const msg of parser.feed(tail)) {
      if (onMessage(msg) === "stop") return { lastEventId: parser.lastEventId, stopped: true, retry: parser.retry };
    }
    parser.end();
  } finally {
    reader.releaseLock();
  }
  return { lastEventId: parser.lastEventId, stopped: false, retry: parser.retry };
}

export function parseJSON<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

/** One-shot stream (e.g. chat `POST /chats/{id}/messages`). Rejects with `ApiError` on HTTP errors. */
export async function postEventStream(
  path: string,
  json: unknown,
  onMessage: (msg: SSEMessage) => void | "stop",
  options: { signal?: AbortSignal; client?: ApiClient } = {},
): Promise<void> {
  const client = options.client ?? apiClient;
  const response = await client.send("POST", path, {
    json,
    signal: options.signal,
    headers: { Accept: "text/event-stream" },
  });
  await readEventStream(response, onMessage);
}

export interface SubscribeOptions {
  path: string;
  onMessage: (msg: SSEMessage) => void | "stop";
  /** Called when a connection is (re)opened. */
  onOpen?: (attempt: number) => void;
  /** Called on a (possibly transient) failure; `willRetry` tells whether we reconnect. */
  onError?: (error: ApiError | Error, willRetry: boolean) => void;
  client?: ApiClient;
  initialLastEventId?: string;
  /** Backoff settings (ms). */
  baseDelay?: number;
  maxDelay?: number;
  /** Give up after this many consecutive failed connection attempts. */
  maxAttempts?: number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

export interface Subscription {
  close(): void;
  readonly done: Promise<void>;
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        resolve();
      },
      { once: true },
    );
  });
}

export function backoffDelay(attempt: number, base: number, max: number): number {
  const exp = Math.min(max, base * 2 ** Math.max(0, attempt - 1));
  // ±20 % jitter, deterministic enough for tests via bounds
  return Math.round(exp * (0.8 + Math.random() * 0.4));
}

/**
 * Long-lived GET subscription with `Last-Event-ID` resume and automatic reconnect (exponential backoff).
 * Stops when `onMessage` returns "stop", when `close()` is called, on non-retryable HTTP errors
 * (e.g. 404), or after `maxAttempts` consecutive failures.
 */
export function subscribeEventStream(options: SubscribeOptions): Subscription {
  const client = options.client ?? apiClient;
  const controller = new AbortController();
  const baseDelay = options.baseDelay ?? 1000;
  const maxDelay = options.maxDelay ?? 15000;
  const maxAttempts = options.maxAttempts ?? 8;
  const sleep = options.sleep ?? defaultSleep;
  let lastEventId = options.initialLastEventId ?? "";

  const run = async () => {
    let failures = 0;
    let serverRetry: number | null = null;
    while (!controller.signal.aborted) {
      try {
        const headers: Record<string, string> = { Accept: "text/event-stream" };
        if (lastEventId) headers["Last-Event-ID"] = lastEventId;
        const response = await client.send("GET", options.path, { headers, signal: controller.signal });
        failures = 0;
        options.onOpen?.(0);
        const parser = new SSEParser();
        parser.lastEventId = lastEventId;
        const result = await readEventStream(
          response,
          (msg) => {
            lastEventId = msg.id || lastEventId;
            return options.onMessage(msg);
          },
          parser,
        );
        lastEventId = result.lastEventId || lastEventId;
        serverRetry = result.retry ?? serverRetry;
        if (result.stopped || controller.signal.aborted) return;
        // Server closed the stream without a terminal event → reconnect after a short pause.
        failures += 1;
      } catch (err) {
        if (controller.signal.aborted) return;
        const error = err instanceof Error ? err : new Error(String(err));
        const retryable = !(error instanceof ApiError) || error.retryable || error.status === 0;
        failures += 1;
        const willRetry = retryable && failures < maxAttempts;
        options.onError?.(error, willRetry);
        if (!willRetry) return;
      }
      if (failures >= maxAttempts) {
        options.onError?.(new Error("The live progress connection was lost."), false);
        return;
      }
      await sleep(serverRetry ?? backoffDelay(failures, baseDelay, maxDelay), controller.signal);
    }
  };

  const done = run();
  return {
    close: () => controller.abort(),
    done,
  };
}
