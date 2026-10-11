"""Archive and URL ingestion limits, re-checked in phase 5 (task 2b).

`test_archive.py` holds the first-pass cases. These are the ones that attack the limits themselves: entries that do
not count, headers that lie, nested archives, tar members of every type, Windows separators, and the GitHub
download path (redirect to another host, endless body, hostile tarball, look-alike URLs).
"""

from __future__ import annotations

import io
import struct
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, IngestionError, InputError, SecurityRejection, UnfoldError
from unfold.ingestion.archive import ExtractionLimits, normalize_member_path, safe_extract
from unfold.ingestion.github import GitHubClient, GitHubRef, parse_github_url
from unfold.ingestion.service import acquire_github

LIMITS = ExtractionLimits(
    max_extracted_bytes=5 * 1024 * 1024,
    max_file_count=50,
    max_single_file_bytes=1024 * 1024,
    max_path_depth=8,
    max_compression_ratio=100,
)


def _zip(path: Path, entries: dict[str, bytes], *, method: int = zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", method) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _tar_gz(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for info, data in members:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def _file(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def _extracted(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


# --- entries that must count -------------------------------------------------------------------------------


def test_a_flood_of_directory_entries_is_refused_like_a_flood_of_files(tmp_path: Path) -> None:
    arc = tmp_path / "dirs.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        for i in range(LIMITS.max_file_count * 3):
            zf.writestr(f"d{i}/", b"")
        zf.writestr("keep.txt", b"x")
    with pytest.raises(SecurityRejection) as ei:
        safe_extract(arc, tmp_path / "out", LIMITS)
    assert ei.value.code == ErrorCode.INPUT_TOO_LARGE
    assert not (tmp_path / "out").exists()  # nothing is left behind


def test_a_flood_of_symlink_entries_is_refused(tmp_path: Path) -> None:
    import stat

    arc = tmp_path / "links.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        for i in range(LIMITS.max_file_count * 3):
            info = zipfile.ZipInfo(f"l{i}")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
        zf.writestr("keep.txt", b"x")
    with pytest.raises(SecurityRejection):
        safe_extract(arc, tmp_path / "out", LIMITS)


def test_tar_entry_flood_is_refused_without_extracting(tmp_path: Path) -> None:
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    for i in range(LIMITS.max_file_count * 3):
        d = tarfile.TarInfo(f"d{i}")
        d.type = tarfile.DIRTYPE
        members.append((d, None))
    with pytest.raises(SecurityRejection):
        safe_extract(_tar_gz(tmp_path / "d.tar.gz", members), tmp_path / "out", LIMITS)
    assert not (tmp_path / "out").exists()


# --- headers that lie ----------------------------------------------------------------------------------------


def _patch_declared_size(archive: Path, new_size: int) -> None:
    """Rewrite every declared uncompressed size (local header and central directory) of a one-entry zip."""
    raw = bytearray(archive.read_bytes())
    local = raw.find(b"PK\x03\x04")
    struct.pack_into("<I", raw, local + 22, new_size)
    central = raw.find(b"PK\x01\x02")
    struct.pack_into("<I", raw, central + 24, new_size)
    archive.write_bytes(bytes(raw))


def test_a_header_that_understates_the_size_cannot_smuggle_a_big_file(tmp_path: Path) -> None:
    arc = _zip(tmp_path / "lie.zip", {"big.bin": b"A" * (3 * 1024 * 1024)})
    _patch_declared_size(arc, 10)  # says 10 bytes, the stream holds 3 MiB
    with pytest.raises(UnfoldError):
        safe_extract(arc, tmp_path / "out", LIMITS)
    leftovers = _extracted(tmp_path / "out") if (tmp_path / "out").exists() else []
    assert not leftovers  # rejected as a whole, and what was written is removed


def test_a_header_that_overstates_the_size_is_not_trusted_either(tmp_path: Path) -> None:
    arc = _zip(tmp_path / "lie2.zip", {"small.txt": b"hello"})
    _patch_declared_size(arc, 2 * 1024 * 1024 * 1024)
    with pytest.raises(UnfoldError):
        safe_extract(arc, tmp_path / "out", LIMITS)


def test_ratio_is_enforced_on_bytes_actually_read(tmp_path: Path) -> None:
    limits = ExtractionLimits(512 * 1024 * 1024, 50, 512 * 1024 * 1024, 8, 100)
    arc = _zip(tmp_path / "ratio.zip", {"zeros.bin": b"\0" * (64 * 1024 * 1024)})
    with pytest.raises(SecurityRejection) as ei:
        safe_extract(arc, tmp_path / "out", limits)
    assert ei.value.code == ErrorCode.ZIP_BOMB_DETECTED
    assert not (tmp_path / "out").exists()


def test_huge_single_file_is_recorded_not_written_and_the_rest_extracts(tmp_path: Path) -> None:
    arc = _zip(tmp_path / "huge.zip", {"README.md": b"# hi", "data/huge.csv": b"1,2,3\n" * 400_000})
    rep = safe_extract(arc, tmp_path / "out", LIMITS)
    assert "data/huge.csv" in rep.oversized and _extracted(tmp_path / "out") == ["README.md"]


# --- nested archives are inert files -------------------------------------------------------------------------


def test_nested_archives_are_never_opened(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("../../escape.py", "boom")
        zf.writestr("payload/" + "d/" * 40 + "deep.txt", "x")
    inner_tgz = io.BytesIO()
    with tarfile.open(fileobj=inner_tgz, mode="w:gz") as tf:
        info = tarfile.TarInfo("../escape2.py")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"boom"))
    arc = _zip(
        tmp_path / "outer.zip",
        {
            "src/app.py": b"x = 1\n",
            "vendor/inner.zip": inner.getvalue(),
            "vendor/i.tar.gz": inner_tgz.getvalue(),
        },
    )
    rep = safe_extract(arc, tmp_path / "out", LIMITS)
    assert _extracted(tmp_path / "out") == ["src/app.py", "vendor/i.tar.gz", "vendor/inner.zip"]
    assert rep.file_count == 3
    assert not (tmp_path / "escape.py").exists() and not (tmp_path / "escape2.py").exists()


def test_a_zip_inside_a_tar_is_a_plain_file(tmp_path: Path) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("a.py", "x")
    members = [_file("r/inner.zip", inner.getvalue()), _file("r/main.py", b"x = 1\n")]
    safe_extract(_tar_gz(tmp_path / "z.tar.gz", list(members)), tmp_path / "out", LIMITS)  # type: ignore[arg-type]
    assert _extracted(tmp_path / "out") == ["inner.zip", "main.py"]


# --- paths -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "..\\evil.py",
        "a\\..\\..\\evil.py",
        "..%2f..%2fevil.py".replace("%2f", "/"),
        "C:\\Windows\\win.ini",
        "\\\\server\\share\\f.py",
        "//abs/path.py",
        "a/./../../evil.py",
        "x\x00.py",
    ],
)
def test_unsafe_member_names_are_rejected(name: str) -> None:
    with pytest.raises(SecurityRejection):
        normalize_member_path(name, 8)


def test_depth_limit_is_exact(tmp_path: Path) -> None:
    ok = "/".join(["d"] * (LIMITS.max_path_depth - 1)) + "/f.txt"  # exactly max_path_depth components
    safe_extract(_zip(tmp_path / "ok.zip", {ok: b"x", "other.txt": b"y"}), tmp_path / "ok", LIMITS)
    too_deep = "/".join(["d"] * LIMITS.max_path_depth) + "/f.txt"
    with pytest.raises(SecurityRejection):
        safe_extract(
            _zip(tmp_path / "deep.zip", {too_deep: b"x", "other.txt": b"y"}), tmp_path / "deep", LIMITS
        )


def test_a_file_written_through_a_skipped_symlink_name_stays_inside(tmp_path: Path) -> None:
    import stat

    arc = tmp_path / "through.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        link = zipfile.ZipInfo("repo/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, str(tmp_path / "outside"))
        zf.writestr("repo/link/evil.py", "boom")
        zf.writestr("repo/ok.py", "x = 1")
    (tmp_path / "outside").mkdir()
    safe_extract(arc, tmp_path / "out", LIMITS)
    assert (
        list((tmp_path / "outside").iterdir()) == []
    )  # the link was never created, so nothing went through it


# --- tar member types --------------------------------------------------------------------------------------------


def test_tar_hardlinks_devices_fifos_and_absolute_names(tmp_path: Path) -> None:
    hard = tarfile.TarInfo("r/hard")
    hard.type, hard.linkname = tarfile.LNKTYPE, "/etc/passwd"
    dev = tarfile.TarInfo("r/dev")
    dev.type, dev.devmajor, dev.devminor = tarfile.CHRTYPE, 1, 3
    fifo = tarfile.TarInfo("r/fifo")
    fifo.type = tarfile.FIFOTYPE
    members: list[tuple[tarfile.TarInfo, bytes | None]] = [
        (hard, None),
        (dev, None),
        (fifo, None),
        _file("r/ok.py", b"x"),
    ]
    rep = safe_extract(_tar_gz(tmp_path / "t.tar.gz", members), tmp_path / "out", LIMITS)
    assert _extracted(tmp_path / "out") == ["ok.py"]
    assert rep.skipped == {"hard": "hardlink", "dev": "special", "fifo": "special"}
    with pytest.raises(SecurityRejection):
        safe_extract(
            _tar_gz(tmp_path / "abs.tar.gz", [_file("/etc/cron.d/x", b"x")]), tmp_path / "o2", LIMITS
        )


def test_tar_declared_size_larger_than_limit_is_not_written(tmp_path: Path) -> None:
    big = _file("r/big.bin", b"\0" * (2 * 1024 * 1024))
    rep = safe_extract(
        _tar_gz(tmp_path / "b.tar.gz", [big, _file("r/ok.py", b"x")]), tmp_path / "out", LIMITS
    )
    assert "big.bin" in rep.oversized and _extracted(tmp_path / "out") == ["ok.py"]


def test_gzip_that_is_not_a_tar_is_a_typed_error(tmp_path: Path) -> None:
    import gzip

    p = tmp_path / "x.tar.gz"
    p.write_bytes(gzip.compress(b"not a tar archive" * 100))
    with pytest.raises(InputError):
        safe_extract(p, tmp_path / "out", LIMITS)


# --- URL ingestion --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com@evil.example/o/r",
        "https://github.com:443@evil.example/o/r",
        "https://github.com.evil.example/o/r",
        "https://evil.example/github.com/o/r",
        "http://github.com/o/r",
        "https://api.github.com/repos/o/r",
        "https://codeload.github.com/o/r/tar.gz/main",
        "https://github.com:8443/o/r",
        "https://github.com/o/../r",
        "https://github.com/o/r/tree/../../x",
        "https://github.com/o/r/blob/main/a.py",
        "file:///etc/passwd",
        "ssh://git@github.com/o/r",
        "https://gıthub.com/o/r",
    ],
)
def test_look_alike_and_non_repository_urls_are_refused(url: str) -> None:
    with pytest.raises(InputError):
        parse_github_url(url)


def _client(handler: httpx.MockTransport, **settings: object) -> GitHubClient:
    cfg = Settings(_env_file=None, **settings)  # type: ignore[arg-type]
    return GitHubClient(cfg, transport=handler)


async def test_a_redirect_to_another_host_is_refused(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "codeload.github.com":
            return httpx.Response(302, headers={"location": "https://evil.example/exfil.tar.gz"})
        return httpx.Response(200, content=b"never served")

    client = _client(httpx.MockTransport(handler))
    try:
        with pytest.raises(IngestionError):
            await client.download(GitHubRef("o", "r"), "a" * 40, tmp_path / "r.tar.gz")
    finally:
        await client.aclose()
    assert "evil.example" not in seen  # the request was never sent


async def test_an_endless_body_is_cut_at_the_size_limit(tmp_path: Path) -> None:
    sent = 0

    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            nonlocal sent
            while True:
                sent += 64 * 1024
                yield b"\0" * (64 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Endless())

    client = _client(httpx.MockTransport(handler), max_repo_archive_bytes=1024 * 1024)
    dest = tmp_path / "r.tar.gz"
    try:
        with pytest.raises(InputError) as ei:
            await client.download(GitHubRef("o", "r"), "a" * 40, dest)
    finally:
        await client.aclose()
    assert ei.value.code == ErrorCode.INPUT_TOO_LARGE
    assert sent <= 4 * 1024 * 1024  # stopped reading soon after the cap; no unbounded buffering
    assert dest.stat().st_size <= 1024 * 1024


async def test_a_hostile_tarball_from_the_download_is_rejected_and_leaves_no_source(tmp_path: Path) -> None:
    evil = io.BytesIO()
    with tarfile.open(fileobj=evil, mode="w:gz") as tf:
        for name, data in {"o-r-abc/ok.py": b"x = 1\n", "o-r-abc/../../outside.py": b"boom"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    sha = "a" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/commits/main"):
            return httpx.Response(200, json={"sha": sha})
        if path.startswith("/repos/"):
            return httpx.Response(
                200, json={"private": False, "visibility": "public", "size": 1, "default_branch": "main"}
            )
        return httpx.Response(200, content=evil.getvalue())

    import unfold.ingestion.service as service

    class Patched(GitHubClient):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings, transport=httpx.MockTransport(handler))

    original = service.GitHubClient
    service.GitHubClient = Patched  # type: ignore[misc]
    try:
        with pytest.raises(SecurityRejection):
            await acquire_github(Settings(_env_file=None), "https://github.com/o/r", tmp_path)
    finally:
        service.GitHubClient = original  # type: ignore[misc]
    assert not (tmp_path / "src").exists() and not (tmp_path.parent / "outside.py").exists()


async def test_a_private_or_oversized_repository_is_refused_before_download(tmp_path: Path) -> None:
    def private(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"private": True, "size": 1})

    def huge(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"private": False, "size": 10_000_000_000})

    for handler, code in ((private, ErrorCode.REPOSITORY_NOT_PUBLIC), (huge, ErrorCode.INPUT_TOO_LARGE)):
        client = _client(httpx.MockTransport(handler))
        try:
            with pytest.raises(InputError) as ei:
                await client.resolve(GitHubRef("o", "r"))
        finally:
            await client.aclose()
        assert ei.value.code == code
