import type { NextConfig } from "next";

/**
 * The browser only ever talks to the web origin: every `/api/*` request goes to the catch-all Route Handler
 * `src/app/api/[...path]/route.ts`, which proxies it to the FastAPI service so the `unfold_session` cookie
 * (HttpOnly, set by the API) is first-party (HLD §2, D9).
 *
 * Why a Route Handler and not `rewrites()`:
 * - the rewrite proxy buffers streamed bodies (measured with SSE before the SSE routes were removed);
 * - rewrites are resolved at `next build`, so `output: "standalone"` would bake the API origin into the image;
 *   the handler reads `API_INTERNAL_URL` at runtime (docker: `http://api:8000`);
 * - an `afterFiles` rewrite would shadow the dynamic `[...path]` handler, so there is deliberately no rewrite.
 */
const isDev = process.env.NODE_ENV !== "production";

/**
 * Upload target of the browser's presigned ZIP POST: the bucket's regional host, e.g. `unfold-up.s3.eu-west-1.amazonaws.com`.
 * Read when the config is evaluated (`next build`), so set it for the build. Unset: the wildcard in dev and test only;
 * a production build then allows no S3 origin (S3 uploads are blocked until it is set).
 */
const uploadHost = (process.env.UPLOAD_BUCKET_HOST ?? "").trim();
if (uploadHost && !/^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.amazonaws\.com(\.cn)?$/i.test(uploadHost)) {
  throw new Error("UPLOAD_BUCKET_HOST must be a bare *.amazonaws.com host name (no scheme, path or spaces)");
}
const uploadOrigin = uploadHost ? `https://${uploadHost}` : isDev ? "https://*.amazonaws.com" : "";

/**
 * Content-Security-Policy (audit S-6). Next injects inline bootstrap scripts, so `script-src` keeps
 * `'unsafe-inline'`; a per-request nonce needs a request proxy and fully dynamic rendering (tracked as a follow-up).
 * Everything else is closed: generated markup (Shiki, Mermaid) is DOMPurify-sanitised before it reaches the DOM,
 * and the page can only talk to its own origin.
 */
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  // the browser POSTs a ZIP straight to the S3 bucket with a presigned policy
  `connect-src 'self'${uploadOrigin ? ` ${uploadOrigin}` : ""}${isDev ? " ws: wss:" : ""}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  agentRules: false,
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Content-Security-Policy", value: csp },
          { key: "Strict-Transport-Security", value: "max-age=31536000" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
