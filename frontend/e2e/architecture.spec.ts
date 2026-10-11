import { expect, test } from "@playwright/test";
import { sharedProjectId } from "./helpers";

test("open the architecture diagram, select a node, switch to Mermaid and read it as text", async ({ page }) => {
  await page.goto(`/projects/${sharedProjectId()}/architecture`);
  await expect(page.getByRole("heading", { level: 1, name: "Architecture" })).toBeVisible();

  await expect(page.getByLabel(/interactive diagram/)).toBeVisible();
  const node = page.locator(".react-flow__node-unfold").first();
  await expect(node).toBeVisible();
  const label = (await node.locator("span").first().innerText()).trim();
  await node.click();
  await expect(page.getByRole("button", { name: "Close node details" })).toBeVisible();
  await expect(page.locator("figure").getByText(label, { exact: true }).first()).toBeVisible();

  await page.getByRole("radio", { name: "Mermaid" }).click();
  await expect(page.locator(".mermaid-host svg").first()).toBeVisible({ timeout: 30_000 });

  await page.getByText("Diagram as text").click();
  await expect(page.locator("figure details").getByText(label).first()).toBeVisible();
});

test("a diagram node can be reached and selected with the keyboard", async ({ page }) => {
  await page.goto(`/projects/${sharedProjectId()}/architecture`);
  const node = page.locator(".react-flow__node-unfold").first();
  await expect(node).toBeVisible();
  await expect(node).toHaveAttribute("tabindex", "0");
  await expect(node).toHaveAttribute("aria-label", /.+/);
  await node.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Close node details" })).toBeVisible();
  await page.getByRole("button", { name: "Close node details" }).click();
  await node.focus();
  await page.keyboard.press("Space");
  await expect(page.getByRole("button", { name: "Close node details" })).toBeVisible();
});
