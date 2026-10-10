"""Stale job workdirs are swept from `settings.work_dir` as well as the system temp dir (audit P2-e)."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from unfold.worker.run import sweep_stale_workdirs


def _make(root: Path, name: str, age_s: float) -> Path:
    path = root / name
    path.mkdir()
    (path / "repo.txt").write_text("untrusted")
    old = time.time() - age_s
    os.utime(path, (old, old))
    return path


def test_sweeps_configured_work_dir_and_keeps_fresh_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system_tmp, work = tmp_path / "tmp", tmp_path / "work"
    system_tmp.mkdir()
    work.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_tmp))
    stale_tmp = _make(system_tmp, "unfold-job-aaa", 7200)
    stale_work = _make(work, "unfold-job-bbb", 7200)
    fresh_work = _make(work, "unfold-job-ccc", 10)
    other = _make(work, "not-ours", 7200)

    assert sweep_stale_workdirs(work_dir=str(work)) == 2
    assert not stale_tmp.exists() and not stale_work.exists()
    assert fresh_work.exists() and other.exists()


def test_work_dir_is_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _make(tmp_path, "unfold-job-zzz", 7200)
    assert sweep_stale_workdirs() == 1
