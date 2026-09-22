import type { NextConfig } from "next";

/**
 * The browser only ever talks to the web origin: every `/api/*` request goes to the catch-all Route Handler
 * `src/app/api/[...path]/route.ts`, which proxies it to the FastAPI service so the `unfold_session` cookie
 * (HttpOnly, set by the API) is first-party (HLD §2, D9).
 *
 * Why a Route Handler and not `rewrites()`:
 * - measured: the rewrite proxy gzip-buffers `text/event-stream` (no SSE bytes reached the client for 24 s,
 *   while the API emitted an event at +0.2 s and a heartbeat at +10 s);
 * - rewrites are resolved at `next build`, so `output: "standalone"` would bake the API origin into the image;
 *   the handler reads `API_INTERNAL_URL` at runtime (docker: `http://api:8000`);
 * - an `afterFiles` rewrite would shadow the dynamic `[...path]` handler, so there is deliberately no rewrite.
 */
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
        ],
      },
    ];
  },
};

export default nextConfig;
