import { expect, test } from "@playwright/test";
import { sharedProjectId } from "./helpers";

test("ask a question, get a cited answer, and open the evidence behind it", async ({ page }) => {
  await page.goto(`/projects/${sharedProjectId()}/chat`);
  await expect(page.getByRole("heading", { level: 1, name: "Question the code." })).toBeVisible();

  await page.getByPlaceholder("Ask about this project…").fill("Where is the retriever implemented?");
  await page.keyboard.press("Enter");

  const answer = page.getByRole("article", { name: "Unfold's answer" });
  await expect(answer).toBeVisible();
  await expect(answer.getByRole("status")).toBeHidden({ timeout: 90_000 });
  await expect(answer).toContainText(/\.py/);

  const sources = answer.getByRole("button", { name: /\d+ sources?/ });
  await expect(sources).toBeVisible();
  await sources.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");

  const cite = answer.getByRole("button", { name: /^Evidence: / }).first();
  await expect(cite).toBeVisible();
  await cite.click();
  await expect(page.getByRole("dialog")).toContainText(/\.py/);
});
