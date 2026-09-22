/**
 * Client-side input validation mirroring the backend exactly, so users get instant feedback:
 * - GitHub URL: `backend/src/unfold/ingestion/github.py::parse_github_url`
 *   (https only; host github.com | www.github.com; no credentials/port; `/{owner}/{repo}[.git][/tree/{ref}]`;
 *   query and fragment ignored).
 * - ZIP: `MAX_ZIP_BYTES` (50 MiB) and `.zip` archives only (`/projects/zip`).
 * The backend remains the authority; this only avoids a round-trip for obvious mistakes.
 */

const NAME_RE = /^[A-Za-z0-9_.-]{1,100}$/;
const REF_RE = /^[A-Za-z0-9_./-]{1,200}$/;

export const MAX_ZIP_BYTES = 50 * 1024 * 1024;

export interface GithubRef {
  owner: string;
  repo: string;
  ref: string | null;
  /** The URL to send to the API (scheme added when the user omitted it). */
  url: string;
}

export type Validation<T> = { ok: true; value: T } | { ok: false; error: string };

/**
 * `github.com/owner/repo` (no scheme) is accepted as a convenience and normalised to `https://…`,
 * because the landing placeholder shows that form; any *explicit* non-https scheme is rejected like the API does.
 */
export function validateGithubUrl(input: string): Validation<GithubRef> {
  let candidate = (input ?? "").trim();
  if (!candidate) return { ok: false, error: "Paste a public GitHub repository URL." };
  if (/^(www\.)?github\.com\//i.test(candidate)) candidate = `https://${candidate}`;

  const m = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)/.exec(candidate);
  if (!m) return { ok: false, error: "Only https://github.com/<owner>/<repo> URLs are supported." };
  const scheme = (m[1] ?? "").toLowerCase();
  const authority = m[2] ?? "";
  const path = m[3] ?? "";

  const at = authority.lastIndexOf("@");
  const hostPort = at === -1 ? authority : authority.slice(at + 1);
  const colon = hostPort.lastIndexOf(":");
  const host = (colon === -1 ? hostPort : hostPort.slice(0, colon)).toLowerCase();

  if (scheme !== "https" || (host !== "github.com" && host !== "www.github.com")) {
    return { ok: false, error: "Only https://github.com/<owner>/<repo> URLs are supported." };
  }
  if (at !== -1 || colon !== -1) {
    return { ok: false, error: "GitHub URLs must not contain credentials or ports." };
  }

  const parts = path.split("/").filter(Boolean);
  if (parts.length < 2) return { ok: false, error: "The URL must point to a repository." };
  const owner = parts[0] as string;
  const rawRepo = parts[1] as string;
  const repo = rawRepo.endsWith(".git") ? rawRepo.slice(0, -4) : rawRepo;
  if (!NAME_RE.test(owner) || !NAME_RE.test(repo) || [owner, repo].some((p) => p === "." || p === "..")) {
    return { ok: false, error: "Invalid GitHub owner or repository name." };
  }

  let ref: string | null = null;
  if (parts.length >= 4 && parts[2] === "tree") {
    ref = parts.slice(3).join("/");
    if (!REF_RE.test(ref) || ref.includes("..")) return { ok: false, error: "Invalid branch or tag in URL." };
  } else if (parts.length > 2) {
    return { ok: false, error: "Link to the repository root or a /tree/<branch> page." };
  }
  return { ok: true, value: { owner, repo, ref, url: candidate } };
}

export function validateZipFile(file: Pick<File, "name" | "size" | "type"> | null | undefined): Validation<true> {
  if (!file) return { ok: false, error: "Choose a .zip file to upload." };
  const isZipName = file.name.toLowerCase().endsWith(".zip");
  const isZipType = ["application/zip", "application/x-zip-compressed", "application/x-zip"].includes(file.type);
  if (!isZipName && !isZipType) return { ok: false, error: "Only .zip archives are supported." };
  if (file.size === 0) return { ok: false, error: "This file is empty." };
  if (file.size > MAX_ZIP_BYTES) {
    return { ok: false, error: "ZIP uploads are limited to 50 MB. Remove build output or dependencies and try again." };
  }
  return { ok: true, value: true };
}

export const MAX_INSTRUCTION_CHARS = 1000;
