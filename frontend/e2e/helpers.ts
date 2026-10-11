import { execFileSync } from "node:child_process";
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { BrowserContext, Page } from "@playwright/test";

const here = fileURLToPath(new URL(".", import.meta.url));
const FIXTURE = join(here, "..", "..", "evals", "fixtures", "repos", "fastapi_rag");
const STATE = join(here, ".state");

export const SESSION_COOKIE = "unfold_session";
export const SESSION_STATE = join(STATE, "session.json");
const PROJECT_STATE = join(STATE, "project.json");

// --- a ZIP of the fixture repository, built in memory (store-only: no compression library needed) ---------

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(data: Buffer): number {
  let c = 0xffffffff;
  for (const byte of data) c = (CRC_TABLE[(c ^ byte) & 0xff] as number) ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function* walk(dir: string, prefix = ""): Generator<{ name: string; data: Buffer }> {
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name === "__pycache__") continue;
    if (entry.isDirectory()) yield* walk(join(dir, entry.name), `${prefix}${entry.name}/`);
    else yield { name: `${prefix}${entry.name}`, data: readFileSync(join(dir, entry.name)) };
  }
}

export function fixtureZip(): Buffer {
  const parts: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  for (const { name, data } of walk(FIXTURE)) {
    const path = Buffer.from(`fastapi_rag/${name}`);
    const head = Buffer.alloc(30);
    head.writeUInt32LE(0x04034b50, 0);
    head.writeUInt16LE(20, 4);
    head.writeUInt32LE(crc32(data), 14);
    head.writeUInt32LE(data.length, 18);
    head.writeUInt32LE(data.length, 22);
    head.writeUInt16LE(path.length, 26);
    const dir = Buffer.alloc(46);
    dir.writeUInt32LE(0x02014b50, 0);
    dir.writeUInt16LE(20, 4);
    dir.writeUInt16LE(20, 6);
    dir.writeUInt32LE(head.readUInt32LE(14), 16);
    dir.writeUInt32LE(data.length, 20);
    dir.writeUInt32LE(data.length, 24);
    dir.writeUInt16LE(path.length, 28);
    dir.writeUInt32LE(offset, 42);
    central.push(Buffer.concat([dir, path]));
    parts.push(head, path, data);
    offset += head.length + path.length + data.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(central.length, 8);
  end.writeUInt16LE(central.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directory, end]);
}

// --- shared state between the `analysis` project and the journeys that reuse its finished project ---------

export async function saveProject(context: BrowserContext, projectId: string): Promise<void> {
  mkdirSync(STATE, { recursive: true });
  await context.storageState({ path: SESSION_STATE });
  writeFileSync(PROJECT_STATE, JSON.stringify({ projectId }));
}

export function sharedProjectId(): string {
  return (JSON.parse(readFileSync(PROJECT_STATE, "utf8")) as { projectId: string }).projectId;
}

// --- UI steps --------------------------------------------------------------------------------------------

/** Upload through the form: the fixture ZIP, a path on disk, or an in-memory file (under Playwright's 50 MB limit). */
export async function uploadZip(page: Page, file: string | { name: string; buffer: Buffer } = { name: "fastapi_rag.zip", buffer: fixtureZip() }) {
  await page.goto("/");
  await page
    .locator('input[type="file"]')
    .setInputFiles(typeof file === "string" ? file : { ...file, mimeType: "application/zip" });
  await page.getByRole("button", { name: /Unfold this project/ }).click();
}

export async function submitGithub(page: Page, url: string) {
  await page.goto("/");
  await page.getByLabel("Public GitHub repository").fill(url);
  await page.getByRole("button", { name: /Unfold this project/ }).click();
}

export async function sessionToken(context: BrowserContext): Promise<string> {
  const cookie = (await context.cookies()).find((c) => c.name === SESSION_COOKIE);
  if (!cookie) throw new Error("no session cookie");
  return cookie.value;
}

/** `frontend/e2e/tool.py`: the states the UI cannot reach itself (a pinned commit, an expired session). */
export function runTool(command: "pin" | "unpin" | "expire", ref: string): void {
  execFileSync("uv", ["run", "--no-sync", "python", "../frontend/e2e/tool.py", command, ref], {
    cwd: join(here, "..", "..", "backend"),
    stdio: "pipe",
  });
}
