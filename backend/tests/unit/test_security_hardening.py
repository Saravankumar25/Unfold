"""Audit S-2, S-3, S-5, S-7 — behaviour that needs no data store."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.requests import Request as StarletteRequest

from unfold.api.deps import client_ip, session_token_from
from unfold.api.main import install_proxy_trust
from unfold.api.streams import StreamCap
from unfold.core.config import Settings

SAFE_PROD = {
    "app_env": "production",
    "cookie_secure": True,
    "neo4j_password": "n3o-prod-secret",
    "object_store_secret_key": "obj-prod-secret",
    "database_url": "postgresql+asyncpg://app:s3cret@db.internal:5432/unfold",
}


def _settings(**over: object) -> Settings:
    return Settings(_env_file=None, **{**SAFE_PROD, **over})  # type: ignore[arg-type]


def test_production_accepts_safe_config() -> None:
    assert _settings().is_production


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"neo4j_password": "unfold-neo4j-pass"}, "NEO4J_PASSWORD"),
        ({"object_store_secret_key": "unfold-minio-secret"}, "OBJECT_STORE_SECRET_KEY"),
        ({"database_url": "postgresql+asyncpg://unfold:unfold@localhost/unfold"}, "DATABASE_URL"),
        ({"trusted_proxy_ips": "*"}, "TRUSTED_PROXY_IPS"),
    ],
)
def test_production_refuses_unsafe_defaults(override: dict[str, object], needle: str) -> None:
    with pytest.raises(ValidationError, match=needle):
        _settings(**override)


def test_development_keeps_defaults() -> None:
    assert not Settings(_env_file=None, app_env="development").cookie_secure


def _ip_app(trusted: str) -> FastAPI:
    app = FastAPI()

    @app.get("/ip")
    async def ip(request: Request) -> dict[str, str]:
        return {"ip": client_ip(request)}

    install_proxy_trust(app, Settings(_env_file=None, trusted_proxy_ips=trusted))
    return app


async def _get_ip(app: FastAPI, peer: str, xff: str) -> str:
    transport = httpx.ASGITransport(app=app, client=(peer, 4242))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return str((await c.get("/ip", headers={"X-Forwarded-For": xff})).json()["ip"])


async def test_forged_forwarded_for_from_untrusted_peer_is_ignored() -> None:
    assert await _get_ip(_ip_app("10.0.0.1"), "198.51.100.7", "1.2.3.4") == "198.51.100.7"


async def test_forwarded_for_honoured_only_from_trusted_proxy() -> None:
    assert await _get_ip(_ip_app("10.0.0.1"), "10.0.0.1", "203.0.113.9") == "203.0.113.9"


def test_session_token_comes_from_cookie_only() -> None:
    scope = {
        "type": "http",
        "headers": [(b"x-unfold-session", b"from-header"), (b"cookie", b"unfold_session=from-cookie")],
    }
    assert session_token_from(StarletteRequest(scope), "unfold_session") == "from-cookie"
    header_only = {"type": "http", "headers": [(b"x-unfold-session", b"from-header")]}
    assert session_token_from(StarletteRequest(header_only), "unfold_session") is None


def test_stream_cap_blocks_n_plus_one_and_frees_on_release() -> None:
    cap = StreamCap(2)
    assert cap.acquire("s") and cap.acquire("s")
    assert not cap.acquire("s")
    assert cap.acquire("other")
    cap.release("s")
    assert cap.acquire("s")
