/**
 * Same-origin streaming proxy: `/api/*` → `${API_INTERNAL_URL}/api/*` (see next.config.ts for why this is a
 * Route Handler rather than a rewrite).
 *
 * - Request bodies are streamed (`duplex: "half"`) so 50 MiB ZIP uploads are never buffered in memory.
 * - Response bodies are passed through as a stream; SSE responses get `Cache-Control: no-transform` so no
 *   compression layer buffers them, plus `X-Accel-Buffering: no` for any reverse proxy in front of us.
 * - Client disconnects abort the upstream request, so the API sees the SSE consumer go away.
 * - Hop-by-hop headers are dropped; `Set-Cookie` headers are forwarded individually (first-party cookie).
 */
import type { NextRequest } from "next/server";
import { clientAddress } from "@/lib/client-address";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
  "accept-encoding",
  "content-encoding",
  "set-cookie",
]);

function upstreamOrigin(): string {
  return (process.env.API_INTERNAL_URL || "http://localhost:8000").replace(/\/+$/, "");
}

function envelope(status: number, code: string, message: string): Response {
  return new Response(JSON.stringify({ error: { code, message, retryable: true, request_id: null } }), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

async function proxy(request: NextRequest): Promise<Response> {
  const incoming = new URL(request.url);
  if (!incoming.pathname.startsWith("/api/")) return envelope(404, "NOT_FOUND", "Not found.");
  const target = `${upstreamOrigin()}${incoming.pathname}${incoming.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  headers.set("accept-encoding", "identity");
  headers.set("x-forwarded-host", incoming.host);
  headers.set("x-forwarded-proto", incoming.protocol.replace(":", ""));
  headers.delete("x-forwarded-for");
  headers.delete("x-real-ip");
  const clientIp = clientAddress(request);
  if (clientIp) headers.set("x-forwarded-for", clientIp);

  const hasBody = request.method !== "GET" && request.method !== "HEAD" && request.body !== null;
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    redirect: "manual",
    cache: "no-store",
    signal: request.signal,
  };
  if (hasBody) init.duplex = "half";

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch (error) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    const detail = error instanceof Error ? error.message : String(error);
    console.error(`[api-proxy] ${request.method} ${incoming.pathname} → ${target} failed: ${detail}`);
    return envelope(502, "UPSTREAM_UNAVAILABLE", "The Unfold API is unreachable right now. Please try again shortly.");
  }

  const out = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) out.set(key, value);
  });
  for (const cookie of upstream.headers.getSetCookie()) out.append("set-cookie", cookie);

  const contentType = upstream.headers.get("content-type") ?? "";
  if (contentType.startsWith("text/event-stream")) {
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
  }

  return new Response(request.method === "HEAD" ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: out,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
