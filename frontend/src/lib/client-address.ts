/**
 * The one address the API may rate-limit on. The platform in front of us (Vercel, Caddy) appends the real peer
 * to X-Forwarded-For, so the *last* entry is the trusted one; anything the client put before it is dropped.
 */
export function clientAddress(request: Request): string | null {
  const platform = request.headers.get("x-vercel-forwarded-for") ?? request.headers.get("x-real-ip");
  if (platform) return platform.split(",")[0]!.trim() || null;
  const chain = request.headers.get("x-forwarded-for");
  if (!chain) return null;
  const last = chain.split(",").at(-1)?.trim();
  return last || null;
}
