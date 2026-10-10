import { isIP } from "node:net";

/**
 * The one address the API may rate-limit on. On Vercel the platform header wins. Elsewhere nothing guarantees a
 * proxy appended the real peer to X-Forwarded-For (a client-forged header would otherwise reach the API as the
 * trusted IP), so the header is ignored unless the operator sets `TRUST_FORWARDED_FOR=1`, which is only valid
 * behind a reverse proxy (Caddy, nginx) that *appends* the peer; the last entry is then the trusted one and
 * anything the client put before it is dropped. By default this returns null and the API rate-limits on the
 * Next server's own peer address, which is safe (coarser, never spoofable). The API honours the result only
 * from `TRUSTED_PROXY_IPS`. Only syntactically valid IPs are returned, so a header can never mint arbitrary keys.
 */
export function clientAddress(request: Request): string | null {
  // Vercel strips client-supplied copies of these headers; anywhere else a client could set them freely.
  if (process.env.VERCEL) {
    const platform = request.headers.get("x-vercel-forwarded-for") ?? request.headers.get("x-real-ip");
    const first = platform?.split(",")[0]?.trim();
    if (first && isIP(first)) return first;
  }
  if (process.env.TRUST_FORWARDED_FOR !== "1") return null;
  const last = request.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim();
  return last && isIP(last) ? last : null;
}
