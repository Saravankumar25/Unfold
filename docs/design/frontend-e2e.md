# Frontend end-to-end journeys and UI polish (phase 5, tasks 1 and 6)

## What the journeys run against

Real everything except the model and the queue launcher.

| Piece | What it is |
|---|---|
| Stores | `docker compose up -d` (Postgres, Redis, Neo4j, Qdrant, MinIO), isolated like the integration tests: database `<db>_test`, Redis DB 1, bucket `unfold-test`, Qdrant prefix `unfold_test`. |
| API | The real FastAPI app, started by `frontend/e2e/api_server.py` through `create_app(settings, queue=..., llm=...)`, the documented injection point. |
| Model | `unfold.evaluation.scripted.ScriptedLLM` (test tooling; production code never imports it). No quota is spent. |
| Queue | `InProcessQueue` in the same launcher: runs `run_analysis`, `run_export` and `cleanup_project` (what `python -m unfold.worker.run` runs) one job per thread, so parsing and embedding do not stall the API loop. |
| Web | `next start` of the production build, proxying `/api/*` to the API (`API_INTERNAL_URL`). |
| Browser | Playwright Chromium, headless. |

There was no worker-side switch for a scripted model (the architecture test forbids production imports of
`unfold.evaluation`), so the launcher injects the model and queue from outside. GitHub projects are created through the
real API but their job stays queued: ingestion would need the network.

`frontend/e2e/tool.py` reaches the two states the UI cannot: `pin` (and `unpin`) makes the finished ZIP project look like a
GitHub analysis at a commit (so citations link to `blob/<sha>/<path>#Lx-Ly`), and `expire` moves a session's expiry into the past.

## Running

```
docker compose up -d                       # repo root
cd backend && uv sync                      # once
cd frontend && npm ci && npx playwright install chromium    # once
npm run e2e                                # next build, then every journey (about 15 min on a busy laptop)
npx playwright test --project=errors       # one project; skips the build
npx playwright test --project=journeys --no-deps responsive   # reuse the finished project of an earlier run
```

Ports: web 3011 (`E2E_WEB_PORT`), API 8010 (`E2E_API_PORT`); the dev servers (3010, 8000) are left alone. The `analysis`
project must run first: it creates the finished project (`e2e/.state/`, git-ignored) the `journeys` reuse.

The live-LLM smoke is on demand only; the config registers it only when `E2E_LIVE=1`, so default and CI runs never
spend quota or touch the network:

```
E2E_LIVE=1 npx playwright test --project=live      # PowerShell: $env:E2E_LIVE=1; npx playwright test --project=live
```

It runs the real gateway and worker subprocesses against `github.com/octocat/Spoon-Knife`, then the guide and one chat answer.

CI: job `e2e` in `.github/workflows/ci.yml` (stores, `uv sync`, both Playwright Chromium installs, `npm run e2e`,
report uploaded as an artifact).

## Journeys

| Project | Spec | Journey |
|---|---|---|
| analysis | `analysis.spec.ts` | Session cookie created (HttpOnly, Lax); ZIP of `evals/fixtures/repos/fastapi_rag` uploaded through the form; progress watched live; every announced stage (server labels, incl. "Researching each chapter") is listed; finished project opens. |
| analysis | `analysis.spec.ts` | GitHub URL submitted: project created, progress page shows "Queued". |
| journeys | `guide.spec.ts` | Compact `path:Lx-Ly` citations (never `[E1]`), citation opens the source excerpt; after `pin`, citations link to the analysed commit and the evidence appendix lists them; regenerate one chapter, others byte-identical. |
| journeys | `architecture.spec.ts` | Diagram opens, node select, Mermaid view, "Diagram as text"; keyboard: focus a node, Enter and Space open its details. |
| journeys | `chat.spec.ts` | Question, cited answer, sources list, evidence drawer. |
| journeys | `export.spec.ts` | PDF (`%PDF-`, real Chromium render) and Markdown downloads. |
| journeys | `responsive.spec.ts` | 375 and 1280 px: five pages, no horizontal overflow, screenshots to `e2e/.state/ui/`. |
| errors | `errors.spec.ts` | Invalid URL (nothing sent); oversize ZIP (form, and 413 `INPUT_TOO_LARGE` from the API); not a ZIP; project not found; unknown route 404; expired session (new session started, project gone, no hang); rate limit (429, "going a little fast ... try again", form stays usable). |
| live | `live.spec.ts` | On demand only, see above. |

## Defects the journeys found

Frontend (fixed):

- The progress page showed only the current stage label; it now lists every stage the server announced (`Pipeline stages`).
- Diagram nodes had no accessible name, and Enter/Space on a focused node did nothing (React Flow selects but `onNodeClick` never fires).
- Chat showed an empty pane while a thread's history loaded.
- Loading and error panels of a project were not in a `<main>` landmark (Lighthouse `landmark-one-main`); the ZIP input had no label.
- On a phone the PDF and Markdown export buttons were the same bare icon.
- Regenerate and export polling treated one failed poll (a 502 from the proxy while the API was busy) as a failed job although the worker finished it; both now tolerate up to five consecutive transient failures (unit-tested).
- Citation links had an accessible name that did not start with the visible text, and a diagram title skipped a heading level.

Backend (not edited here): no defect in `backend/src`. Observed under load only in the e2e launcher, where analysis shares the
API process: the Next proxy's `fetch` to the API failed (`fetch failed`, 502 envelope) a few times while embedding ran. Production
runs analysis in a separate worker, so this is a test-launcher artefact; the frontend hardening above is still correct. Observed, not a defect: `REVISING_GUIDE` ("Fixing unsupported statements") is
announced only when the verifier requests a rewrite; the scripted model's runs did not announce it, so the journey checks
the list against what the server announced rather than requiring it. The vitest test
`tests/progress-page.test.tsx` covers that label.

## UI polish (task 6)

| Item | Change |
|---|---|
| Mobile 375 px | Verified by screenshots (no overflow on home, progress, guide, architecture, chat). The architecture diagram is legible only by zooming on a phone; "Diagram as text" carries the content. Not redesigned. |
| Keyboard diagram | Nodes are in the tab order with `aria-label` "label, kind[, inferred]"; Enter and Space select. |
| Reduced motion | CSS already neutralised animations; added `MotionConfig reducedMotion="user"` (toasts) and non-smooth scroll in chat. |
| States | Chat history now has the same skeleton as other loads; error panels share `ErrorPanel`; project-level panels are `<main>`. |
| Metadata | `metadataBase`, Open Graph and Twitter card, `theme-color`, `app/icon.svg`, generated `opengraph-image`, project pages `noindex`. |
| Contrast | `--ink-faint` in light theme `#767983` (4.2:1 on paper) to `#666973` (5.2:1 on paper, 4.7:1 on paper-dim). |

## Lighthouse

Measured once per page with Lighthouse 13.5.0 (CLI, Playwright Chromium 153, headless, default mobile emulation and
throttling), against the production build served by `next start` and the e2e API, on a shared Windows laptop that was
busy with other work (Lighthouse's own warning: "device appears to have a slower CPU than Lighthouse expects").
Guide page measured with the e2e project's session cookie.

| Page | Performance | Accessibility | Best practices | SEO | FCP | LCP | TBT | CLS | Transfer |
|---|---|---|---|---|---|---|---|---|---|
| Home `/` (final build) | 36 | 100 | 96 | 100 | 2.3 s | 6.6 s | 2,390 ms | 0 | 338 KiB, 24 requests |
| Guide (final build) | 47 | 96 | 100 | 60 | 0.9 s | 5.0 s | 1,950 ms | 0.068 | 477 KiB, 39 requests |

Earlier run of the same pages, same machine, a few minutes before the last two label/heading fixes: home 75 / 100 / 96 / 100
(FCP 1.7 s, LCP 3.1 s, TBT 350 ms), guide 52 / 94 / 100 / 60 (FCP 0.9 s, LCP 4.9 s, TBT 1,030 ms). The performance score
and TBT moved by 2x to 7x between runs of the same code (Lighthouse benchmark index 922 to 1,557), so treat the
performance column as indicative only; transfer size and request count are stable. A trustworthy performance number
needs a quiet machine or CI (not measured).

Reading the rest:

- Accessibility 100 on home. Guide 96: the remaining finding is React Flow's attribution link (target size 47 x 14 px),
  left in place on purpose (library attribution).
- SEO 60 on the guide is `is-crawlable`: project pages are `noindex` by design.
- Best practices 96 on home is the console message for the first `GET /api/v1/sessions/current` answering 401 before the
  session is created (the intended bootstrap); not changed.
- Guide TBT is the cost of the guide's JavaScript (React Flow, Shiki, Mermaid load on demand) on a throttled CPU; the
  largest long tasks were framework chunks (about 540 ms and 310 ms in the first run).

Budget (proposed, not yet enforced in CI; derived from the above, to be re-measured on a quiet machine or in CI):

| Metric | Home | Guide |
|---|---|---|
| Accessibility score | 100 | at least 95 |
| Transfer | at most 400 KiB | at most 550 KiB |
| Requests | at most 30 | at most 45 |
| CLS | 0 | at most 0.1 |
| LCP (mobile, throttled) | at most 2.5 s | at most 4 s |
| TBT | at most 300 ms | at most 600 ms |

The LCP and TBT lines are targets, not met by the measurements above on this machine.

## Not covered

- Webkit and Firefox; only Chromium.
- A live-model run (the smoke exists, was not run in this change: it needs keys and spends quota).
- A real network GitHub ingestion in default CI (by design).
- Mid-page session expiry while polling (only reload after expiry).
