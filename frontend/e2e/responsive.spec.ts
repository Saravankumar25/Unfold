import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { sharedProjectId } from "./helpers";

// Screenshots land in e2e/.state/ui/<name>-<width>.png (git-ignored, kept across runs); the assertion is "no sideways scroll".
const WIDTHS = [375, 1280];

for (const width of WIDTHS) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height: width < 600 ? 812 : 800 } });

    const pages: [string, () => string, (p: Page) => Promise<void>][] = [
      ["home", () => "/", async (p) => void (await p.getByRole("heading", { level: 1 }).waitFor())],
      ["guide", () => `/projects/${sharedProjectId()}/guide`, async (p) => void (await p.getByRole("heading", { level: 1 }).waitFor())],
      [
        "architecture",
        () => `/projects/${sharedProjectId()}/architecture`,
        async (p) => void (await p.locator(".react-flow__node-unfold").first().waitFor()),
      ],
      ["chat", () => `/projects/${sharedProjectId()}/chat`, async (p) => void (await p.getByRole("heading", { level: 1 }).waitFor())],
      ["progress", () => `/projects/${sharedProjectId()}`, async (p) => void (await p.getByRole("heading", { level: 1 }).waitFor())],
    ];

    for (const [name, url, ready] of pages) {
      test(`${name} fits the viewport`, async ({ page }) => {
        await page.goto(url());
        await ready(page);
        mkdirSync("e2e/.state/ui", { recursive: true });
        await page.screenshot({ path: `e2e/.state/ui/${name}-${width}.png`, fullPage: true });
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, "horizontal overflow in px").toBeLessThanOrEqual(0);
      });
    }
  });
}
