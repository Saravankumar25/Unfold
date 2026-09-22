from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from unfold.api.main import create_app
from unfold.core.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _session(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/v1/sessions")
    assert resp.status_code == 201
    token = resp.cookies.get("unfold_session")
    assert token
    return token


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo/app.py", "print('hi')\n")
    return buf.getvalue()


async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json()["status"] == "ok"
    ready = await client.get("/ready")
    assert ready.status_code == 200, ready.text
    assert set(ready.json()["checks"].values()) == {"ok"}


async def test_session_required_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/sessions/current")
    assert resp.status_code == 401
    body = resp.json()["error"]
    assert body["code"] == "SESSION_REQUIRED"
    assert body["request_id"]
    assert resp.headers["X-Request-ID"] == body["request_id"]


async def test_github_project_lifecycle_and_ownership(client: httpx.AsyncClient) -> None:
    await _session(client)
    bad = await client.post("/api/v1/projects/github", json={"url": "https://gitlab.com/a/b"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_GITHUB_URL"

    created = await client.post(
        "/api/v1/projects/github",
        json={
            "url": "https://github.com/octocat/Hello-World",
            "technical_level": "beginner",
            "instruction": "viva prep",
        },
    )
    assert created.status_code == 202, created.text
    pid = created.json()["project_id"]

    project = (await client.get(f"/api/v1/projects/{pid}")).json()
    assert project["name"] == "Hello-World"
    assert project["job"]["current_stage"] == "RECEIVED"
    assert project["job"]["stage_total"] == 19

    current = (await client.get("/api/v1/sessions/current")).json()
    assert [p["id"] for p in current["projects"]] == [pid]

    # A different session cannot see the project (404, not 403).
    other = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    await _session(other)
    assert (await other.get(f"/api/v1/projects/{pid}")).status_code == 404
    await other.aclose()


async def test_zip_upload_validation(client: httpx.AsyncClient) -> None:
    await _session(client)
    not_zip = await client.post(
        "/api/v1/projects/zip", files={"file": ("x.zip", b"hello world", "application/zip")}
    )
    assert not_zip.status_code == 400
    assert not_zip.json()["error"]["code"] == "ZIP_INVALID"

    ok = await client.post(
        "/api/v1/projects/zip",
        files={"file": ("demo.zip", _zip_bytes(), "application/zip")},
        data={"technical_level": "experienced"},
    )
    assert ok.status_code == 202, ok.text
    project = (await client.get(f"/api/v1/projects/{ok.json()['project_id']}")).json()
    assert project["name"] == "demo"
    assert project["technical_level"] == "experienced"


async def test_touch_extends_within_cap(client: httpx.AsyncClient) -> None:
    await _session(client)
    before = (await client.get("/api/v1/sessions/current")).json()["expires_at"]
    after = (await client.post("/api/v1/sessions/current/touch")).json()["expires_at"]
    assert after >= before
