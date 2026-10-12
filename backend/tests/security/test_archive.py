from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from unfold.core.errors import ErrorCode, InputError, SecurityRejection
from unfold.ingestion.archive import ExtractionLimits, normalize_member_path, safe_extract

LIMITS = ExtractionLimits(
    max_extracted_bytes=5 * 1024 * 1024,
    max_file_count=50,
    max_single_file_bytes=1024 * 1024,
    max_path_depth=8,
    max_compression_ratio=100,
)


def _zip(tmp_path: Path, entries: dict[str, bytes], name: str = "a.zip") -> Path:
    p = tmp_path / name
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, data in entries.items():
            zf.writestr(n, data)
    return p


def test_extracts_and_strips_single_root(tmp_path: Path) -> None:
    arc = _zip(tmp_path, {"proj/app.py": b"print(1)\n", "proj/pkg/mod.py": b"x = 1\n"})
    rep = safe_extract(arc, tmp_path / "out", LIMITS)
    assert (tmp_path / "out" / "app.py").read_text() == "print(1)\n"
    assert (tmp_path / "out" / "pkg" / "mod.py").exists()
    assert rep.file_count == 2


@pytest.mark.parametrize("evil", ["../evil.py", "a/../../evil.py", "/etc/passwd", "C:/win.ini", "a/\x00b"])
def test_rejects_traversal_and_absolute(tmp_path: Path, evil: str) -> None:
    with pytest.raises(SecurityRejection):
        normalize_member_path(evil, 8)


def test_traversal_zip_rejected_and_nothing_left(tmp_path: Path) -> None:
    p = tmp_path / "t.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("ok.py", "x=1")
        info = zipfile.ZipInfo("../../escape.py")
        zf.writestr(info, "boom")
    with pytest.raises(SecurityRejection):
        safe_extract(p, tmp_path / "out", LIMITS)
    assert not (tmp_path / "escape.py").exists()
    assert not (tmp_path / "out").exists()


def test_symlink_entries_skipped(tmp_path: Path) -> None:
    p = tmp_path / "s.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("real.txt", "hello")
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    rep = safe_extract(p, tmp_path / "out", LIMITS)
    assert rep.skipped == {"link": "symlink"}
    assert not (tmp_path / "out" / "link").exists()


def test_zip_bomb_ratio_rejected(tmp_path: Path) -> None:
    big = b"\0" * (4 * 1024 * 1024)
    arc = _zip(tmp_path, {"a.bin": big, "b.bin": big, "c.bin": big})
    limits = ExtractionLimits(200 * 1024 * 1024, 50, 100 * 1024 * 1024, 8, 100)
    with pytest.raises(SecurityRejection) as ei:
        safe_extract(arc, tmp_path / "out", limits)
    assert ei.value.code in (ErrorCode.ZIP_BOMB_DETECTED, ErrorCode.INPUT_TOO_LARGE)


def test_total_size_limit(tmp_path: Path) -> None:
    import os

    arc = _zip(tmp_path, {f"f{i}.bin": os.urandom(900 * 1024) for i in range(7)})
    with pytest.raises(SecurityRejection) as ei:
        safe_extract(arc, tmp_path / "out", LIMITS)
    assert ei.value.code == ErrorCode.INPUT_TOO_LARGE


def test_file_count_limit(tmp_path: Path) -> None:
    arc = _zip(tmp_path, {f"f{i}.txt": b"x" for i in range(60)})
    with pytest.raises(SecurityRejection):
        safe_extract(arc, tmp_path / "out", LIMITS)


def test_depth_limit(tmp_path: Path) -> None:
    arc = _zip(tmp_path, {"/".join(["d"] * 12) + "/f.txt": b"x"})
    with pytest.raises(SecurityRejection):
        safe_extract(arc, tmp_path / "out", LIMITS)


def test_oversized_file_recorded_not_written(tmp_path: Path) -> None:
    import os

    limits = ExtractionLimits(50 * 1024 * 1024, 50, 1024, 8, 100)
    arc = _zip(tmp_path, {"small.txt": b"hi", "big.bin": os.urandom(4096)})
    rep = safe_extract(arc, tmp_path / "out", limits)
    assert rep.oversized == {"big.bin": 4096}
    assert not (tmp_path / "out" / "big.bin").exists()


def test_not_a_zip(tmp_path: Path) -> None:
    p = tmp_path / "x.zip"
    p.write_bytes(b"MZ\x90\x00 not a zip")
    with pytest.raises(InputError) as ei:
        safe_extract(p, tmp_path / "out", LIMITS)
    assert ei.value.code == ErrorCode.ZIP_INVALID


def test_empty_zip(tmp_path: Path) -> None:
    arc = _zip(tmp_path, {})
    with pytest.raises(InputError) as ei:
        safe_extract(arc, tmp_path / "out", LIMITS)
    assert ei.value.code == ErrorCode.EMPTY_REPOSITORY


def _tar(tmp_path: Path, build) -> Path:  # type: ignore[no-untyped-def]
    p = tmp_path / "r.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        build(tf)
    return p


def _add(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))


def test_tar_github_style(tmp_path: Path) -> None:
    def build(tf: tarfile.TarFile) -> None:
        _add(tf, "owner-repo-abc/README.md", b"# hi")
        _add(tf, "owner-repo-abc/src/main.py", b"x=1")
        link = tarfile.TarInfo("owner-repo-abc/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../etc/passwd"
        tf.addfile(link)

    rep = safe_extract(_tar(tmp_path, build), tmp_path / "out", LIMITS)
    assert (tmp_path / "out" / "src" / "main.py").exists()
    assert rep.skipped == {"link": "symlink"}


def test_tar_traversal_rejected(tmp_path: Path) -> None:
    def build(tf: tarfile.TarFile) -> None:
        _add(tf, "../outside.txt", b"x")

    with pytest.raises(SecurityRejection):
        safe_extract(_tar(tmp_path, build), tmp_path / "out", LIMITS)
    assert not (tmp_path / "outside.txt").exists()


def test_a_swapped_upload_is_refused_when_only_zip_is_allowed(tmp_path: Path) -> None:
    """A presigned POST stays valid after the ZIP check: the worker must not accept what replaced the file."""
    p = tmp_path / "swapped.zip"
    with tarfile.open(p, "w:gz") as tf:
        info = tarfile.TarInfo("a.py")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"x\n"))
    with pytest.raises(InputError) as exc:
        safe_extract(p, tmp_path / "out", LIMITS, only="zip")
    assert exc.value.code == ErrorCode.ZIP_INVALID


def _big_tar(tmp_path: Path, members: int, size: int) -> Path:
    p = tmp_path / "big.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for i in range(members):
            info = tarfile.TarInfo(f"f{i}.bin")
            info.size = size
            tf.addfile(info, io.BytesIO(b"\0" * size))
    return p


def test_a_tar_whose_skipped_members_decompress_far_beyond_the_limit_is_refused(tmp_path: Path) -> None:
    """Oversized members are skipped, not written, so only the stream total can stop the decompression."""
    limits = ExtractionLimits(
        max_extracted_bytes=64 * 1024,
        max_file_count=50,
        max_single_file_bytes=1024,
        max_path_depth=8,
        max_compression_ratio=10_000,
    )
    with pytest.raises(SecurityRejection) as exc:
        safe_extract(_big_tar(tmp_path, members=8, size=100 * 1024), tmp_path / "out", limits)
    assert exc.value.code == ErrorCode.ZIP_BOMB_DETECTED
