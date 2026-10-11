import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { sharedProjectId } from "./helpers";

test("export the guide as PDF and as Markdown", async ({ page }) => {
  await page.goto(`/projects/${sharedProjectId()}/guide`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const pdf = page.waitForEvent("download", { timeout: 120_000 });
  await page.getByRole("button", { name: "Export PDF" }).click();
  const pdfFile = await pdf;
  expect(pdfFile.suggestedFilename()).toMatch(/\.pdf$/);
  const bytes = readFileSync(await pdfFile.path());
  expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
  expect(bytes.length).toBeGreaterThan(10_000);

  const md = page.waitForEvent("download", { timeout: 120_000 });
  await page.getByRole("button", { name: "Markdown" }).click();
  const mdFile = await md;
  expect(mdFile.suggestedFilename()).toMatch(/\.md$/);
  const text = readFileSync(await mdFile.path(), "utf8");
  expect(text).toMatch(/^# /m);
  expect(text.length).toBeGreaterThan(500);
});
