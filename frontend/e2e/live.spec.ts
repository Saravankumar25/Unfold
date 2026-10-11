import { expect, test } from "@playwright/test";
import { submitGithub } from "./helpers";

// On demand only: `E2E_LIVE=1 npx playwright test --project=live` (the config registers this project only then).
// It spends real model quota and needs the network: GitHub, the Gemini keys of the repository's .env.
test.skip(process.env.E2E_LIVE !== "1", "live smoke runs on demand (E2E_LIVE=1)");

test("@live a tiny public repository is analysed by the real pipeline and the real model answers about it", async ({
  page,
}) => {
  await submitGithub(page, "https://github.com/octocat/Spoon-Knife");
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { level: 1, name: "Your project is unfolded." })).toBeVisible({
    timeout: 15 * 60_000,
  });

  await page.getByRole("link", { name: /Open project guide/ }).click();
  await expect(page.getByRole("navigation", { name: "Guide chapters" }).first()).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  await page.getByRole("link", { name: "Ask Unfold" }).first().click();
  await page.getByPlaceholder("Ask about this project…").fill("What does this project contain?");
  await page.keyboard.press("Enter");
  const answer = page.getByRole("article", { name: "Unfold's answer" });
  await expect(answer).toBeVisible();
  await expect(answer.getByRole("status")).toBeHidden({ timeout: 3 * 60_000 });
  await expect(answer).not.toContainText("Something went wrong");
  await expect(answer.getByRole("button", { name: /\d+ sources?/ })).toBeVisible();
});
