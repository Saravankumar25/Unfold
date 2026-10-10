import { isIP } from "node:net";

/**
 * The one address the API may rate-limit on. On Vercel the platform header wins. Elsewhere the reverse proxy in
 * front of us (Caddy, nginx) appends the real peer to X-Forwarded-For, so the *last* entry is the trusted one;
 * anything the client put before it is dropped. The API honours it only from `TRUSTED_PROXY_IPS`.
 * Only syntactically valid IPs are returned, so a header can never mint arbitrary rate-limit keys.
 */
export function clientAddress(request: Request): string | null {
  // Vercel strips client-supplied copies of these headers; anywhere else a client could set them freely.
  if (process.env.VERCEL) {
    const platform = request.headers.get("x-vercel-forwarded-for") ?? request.headers.get("x-real-ip");
    const first = platform?.split(",")[0]?.trim();
    if (first && isIP(first)) return first;
  }
  const last = request.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim();
  return last && isIP(last) ? last : null;
}
