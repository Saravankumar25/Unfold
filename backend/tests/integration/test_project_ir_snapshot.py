from __future__ import annotations

import gzip

import pytest

from unfold.core.errors import NotFound, UnfoldError
from unfold.core.ids import entity_id
from unfold.domain.models import Provenance
from unfold.persistence.object_store import project_prefix
from unfold.project_ir.schema import FileIR, ProjectIdentity, ProjectIR, RelationshipIR, SymbolIR
from unfold.project_ir.snapshot import IRCache, encode_ir, evict_project, ir_object_key, load_ir, save_ir
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration


def _ir(project_id: str) -> ProjectIR:
    p = Provenance(analyzer="test")
    fid = entity_id(project_id, "file", "app/main.py")
    sym = SymbolIR(
        id=entity_id(project_id, "function", "app/main.py", "main"),
        name="main",
        kind="function",
        qualname="main",
        file_id=fid,
        path="app/main.py",
        start_line=1,
        end_line=3,
        provenance=p,
    )
    return ProjectIR(
        analysis_version="v1",
        project=ProjectIdentity(project_id=project_id, name="demo", input_type="zip"),
        files=[FileIR(id=fid, name="main.py", path="app/main.py", language="python", lines=3, provenance=p)],
        symbols=[sym],
        relationships=[RelationshipIR(source_id=fid, target_id=sym.id, type="CONTAINS", provenance=p)],
        stats={"files_total": 1},
    )


async def test_save_load_roundtrip(runtime: Runtime, project_id: str) -> None:
    ir = _ir(project_id)
    cache = IRCache(max_entries=2)
    try:
        key = await save_ir(runtime.objects, ir)
        assert key == ir_object_key(project_id, "v1") == f"projects/{project_id}/ir/v1.json.gz"
        stored = await runtime.objects.get_bytes(key)
        assert stored == encode_ir(ir)  # canonical, byte-identical
        assert gzip.decompress(stored).startswith(b"{")

        cold = await load_ir(runtime.objects, key, cache=IRCache(max_entries=0))
        assert cold == ir
        cached = await load_ir(runtime.objects, key, cache=cache)
        assert cached is await load_ir(runtime.objects, key, cache=cache)  # LRU hit
        assert len(cache) == 1
        evict_project(runtime.objects, project_id, cache=cache)
        assert len(cache) == 0

        with pytest.raises(NotFound):
            await load_ir(runtime.objects, ir_object_key(project_id, "missing"), cache=cache)

        bad_key = ir_object_key(project_id, "corrupt")
        await runtime.objects.put_bytes(bad_key, b"not gzip")
        with pytest.raises(UnfoldError):
            await load_ir(runtime.objects, bad_key, cache=cache)
    finally:
        await runtime.objects.delete_prefix(project_prefix(project_id))


def test_key_validation() -> None:
    with pytest.raises(UnfoldError):
        ir_object_key("a/b", "v1")
    with pytest.raises(UnfoldError):
        ir_object_key("p", "../v1")
