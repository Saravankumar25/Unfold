import { expect, test, type Page } from "@playwright/test";
import { createReadStream, writeFileSync } from "node:fs";
import { runTool, sessionToken, submitGithub, uploadZip } from "./helpers";

// The route announcer is also role=alert; the app's own messages are the ones that carry text.
const alert = (page: Page) => page.locator('[role="alert"]:not(#__next-route-announcer__)');

test("an invalid repository URL is explained before anything is sent", async ({ page }) => {
  let sent = 0;
  page.on("request", (r) => {
    if (r.url().includes("/projects/github")) sent++;
  });
  await submitGithub(page, "http://example.com/not/github");
  await expect(alert(page)).toContainText("Only https://github.com/<owner>/<repo> URLs are supported.");
  await page.getByLabel("Public GitHub repository").fill("https://github.com/only-owner");
  await page.getByRole("button", { name: /Unfold this project/ }).click();
  await expect(alert(page)).toContainText("must point to a repository");
  expect(sent).toBe(0);
});

test("an oversize ZIP is refused in the form and by the API", async ({ page, request }) => {
  test.setTimeout(240_000); // two 50 MB transfers through the proxy
  const big = test.info().outputPath("huge.zip"); // Playwright refuses in-memory buffers over 50 MB
  writeFileSync(big, Buffer.alloc(50 * 1024 * 1024 + 1));
  await uploadZip(page, big);
  await expect(alert(page)).toContainText("ZIP uploads are limited to 50 MB");
  await expect(page).toHaveURL(/\/$/);

  // The same file straight at the API (the form's check is a convenience, not the control).
  await request.post("/api/v1/sessions");
  const response = await request.post("/api/v1/projects/zip", {
    multipart: { file: createReadStream(big) },
  });
  expect(response.status()).toBe(413);
  expect(((await response.json()) as { error: { code: string } }).error.code).toBe("INPUT_TOO_LARGE");
});

test("a file that is not a ZIP is rejected by the API with a readable message", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "fake.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("this is not a zip archive"),
  });
  await page.getByRole("button", { name: /Unfold this project/ }).click();
  await expect(alert(page)).toContainText("isn't a valid ZIP archive");
});

test("a project that does not exist says so and leads back home", async ({ page }) => {
  await page.goto("/projects/00000000-0000-4000-8000-000000000000");
  await expect(alert(page)).toContainText("This project isn't available");
  await expect(alert(page)).toContainText("couldn't find that");
  await page.getByRole("link", { name: /Back to Unfold/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: /Prepare your/ })).toBeVisible();
});

test("an unknown page is a 404 page, not a crash", async ({ page }) => {
  const response = await page.goto("/no/such/page");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "There is nothing here" })).toBeVisible();
});

test("an expired session: the project is gone, a fresh session is started, nothing hangs", async ({
  page,
  context,
}) => {
  test.setTimeout(240_000); // the tool starts a Python process
  await submitGithub(page, "https://github.com/octocat/Spoon-Knife");
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  const first = await sessionToken(context);

  runTool("expire", first);
  await page.reload();
  await expect(alert(page)).toContainText("This project isn't available");
  await expect(alert(page)).toContainText("expired with your session");
  expect(await sessionToken(context)).not.toBe(first);

  await page.getByRole("link", { name: /Back to Unfold/ }).click();
  await expect(page.getByRole("button", { name: /Unfold this project/ })).toBeEnabled();
});

test("being rate limited shows a retry message and the form stays usable", async ({ page }) => {
  // The e2e API allows three new projects per session per hour (api_server.py).
  for (let i = 0; i < 3; i++) {
    await submitGithub(page, `https://github.com/octocat/Hello-World-${i}`);
    await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  }
  await submitGithub(page, "https://github.com/octocat/Hello-World-4");
  await expect(alert(page)).toContainText("going a little fast");
  await expect(alert(page)).toContainText("try again");
  await expect(page.getByRole("button", { name: /Unfold this project/ })).toBeEnabled();
});
