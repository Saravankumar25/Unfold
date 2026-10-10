"""Every request the frontend client can make names a route the API really serves (audit F-2)."""

from __future__ import annotations

import re
from pathlib import Path

from unfold.api.deps import API_PREFIX
from unfold.api.main import create_app
from unfold.core.config import Settings

ENDPOINTS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "api" / "endpoints.ts"
_CALL = re.compile(r"client\.(get|post|delete)<[^>]*>\(\s*([`\"])(.+?)\2", re.S)
_REQUEST = re.compile(r"client\.request<[^>]*>\(\s*\"(GET|POST|DELETE)\",\s*([`\"])(.+?)\2", re.S)


def _shape(path: str) -> str:
    path = path.split("?", 1)[0]
    return re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "{}", path)


def _client_routes() -> set[tuple[str, str]]:
    source = ENDPOINTS.read_text(encoding="utf-8")
    found = {(m.group(1).upper(), _shape(m.group(3))) for m in _CALL.finditer(source)}
    found |= {(m.group(1), _shape(m.group(3))) for m in _REQUEST.finditer(source)}
    return found


def _api_routes() -> set[tuple[str, str]]:
    app = create_app(Settings(app_env="test"))
    out: set[tuple[str, str]] = set()
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            if path.startswith(API_PREFIX):
                out.add((method.upper(), _shape(path.removeprefix(API_PREFIX))))
    return out


def test_client_paths_are_served_by_the_api() -> None:
    client, api = _client_routes(), _api_routes()
    assert len(client) >= 15, "the client route extractor found too few calls"
    assert ("POST", "/chats/{}/messages") in client and ("GET", "/projects/{}/progress") in client
    missing = sorted(client - api)
    assert missing == [], f"the frontend calls routes the API does not serve: {missing}"
