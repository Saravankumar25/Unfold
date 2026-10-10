from __future__ import annotations

import io
import secrets
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from unfold.api.main import create_app
from unfold.core.config import Settings
from unfold.runtime import Runtime

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


# --- audit S-1 / S-2 / S-5 / S-7 -------------------------------------------------------------------


@asynccontextmanager
async def _app_client(settings: Settings, peer: str) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application, client=(peer, 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield application, c


def _peer() -> str:
    return f"198.51.{secrets.randbelow(250) + 1}.{secrets.randbelow(250) + 1}"


async def test_session_creation_is_limited_per_ip_and_forged_xff_does_not_help(settings: Settings) -> None:
    limited = settings.model_copy(update={"max_sessions_per_ip_per_hour": 2})
    async with _app_client(limited, _peer()) as (_, c):
        for i in range(2):
            ok = await c.post("/api/v1/sessions", headers={"X-Forwarded-For": f"10.0.0.{i}"})
            assert ok.status_code == 201
        blocked = await c.post("/api/v1/sessions", headers={"X-Forwarded-For": "10.0.9.9"})
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"


async def test_session_creation_has_a_global_ceiling(settings: Settings) -> None:
    limited = settings.model_copy(update={"max_sessions_per_hour": 1})
    async with _app_client(limited, _peer()) as (app, c):
        rt: Runtime = app.state.runtime
        await rt.limiter.delete_prefix("sessions:global")
        assert (await c.post("/api/v1/sessions")).status_code == 201
        assert (await c.post("/api/v1/sessions")).status_code == 429
        await rt.limiter.delete_prefix("sessions:global")


async def test_project_creation_is_limited_per_ip_across_sessions(settings: Settings) -> None:
    limited = settings.model_copy(update={"max_projects_per_ip_per_hour": 2})
    async with _app_client(limited, _peer()) as (_, c):
        statuses = []
        for _ in range(3):
            await _session(c)  # a fresh session each time: the per-session limit alone would allow all
            resp = await c.post(
                "/api/v1/projects/github", json={"url": "https://github.com/octocat/Hello-World"}
            )
            statuses.append(resp.status_code)
        assert statuses == [202, 202, 429]


async def test_header_is_not_a_session_credential(client: httpx.AsyncClient) -> None:
    token = await _session(client)
    bare = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    resp = await bare.get("/api/v1/sessions/current", headers={"X-Unfold-Session": token})
    await bare.aclose()
    assert resp.status_code == 401


async def test_stream_cap_rejects_extra_streams(app: FastAPI, client: httpx.AsyncClient) -> None:
    sid = (await client.post("/api/v1/sessions")).json()["session_id"]
    pid = (
        await client.post("/api/v1/projects/github", json={"url": "https://github.com/octocat/Hello-World"})
    ).json()["project_id"]
    caps = app.state.streams
    leases = []
    while (lease := caps.acquire(sid)) is not None:
        leases.append(lease)
    resp = await client.get(f"/api/v1/projects/{pid}/events")
    assert resp.status_code == 429
    # A malformed cursor must be rejected by the cap (429), not crash before it (500).
    bad_cursor = await client.get(f"/api/v1/projects/{pid}/events?last_event_id=%C2%B2")
    assert bad_cursor.status_code == 429
    for lease in leases:
        lease.release()


async def test_events_replay_from_last_event_id(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _session(client)
    pid = (
        await client.post("/api/v1/projects/github", json={"url": "https://github.com/octocat/Hello-World"})
    ).json()["project_id"]
    rt: Runtime = app.state.runtime
    first = await rt.progress.publish(pid, {"type": "stage", "stage": "INVENTORY"})
    await rt.progress.publish(pid, {"type": "ready", "stage": "READY"})
    resp = await client.get(f"/api/v1/projects/{pid}/events", headers={"Last-Event-ID": str(first)})
    assert resp.status_code == 200
    assert "event: ready" in resp.text
    assert "INVENTORY" not in resp.text
