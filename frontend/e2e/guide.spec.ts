import { expect, test, type Page } from "@playwright/test";
import { runTool, sharedProjectId } from "./helpers";

const SHA = "0123456789abcdef0123456789abcdef01234567";

async function openGuide(page: Page) {
  await page.goto(`/projects/${sharedProjectId()}/guide`);
  await expect(page.getByRole("navigation", { name: "Guide chapters" }).first()).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("a chapter cites locations, and a citation opens the source excerpt", async ({ page }) => {
  test.setTimeout(240_000); // the tool starts a Python process
  runTool("unpin", sharedProjectId()); // a previous run may have left the project pinned
  await openGuide(page);
  const chapters = page.getByRole("navigation", { name: "Guide chapters" }).first().getByRole("listitem");
  expect(await chapters.count()).toBeGreaterThanOrEqual(4);

  // ZIP project: citations are buttons showing `path:Lx-Ly`, never the raw [E1] labels.
  const cite = page.getByRole("button", { name: /^Evidence: / }).first();
  await expect(cite).toBeVisible();
  await expect(cite).toHaveText(/\.py:L\d+/);
  expect(await page.locator("main").innerText()).not.toMatch(/\[E\d+\]/);
  await cite.click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText(/\.py/);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();

  await page.getByRole("navigation", { name: "Guide chapters" }).first().getByRole("button").nth(1).click();
  await expect(page.getByText(/^Chapter 2 ·/)).toBeVisible();
});

test("citations of a GitHub analysis are pinned to the analysed commit; the evidence appendix lists them", async ({
  page,
}) => {
  runTool("pin", sharedProjectId());
  await openGuide(page);
  const pinned = page.locator(`main a[href^="https://github.com/unfold-e2e/fastapi_rag/blob/${SHA}/"]`);
  await expect(pinned.first()).toBeVisible();
  await expect(pinned.first()).toHaveText(/:L\d+/);
  await expect(pinned.first()).toHaveAttribute("rel", /noopener/);

  const appendix = page.getByTestId("evidence-appendix");
  await expect(appendix).toBeVisible();
  await appendix.locator("summary").click();
  await expect(appendix).toContainText("GitHub");
  await expect(
    appendix.locator(`a[href^="https://github.com/unfold-e2e/fastapi_rag/blob/${SHA}/"]`).first(),
  ).toBeVisible();
});

test("regenerating one chapter replaces it and leaves the others unchanged", async ({ page, request }) => {
  const id = sharedProjectId();
  const guide = (await (await request.get(`/api/v1/projects/${id}/guide`)).json()) as { sections: { key: string }[] };
  const [first, other] = guide.sections;
  const read = async (key: string) =>
    JSON.stringify(await (await request.get(`/api/v1/projects/${id}/guide/sections/${key}`)).json());
  const otherBefore = await read(other!.key);

  await openGuide(page);
  await page.getByRole("button", { name: "Regenerate this chapter" }).click();
  await expect(page.getByRole("button", { name: "Regenerate this chapter" })).toBeDisabled();
  await expect(page.getByText("Chapter regenerated.")).toBeVisible({ timeout: 4 * 60_000 });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  expect(await read(other!.key)).toBe(otherBefore);
  const after = (await (await request.get(`/api/v1/projects/${id}/guide/sections/${first!.key}`)).json()) as {
    status: string;
  };
  expect(after.status).not.toBe("failed");
});
