"""Public GitHub repository acquisition via the REST API + codeload tarball (LLD §6).

No git binary is used, so repository hooks and submodules can never run. Only api.github.com and
codeload.github.com are contacted; redirects to any other host are refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, IngestionError, InputError

_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9_.\-/]{1,200}$")
ALLOWED_HOSTS = {"api.github.com", "codeload.github.com"}


@dataclass(frozen=True)
class GitHubRef:
    owner: str
    repo: str
    ref: str | None = None

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


@dataclass(frozen=True)
class FetchedRepo:
    archive_path: Path
    owner: str
    repo: str
    default_branch: str
    commit_sha: str
    size_kb: int
    description: str | None


def parse_github_url(url: str) -> GitHubRef:
    """Accept only https://github.com/{owner}/{repo}[.git][/tree/{ref}] (query/fragment ignored)."""
    candidate = url.strip()
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
        raise InputError(
            "Only https://github.com/<owner>/<repo> URLs are supported.", code=ErrorCode.INVALID_GITHUB_URL
        )
    if parsed.username or parsed.password or parsed.port:
        raise InputError(
            "GitHub URLs must not contain credentials or ports.", code=ErrorCode.INVALID_GITHUB_URL
        )
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise InputError("The URL must point to a repository.", code=ErrorCode.INVALID_GITHUB_URL)
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not (_NAME.match(owner) and _NAME.match(repo)) or repo in {".", ".."} or owner in {".", ".."}:
        raise InputError("Invalid GitHub owner or repository name.", code=ErrorCode.INVALID_GITHUB_URL)
    ref: str | None = None
    if len(parts) >= 4 and parts[2] == "tree":
        ref = "/".join(parts[3:])
        if not _REF.match(ref) or ".." in ref:
            raise InputError("Invalid branch or tag in URL.", code=ErrorCode.INVALID_GITHUB_URL)
    elif len(parts) > 2:
        raise InputError(
            "Link to the repository root or a /tree/<branch> page.", code=ErrorCode.INVALID_GITHUB_URL
        )
    return GitHubRef(owner, repo, ref)


async def _guard_host(request: httpx.Request) -> None:
    if request.url.host not in ALLOWED_HOSTS or request.url.scheme != "https":
        raise IngestionError(
            f"Refusing to contact {request.url.host}", code=ErrorCode.REPOSITORY_UNAVAILABLE, retryable=False
        )


class GitHubClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "unfold-analyzer",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if settings.github_token is not None:
            headers["Authorization"] = f"Bearer {settings.github_token.get_secret_value()}"
        self._settings = settings
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(30.0, read=120.0),
            follow_redirects=True,
            event_hooks={"request": [_guard_host]},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str) -> dict[str, object]:
        url = f"{self._settings.github_api_url}{path}"
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise IngestionError(f"GitHub is unreachable: {type(exc).__name__}") from exc
        if resp.status_code in (401, 404):
            raise InputError("Repository not found or not public.", code=ErrorCode.REPOSITORY_NOT_PUBLIC)
        if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
            raise IngestionError("GitHub API rate limit reached; retry later.", retryable=True)
        if resp.status_code >= 400:
            raise IngestionError(f"GitHub API error {resp.status_code}", retryable=resp.status_code >= 500)
        data = resp.json()
        if not isinstance(data, dict):
            raise IngestionError("Unexpected GitHub API response", retryable=False)
        return data

    async def resolve(self, ref: GitHubRef) -> tuple[str, str, int, str | None]:
        """Return (default_branch, commit_sha, size_kb, description); enforce public + size policy."""
        meta = await self._get_json(f"/repos/{ref.owner}/{ref.repo}")
        if meta.get("private") or meta.get("visibility") not in (None, "public"):
            raise InputError("Only public repositories are supported.", code=ErrorCode.REPOSITORY_NOT_PUBLIC)
        size_raw = meta.get("size")
        size_kb = size_raw if isinstance(size_raw, int) else 0
        if size_kb * 1024 > self._settings.max_repo_archive_bytes * 4:
            raise InputError("The repository exceeds the processing policy.", code=ErrorCode.INPUT_TOO_LARGE)
        default_branch = str(meta.get("default_branch") or "main")
        target = ref.ref or default_branch
        commit = await self._get_json(f"/repos/{ref.owner}/{ref.repo}/commits/{target}")
        sha = str(commit.get("sha") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise IngestionError("Could not resolve the commit.", retryable=False)
        desc = meta.get("description")
        return default_branch, sha, size_kb, str(desc) if desc else None

    async def download(self, ref: GitHubRef, sha: str, dest: Path) -> Path:
        url = f"{self._settings.github_codeload_url}/{ref.owner}/{ref.repo}/tar.gz/{sha}"
        limit = self._settings.max_repo_archive_bytes
        dest.parent.mkdir(parents=True, exist_ok=True)
        received = 0
        try:
            async with self._client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    raise InputError(
                        "Repository archive not available.", code=ErrorCode.REPOSITORY_NOT_PUBLIC
                    )
                if resp.status_code >= 400:
                    raise IngestionError(f"Archive download failed ({resp.status_code})")
                with dest.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        received += len(chunk)
                        if received > limit:
                            raise InputError(
                                "The repository archive exceeds the size limit.",
                                code=ErrorCode.INPUT_TOO_LARGE,
                            )
                        fh.write(chunk)
        except httpx.HTTPError as exc:
            raise IngestionError(f"Archive download failed: {type(exc).__name__}") from exc
        return dest

    async def fetch(self, ref: GitHubRef, workdir: Path) -> FetchedRepo:
        default_branch, sha, size_kb, desc = await self.resolve(ref)
        archive = await self.download(ref, sha, workdir / "repo.tar.gz")
        return FetchedRepo(archive, ref.owner, ref.repo, ref.ref or default_branch, sha, size_kb, desc)
