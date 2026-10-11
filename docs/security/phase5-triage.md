# Phase 5 security triage: dependencies and secrets

Scope: plan task 2 (targeted part). The full multi-agent `claude-security` scan is deferred until phase 4 is merged.
Run on 2026-10-11 on branch `phase-5-hardening` (from `main` at `bd2266c`). Only results actually obtained are
recorded here; anything that did not run says so.

## 1. Backend dependencies (`pip-audit`, from `backend/uv.lock`)

Command: `uv export --frozen --no-hashes --no-emit-project -o req.txt` then
`uvx pip-audit -r req.txt --no-deps --disable-pip` (121 pinned packages, runtime and dev groups).

| Package | Installed | Advisory | Reachability | Decision |
|---|---|---|---|---|
| `pyjwt` | 2.14.0 | PYSEC-2026-4141 (GHSA-42vr-xj54-vc7v / CVE-2026-101918), PYSEC-2026-4183 (GHSA-x33g-cr3x-6449 / CVE-2026-102275) | Transitive of `redis` 5.3.1 (its optional token-auth helpers). Unfold never decodes a JWT, and the Redis client is configured with a plain URL, so the code path is not reachable. | **Fixed by upgrade**: `uv lock --upgrade-package pyjwt` took it to 2.15.1 (the lock file is the only change). |

Re-run after the upgrade: `No known vulnerabilities found` (exit 0).

## 2. Frontend dependencies (`npm audit --omit=dev`)

Run against `frontend/package-lock.json` as it stood in the shared worktree (10 findings: 1 critical, 7 high,
2 low). `frontend/**` is owned by another agent and its lock file was being edited, so **nothing was changed
here**. Each row says what to do; all of them are lock-file refreshes inside the declared semver ranges
(`next ^16.3.5`, `mermaid ^12.0.0`, `dompurify ^3.4.15`).

| Package (installed) | Severity | Advisory | Reachable in Unfold? | Action for the frontend owner |
|---|---|---|---|---|
| `next` 16.3.5 | **critical** | GHSA-vcvr-r3jv-pc5j: RCE in `next/og` `ImageResponse`, fixed in 16.3.6 | **Yes, once task 6 lands**: `frontend/src/app/opengraph-image.tsx` uses `ImageResponse`. Do not ship that file on 16.3.5. | `npm update next` (16.3.8 or later fixes this and the six below). Must be done before phase 6. |
| `next` 16.3.5 | high | GHSA-cjq9-62q9-8jv4: SSRF in Image Optimization | Only if `next/image` optimisation is enabled for remote hosts (Vercel runs it). Fixed by the same bump. | same |
| `next` 16.3.5 | moderate (4) / low (1) | cache poisoning of SSG/ISR (self-hosted only), `use cache` draft-mode leak, metadata image routes, dev-server MCP endpoint | Not reachable on Vercel (not self-hosted) and Unfold does not use `use cache`; fixed by the same bump. | same |
| `sharp` 0.35.4 (optional, via `next`) | high | GHSA-wq5f-xc86-pv6w: librsvg dependency | Image optimisation only. | `npm update sharp` (>= 0.35.5) |
| `source-map-js` 1.2.1 (via `postcss`) | high | GHSA-68fv-2mgg-jv7q: event-loop DoS on crafted source maps | Build time only; no source map from outside is parsed. | `npm update source-map-js` (>= 1.2.2) |
| `lodash-es` 4.17.23 (via `mermaid`) | high | GHSA-r5fr-rjxr-66jc (`_.template` code injection), GHSA-f23m-r3pf-42rh (prototype pollution) | `mermaid` does not call `_.template` with user data; the diagram source is sanitised by our own `sanitize_mermaid` before it is rendered. | `npm update lodash-es` (>= 4.17.24) |
| `chevrotain` 11.1.2 and its `@chevrotain/*` (via `mermaid`) | high | advisory range 11.0.0 - 11.2.0 | Parser generator inside `mermaid`; not fed untrusted grammar. | `npm update chevrotain` |
| `mermaid` 12.0.0 | high (inherited) | flagged only because of the three rows above; `npm audit` proposes `mermaid@10.8.0`, which is a **downgrade** and must not be applied | n/a | Take the transitive updates above; the finding clears with them. Do not follow the suggested downgrade. |
| `katex` 0.16.47 (via `mermaid`) | low | GHSA-238p-pmpm-9mq7 prototype pollution bypassing `trust` | Math rendering is not used. | `npm update katex` (>= 0.18.2) |
| `dompurify` 3.4.15 | low | GHSA-p98j-92pf-mc4p, GHSA-6688-9rhm-gjv2: only the `IN_PLACE` mode | `frontend/src/lib/sanitize.ts` does not use `IN_PLACE`. | `npm update dompurify` when a fixed release exists |

Not covered by `npm audit`: the Mermaid bundle vendored for PDF export
(`backend/src/unfold/exports/static/mermaid.min.js`, 11.17.2, hash in `VENDORED.md`). It runs in Chromium with
JavaScript on, offline, with a CSP and `securityLevel` forced by `MERMAID_CONFIG`, on diagram source that went
through `sanitize_mermaid`. Check it against the Mermaid advisories when it is next refreshed.

## 3. Secrets scan

| Tool | Scope | Result |
|---|---|---|
| Project scanner (`unfold.security.secrets.redact`) | 490 tracked files in the tree | 226 detections: 191 `assignment`, 25 `url_credentials`, 9 `openai_key`, 1 `aws_access_key`. All reviewed (values never printed): none is a live credential. Breakdown below. |
| Project scanner | added lines of every commit reachable from `HEAD` (57 commits) | 74 distinct commit/file/kind triples. The specific-pattern ones (`openai_key`, `aws_access_key`) are exactly the redaction fixtures listed below; the rest are the heuristic `assignment` / `url_credentials` kinds, which were reviewed in the tree (same files) but not line by line in each historical revision. No provider-key, token or private-key pattern appears anywhere in history outside the fixtures. |
| Tracked-file names in history | `.env`, `*.pem`, `id_rsa`, `*.p12`, `*.pfx` | none ever tracked; only the three `.env.example` templates, all with empty or documented-default values. |
| `trivy fs --scanners secret` (Trivy 0.74.0) on `git archive HEAD` | the committed tree | 0 secrets. |
| `gitleaks` | n/a | **not installed on this machine, not run.** Trivy above is the second opinion. |

Review of the project scanner's detections (it is deliberately generous):

* `assignment` (191): 99 are inside the minified Mermaid bundle, 1 in a stored eval output, 12 are empty
  `GEMINI_API_KEY_n=` template lines in `.env.example` and `docs/specs/README_original.md` that the pattern matched
  across a line break, and 79 are in source, tests and fixture repositories: identifiers containing `token`
  (`budget_tokens`, `token_hash`, `first_token_ms`, `ev.tokens`, `tokens_in=`), `password: z.string()` in the
  fixture repositories, strings that exist to be redacted (`letmein12345`), and the documented development
  defaults in `core/config.py` that production start-up refuses
  (`test_security_hardening.py::test_production_refuses_unsafe_defaults`).
* `url_credentials` (25): the local development database URL (`unfold:<default>@localhost`, same default
  refused in production) and fixture repositories' compose files.
* `openai_key` (9) and `aws_access_key` (1): the strings used to prove redaction works (`test_chat_answer`,
  `test_chat_api`, `evals/gold/injection.json`, the AWS documentation example key in
  `test_guide_critique_semantic.py`) and the fixture repository's `DEV_OPENAI_API_KEY`.

No `.env` content was read into this document or into any log.

## 4. Defects found by the targeted pass (fixed)

See `docs/security/redteam.md` for the attack-to-test matrix. Findings F-1 to F-5 there are the defects; all are
fixed with a test that failed first. Reliability defects R-1 and R-2 are in `docs/design/reliability.md`.

## 5. Residual risk and what was not run

* Full `claude-security` multi-agent scan: deferred to after phase 4 (as planned).
* Frontend findings above are open until the frontend owner refreshes the lock file; the critical `next/og` one
  gates phase 6.
* `pip-audit` and `npm audit` see published advisories only; they say nothing about the vendored Mermaid bundle
  or about the Chromium build Playwright downloads.
