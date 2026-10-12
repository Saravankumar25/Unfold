# Frontend performance and accessibility (phase 5 close-out)

## Method

- Production build (`npm run build`, Next 16.4 Turbopack), served with `next start` on port 3010, proxying `/api/*` to the real
  FastAPI app of the e2e stack (`frontend/e2e/api_server.py`, docker-compose stores, scripted model).
- A finished project was created by the Playwright `analysis` journey (`E2E_WEB_PORT=3010 npx playwright test --project=analysis -g "create a session"`);
  its session cookie was sent to Lighthouse with `--extra-headers`, so the project pages render real data.
- Lighthouse 13.5 CLI, Chrome 155 headless, default mobile preset (simulated Slow 4G, 4x CPU) and `--preset=desktop`.
- Before = `git archive HEAD frontend`, `npm ci`, built and served on 3012 against the same API. After = this worktree.
- Pages: landing (`/`), project (progress / done page), guide, architecture, chat. The new-project form is the landing page.
- The laptop was shared with other work, so scores are noisy. Landing mobile was run four times per side (the table shows the
  median); the other pages once per form.

## Results

Mobile (Performance / Accessibility / Best Practices / SEO, then LCP, CLS, TBT, JS transferred):

| Route | Before | After |
|---|---|---|
| Landing, median of 4 | 77 / 100 / 96-100 / 100, LCP 3.5 s, CLS 0 to 0.094, TBT 330-720 ms, JS 233 KB | **90 / 100 / 96-100 / 100**, LCP 3.1 s, CLS 0, TBT 130-430 ms, JS 166 KB |
| Landing, 4 runs | 70, 83, 88, 72 | 92, 89, 90, 81 |
| Project | 71 / 100 / 100 / 60, LCP 3.9 s, TBT 717 ms, JS 247 KB | 92 / 100 / 100 / 60, LCP 3.3 s, TBT 92 ms, JS 203 KB |
| Guide | 64 / 96 / 100 / 60, LCP 4.5 s, TBT 627 ms, JS 347 KB | 68 / 100 / 100 / 60, LCP 4.5 s, TBT 587 ms, JS 304 KB, CLS 0.068 |
| Architecture | 76 / 96 / 100 / 60, LCP 4.4 s, TBT 361 ms, JS 328 KB | 65 / 100 / 100 / 60, LCP 4.5 s, TBT 810 ms, JS 285 KB |
| Chat | 80 / 98 / 100 / 60, LCP 4.0 s, TBT 312 ms, JS 267 KB | 77 / 98 / 100 / 60, LCP 4.0 s, TBT 428 ms, JS 223 KB |

Desktop: landing 100 / 100 / 100 / 100 before and after (LCP 0.75 s to 0.65 s, JS 233 to 166 KB); project 99 to 100; guide 99 to 98;
architecture 98 to 97; chat 98 to 99; all CLS at or under 0.086, accessibility 100 (chat 98, see below).

Reading the table: the byte reductions are exact and repeatable (JS down 43 to 67 KB on every route). The Performance score is
not: the same build moved by 20 points between runs on TBT alone (the second row shows it). The guide, architecture and chat
mobile changes of a few points in either direction are inside that noise; do not read them as regressions or wins. The mobile
guide and architecture scores stay in the 60s: their cost is React hydration of a large client tree plus the React Flow chunk
(main thread 3.6 to 3.9 s under 4x throttle), which needs a structural change (server-render the guide) and is out of scope here.

Targets for the landing page on mobile (a11y >= 95, best practices >= 95, performance >= 85): met (100, 96 to 100, median 90; one of
four runs was 81, a noisy run with TBT 430 ms). Best practices is 96 rather than 100 when the browser has no session cookie: the
first `GET /api/v1/sessions/current` answers 401 by design (the API creates the session next) and Chrome logs it as a console
error. The cookie is HttpOnly, so the page cannot know in advance.

## What changed

| File | Change |
|---|---|
| `src/components/ui/toast.tsx`, `src/components/app/app-providers.tsx`, `package.json` | `motion` was only used for the toast enter/exit. Replaced by a CSS keyframe (`.toast-in` in `globals.css`; the existing `prefers-reduced-motion` rule covers it) and the dependency removed. About 60 KB less JS on every route. The toast no longer animates out. |
| `src/components/ui/tooltip.tsx` | The Radix `TooltipProvider` moved from the root layout into `Tooltip` itself, so the tooltip code loads only where a tooltip exists (the project pages). |
| `src/components/app/landing-page.tsx`, `landing-form.tsx`, `continue-session.tsx` | The landing page is now a server component. Only the form and the "Continue this session" list are client components; the hero, features and footer are no longer hydrated (TBT, JS). |
| `src/app/layout.tsx` | `IBM Plex Mono` uses `display: "optional"` with a monospace fallback: a late mono font was swapped in after first paint and moved the landing form (CLS 0.094 on mobile, now 0). |
| `src/components/diagram/diagram-canvas.tsx` | React Flow's attribution moved to `bottom-left`; it overlapped the zoom controls, which failed WCAG target-size (accessibility 96 to 100 on guide and architecture). |
| `src/components/app/chat-page.tsx` | The conversation pane is a `<main>` (Lighthouse `landmark-one-main` on chat, 98). Measured before this edit only: chat accessibility should now be 100, not re-run. |
| `next.config.ts`, `tests/security-headers.test.ts` | CSP `connect-src` is pinned to the upload bucket's regional host from `UPLOAD_BUCKET_HOST` (see below). |
| `e2e/api_server.py` | Import fix: `mark_failed` moved to `unfold.exports.status` in phase 3, so the e2e launcher no longer started. |

### CSP upload origin

`UPLOAD_BUCKET_HOST` (for example `unfold-uploads.s3.eu-west-1.amazonaws.com`) is read when `next.config.ts` is evaluated, so it
must be set for `next build` (baked into the routes manifest), not only at runtime. A value that is not a bare `*.amazonaws.com`
host name makes the build fail. Unset: the wildcard `https://*.amazonaws.com` in dev and test, and no S3 origin at all in a
production build (browser ZIP uploads to S3 are then blocked, so set it in the deploy build). Tests cover the pinned, fallback,
production-unset and invalid cases.

## Already fine, left alone

- Mermaid, Shiki, DOMPurify and the React Flow canvas are loaded with dynamic `import()` / `next/dynamic`; none is in the first-load chunks.
- Fonts are self-hosted through `next/font` (preloaded, latin subset, size-adjusted fallbacks for the two text fonts).
- Static assets under `/_next/static` carry `Cache-Control: public, max-age=31536000, immutable`; the prerendered landing page is
  `s-maxage=31536000` with revalidation by build id. No change needed in `next.config.ts` or `vercel.json`.

## Not fixed, by design

- SEO is 60 on every project page: `noindex, nofollow` is deliberate (projects are private and short-lived). The landing page is 100.
- `bf-cache` fails on project pages: Next sends `Cache-Control: no-store` for the dynamic, per-session HTML.
- "Legacy JavaScript" (14 KB) is Next's own polyfill block in the framework chunk, not application code.
- The 1.7 to 2.5 s "server response time" Lighthouse reported on project pages is not reproducible: `curl` measures 20 to 30 ms for the same URL.
- Guide CLS 0.068 and architecture desktop CLS 0.086 (content replacing the loading skeleton) are under the 0.1 threshold, so left.

## What could not be measured

- The 404 page: Lighthouse refuses non-2xx documents (`ERRORED_DOCUMENT_REQUEST`). It is a static server component with one heading, one link and no client JS beyond the shell.
- The error boundary (`error.tsx`) pages: they need a failing request; covered by `e2e/errors.spec.ts`, not audited by Lighthouse.
- Real-user field data (CrUX) and anything on real mobile hardware: lab runs only.
- Playwright journeys (`npm run e2e`) were not run in full; only the `analysis` journey was, to create the finished project.
