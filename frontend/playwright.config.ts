import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end journeys against the real stack (docs/design/frontend-e2e.md).
 *
 * - `api` web server: the real FastAPI app on the docker-compose stores with the scripted model (e2e/api_server.py),
 *   so no LLM quota is spent. `E2E_LIVE=1` swaps in the real gateway and worker queue and runs only the live smoke.
 * - `web` web server: `next start` of the production build (`npm run e2e` runs `next build` first), proxying /api.
 * - Projects run in order with one worker: `analysis` creates the finished project the `journeys` reuse.
 */
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3011);
const API_PORT = Number(process.env.E2E_API_PORT ?? 8010);
const live = process.env.E2E_LIVE === "1";
const chromium = { ...devices["Desktop Chrome"] };

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  outputDir: "test-results",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: live
    ? [{ name: "live", testMatch: /live\.spec\.ts/, timeout: 20 * 60_000, use: chromium }]
    : [
        { name: "analysis", testMatch: /analysis\.spec\.ts/, timeout: 10 * 60_000, use: chromium },
        {
          name: "journeys",
          dependencies: ["analysis"],
          testMatch: /(guide|architecture|chat|export|responsive)\.spec\.ts/,
          timeout: 5 * 60_000,
          use: { ...chromium, storageState: "e2e/.state/session.json" },
        },
        { name: "errors", testMatch: /errors\.spec\.ts/, use: chromium },
      ],
  webServer: [
    {
      command: "uv run python ../frontend/e2e/api_server.py",
      cwd: "../backend",
      url: `http://127.0.0.1:${API_PORT}/ready`,
      timeout: 180_000,
      reuseExistingServer: !process.env.CI,
      env: { E2E_API_PORT: String(API_PORT), ...(live ? { E2E_LIVE: "1" } : {}) },
    },
    {
      command: `npx next start -p ${WEB_PORT} -H 127.0.0.1`, // `npm run e2e` builds first
      url: `http://127.0.0.1:${WEB_PORT}`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: { API_INTERNAL_URL: `http://127.0.0.1:${API_PORT}` },
    },
  ],
});
