import { expect, test } from "@playwright/test";
import { fixtureZip, saveProject, sessionToken, submitGithub, uploadZip } from "./helpers";

test("create a session, upload a ZIP, watch every stage, open the finished project", async ({ page, context }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: /Prepare your/ })).toBeVisible();
  await expect.poll(() => sessionToken(context).catch(() => ""), { message: "session cookie" }).not.toBe("");
  const cookie = (await context.cookies()).find((c) => c.name === "unfold_session");
  expect(cookie).toMatchObject({ httpOnly: true, sameSite: "Lax", path: "/" });

  await uploadZip(page, { name: "fastapi_rag.zip", buffer: fixtureZip() });
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  const projectId = new URL(page.url()).pathname.split("/").pop() as string;
  await expect(page.getByRole("heading", { level: 1, name: "Reading between the lines." })).toBeVisible();

  const done = page.getByRole("heading", { level: 1, name: "Your project is unfolded." });
  await expect(done).toBeVisible({ timeout: 9 * 60_000 });

  // Every stage the server announced is on screen, with the server's own label.
  const events = (await (await page.request.get(`/api/v1/projects/${projectId}/progress?after=0`)).json()) as {
    events: { type: string; index: number; label: string }[];
  };
  const announced = [...new Set(events.events.filter((e) => e.type !== "heartbeat").map((e) => e.label))];
  const shown = await page.getByRole("list", { name: "Pipeline stages" }).getByRole("listitem").allInnerTexts();
  expect(shown.map((s) => s.trim())).toEqual(announced);
  expect(shown.length).toBeGreaterThanOrEqual(20);
  expect(shown).toContain("Researching each chapter"); // RESEARCHING_GUIDE
  expect(shown).toContain("Ready");
  // REVISING_GUIDE ("Fixing unsupported statements") appears only when the verifier asks for a rewrite;
  // the scripted model rarely does, so it is checked against what was announced, not required.
  expect(shown.includes("Fixing unsupported statements")).toBe(announced.includes("Fixing unsupported statements"));

  await page.getByRole("link", { name: /Open project guide/ }).click();
  await expect(page.getByRole("navigation", { name: "Guide chapters" })).toBeVisible();
  await saveProject(context, projectId);
});

test("submit a GitHub URL: the project is created and waits in the queue", async ({ page }) => {
  await submitGithub(page, "https://github.com/octocat/Hello-World");
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Hello-World")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Reading between the lines." })).toBeVisible();
  await expect(page.getByText("Queued").first()).toBeVisible();
});
