"""Hardened archive extraction for zip and tar.gz (LLD §6, PRD §19).

Every member is validated *before* anything is written; sizes are enforced on bytes actually read,
never on header-declared sizes. Nothing here executes or interprets repository content.
"""

from __future__ import annotations

import io
import os
import posixpath
import shutil
import stat
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from unfold.core.errors import ErrorCode, InputError, SecurityRejection

ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
GZIP_MAGIC = b"\x1f\x8b"
_CHUNK = 64 * 1024


@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int
    max_file_count: int
    max_single_file_bytes: int
    max_path_depth: int
    max_compression_ratio: int

    @classmethod
    def from_settings(cls, s: object) -> ExtractionLimits:
        return cls(
            max_extracted_bytes=s.max_extracted_bytes,  # type: ignore[attr-defined]
            max_file_count=s.max_file_count,  # type: ignore[attr-defined]
            max_single_file_bytes=s.max_single_file_bytes,  # type: ignore[attr-defined]
            max_path_depth=s.max_path_depth,  # type: ignore[attr-defined]
            max_compression_ratio=s.max_compression_ratio,  # type: ignore[attr-defined]
        )


@dataclass
class ExtractionReport:
    root: Path
    file_count: int = 0
    total_bytes: int = 0
    oversized: dict[str, int] = field(default_factory=dict)  # path -> size (not written)
    skipped: dict[str, str] = field(default_factory=dict)  # path -> reason (symlink, device, …)


@dataclass(frozen=True)
class _Member:
    name: str
    is_dir: bool
    size: int
    compressed: int
    kind: str  # file | dir | symlink | hardlink | special


def detect_format(path: Path) -> str:
    with path.open("rb") as fh:
        head = fh.read(4)
    if head.startswith(ZIP_MAGIC[0]) or head.startswith(ZIP_MAGIC[1]):
        return "zip"
    if head.startswith(GZIP_MAGIC):
        return "tar.gz"
    raise InputError("The upload is not a ZIP archive.", code=ErrorCode.ZIP_INVALID)


def normalize_member_path(name: str, max_depth: int) -> str | None:
    """Return a safe relative POSIX path, None for entries to ignore (e.g. '.'), or raise on traversal."""
    if "\x00" in name:
        raise SecurityRejection("Archive entry contains a NUL byte.", code=ErrorCode.UNSAFE_ARCHIVE)
    raw = name.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":") or raw.startswith("//"):
        raise SecurityRejection(f"Absolute path in archive: {name!r}", code=ErrorCode.UNSAFE_ARCHIVE)
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise SecurityRejection(f"Path traversal in archive: {name!r}", code=ErrorCode.UNSAFE_ARCHIVE)
    if not parts:
        return None
    if len(parts) > max_depth:
        raise SecurityRejection(f"Archive path too deep: {name!r}", code=ErrorCode.UNSAFE_ARCHIVE)
    norm = posixpath.normpath("/".join(parts))
    if norm.startswith("..") or norm.startswith("/"):
        raise SecurityRejection(f"Path traversal in archive: {name!r}", code=ErrorCode.UNSAFE_ARCHIVE)
    return norm


def _zip_members(zf: zipfile.ZipFile) -> Iterator[tuple[_Member, zipfile.ZipInfo]]:
    for info in zf.infolist():
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)  # 0 when the archiver stored permission bits only (e.g. Windows tools)
        if stat.S_ISLNK(mode):
            kind = "symlink"
        elif info.is_dir():
            kind = "dir"
        elif file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            kind = "special"
        else:
            kind = "file"
        yield _Member(info.filename, info.is_dir(), info.file_size, info.compress_size, kind), info


def _tar_kind(ti: tarfile.TarInfo) -> str:
    if ti.issym():
        return "symlink"
    if ti.islnk():
        return "hardlink"
    if ti.isdir():
        return "dir"
    if ti.isreg():
        return "file"
    return "special"


def _common_root(names: list[str]) -> str | None:
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and any("/" in n for n in names):
        return tops.pop()
    return None


class _Budget:
    def __init__(self, limits: ExtractionLimits, archive_size: int) -> None:
        self.limits = limits
        self.archive_size = max(archive_size, 1)
        self.total = 0
        self.files = 0

    def add_file(self) -> None:
        self.files += 1
        if self.files > self.limits.max_file_count:
            raise SecurityRejection(
                f"Archive has more than {self.limits.max_file_count} files.", code=ErrorCode.INPUT_TOO_LARGE
            )

    def add_bytes(self, n: int) -> None:
        self.total += n
        if self.total > self.limits.max_extracted_bytes:
            raise SecurityRejection(
                "Extracted size exceeds the configured limit.", code=ErrorCode.INPUT_TOO_LARGE
            )
        if (
            self.total > 10 * 1024 * 1024
            and self.total / self.archive_size > self.limits.max_compression_ratio
        ):
            raise SecurityRejection(
                "Archive compression ratio indicates a zip bomb.", code=ErrorCode.ZIP_BOMB_DETECTED
            )


def _copy_limited(src: IO[bytes], dst: Path, declared: int, budget: _Budget, ratio_base: int) -> int:
    """Stream bytes, enforcing declared size, global budget and per-entry ratio on bytes actually read."""
    written = 0
    with dst.open("wb") as out:
        while True:
            buf = src.read(_CHUNK)
            if not buf:
                break
            written += len(buf)
            if declared >= 0 and written > declared:
                raise SecurityRejection(
                    "Archive entry is larger than declared (zip bomb).", code=ErrorCode.ZIP_BOMB_DETECTED
                )
            if written > 1024 * 1024 and written / max(ratio_base, 1) > budget.limits.max_compression_ratio:
                raise SecurityRejection(
                    "Archive entry compression ratio indicates a zip bomb.", code=ErrorCode.ZIP_BOMB_DETECTED
                )
            budget.add_bytes(len(buf))
            out.write(buf)
    return written


def safe_extract(archive: Path, dest: Path, limits: ExtractionLimits) -> ExtractionReport:
    """Extract `archive` into `dest` (must be empty/new). Raises SecurityRejection/InputError on violations."""
    dest.mkdir(parents=True, exist_ok=True)
    fmt = detect_format(archive)
    report = ExtractionReport(root=dest)
    budget = _Budget(limits, archive.stat().st_size)
    try:
        if fmt == "zip":
            _extract_zip(archive, dest, limits, budget, report)
        else:
            _extract_tar(archive, dest, limits, budget, report)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise InputError(
            f"The archive is corrupt or unreadable: {type(exc).__name__}", code=ErrorCode.ZIP_INVALID
        ) from exc
    except (SecurityRejection, InputError):
        shutil.rmtree(dest, ignore_errors=True)
        raise
    report.file_count = budget.files
    report.total_bytes = budget.total
    if report.file_count == 0:
        raise InputError("The archive contains no files.", code=ErrorCode.EMPTY_REPOSITORY)
    return report


def _target(dest: Path, rel: str) -> Path:
    target = (dest / rel).resolve()
    root = dest.resolve()
    if os.path.commonpath([root, target]) != str(root):
        raise SecurityRejection(f"Path escapes extraction root: {rel!r}", code=ErrorCode.UNSAFE_ARCHIVE)
    return target


def _extract_zip(
    archive: Path, dest: Path, limits: ExtractionLimits, budget: _Budget, report: ExtractionReport
) -> None:
    with zipfile.ZipFile(archive) as zf:
        members = list(_zip_members(zf))
        names = []
        for m, _ in members:
            norm = normalize_member_path(m.name, limits.max_path_depth + 1)
            if norm:
                names.append(norm)
        strip = _common_root(names)
        declared_total = sum(m.size for m, _ in members)
        if (
            declared_total > 10 * 1024 * 1024
            and declared_total / budget.archive_size > limits.max_compression_ratio
        ):
            raise SecurityRejection(
                "Archive compression ratio indicates a zip bomb.", code=ErrorCode.ZIP_BOMB_DETECTED
            )
        for m, info in members:
            rel = _strip(normalize_member_path(m.name, limits.max_path_depth + 1), strip)
            if rel is None:
                continue
            if len(rel.split("/")) > limits.max_path_depth:
                raise SecurityRejection(f"Archive path too deep: {m.name!r}", code=ErrorCode.UNSAFE_ARCHIVE)
            if m.kind in ("symlink", "special"):
                report.skipped[rel] = m.kind
                continue
            target = _target(dest, rel)
            if m.kind == "dir":
                target.mkdir(parents=True, exist_ok=True)
                continue
            budget.add_file()
            if m.size > limits.max_single_file_bytes:
                report.oversized[rel] = m.size
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src:
                _copy_limited(src, target, m.size, budget, max(m.compressed, 1))


def _extract_tar(
    archive: Path, dest: Path, limits: ExtractionLimits, budget: _Budget, report: ExtractionReport
) -> None:
    # Two passes over the stream: listing (headers only) then extraction; `r|gz` never seeks backwards.
    with tarfile.open(archive, "r:gz") as tf:
        infos = []
        for ti in tf:
            infos.append((ti.name, _tar_kind(ti)))
            if len(infos) > limits.max_file_count * 2:
                raise SecurityRejection("Archive has too many entries.", code=ErrorCode.INPUT_TOO_LARGE)
    names = [n for n in (normalize_member_path(n, limits.max_path_depth + 1) for n, _ in infos) if n]
    strip = _common_root(names)
    with tarfile.open(archive, "r:gz") as tf:
        for ti in tf:
            rel = _strip(normalize_member_path(ti.name, limits.max_path_depth + 1), strip)
            if rel is None:
                continue
            if len(rel.split("/")) > limits.max_path_depth:
                raise SecurityRejection(f"Archive path too deep: {ti.name!r}", code=ErrorCode.UNSAFE_ARCHIVE)
            kind = _tar_kind(ti)
            if kind in ("symlink", "hardlink", "special"):
                report.skipped[rel] = kind
                continue
            target = _target(dest, rel)
            if kind == "dir":
                target.mkdir(parents=True, exist_ok=True)
                continue
            budget.add_file()
            if ti.size > limits.max_single_file_bytes:
                report.oversized[rel] = ti.size
                continue
            src = tf.extractfile(ti)
            if src is None:
                report.skipped[rel] = "unreadable"
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with src:
                # Tar members are uncompressed inside the gzip stream; the global ratio check in _Budget applies.
                _copy_limited(src, target, ti.size, budget, max(ti.size, 1))


def _strip(rel: str | None, root: str | None) -> str | None:
    if rel is None:
        return None
    if root is None:
        return rel
    if rel == root:
        return None
    return rel[len(root) + 1 :] if rel.startswith(root + "/") else rel


def zip_bytes_from_dir(root: Path) -> bytes:
    """Test/eval helper: zip a directory in memory (deterministic order)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
    return buf.getvalue()
