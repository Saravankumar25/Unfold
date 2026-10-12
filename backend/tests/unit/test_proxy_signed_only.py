from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from unfold.api import proxy_auth

SECRET = b"k" * 32


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    proxy_auth.require_signed(app, SECRET)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/x")
    def x() -> dict[str, str]:
        return {"ok": "yes"}

    return TestClient(app)


def test_unsigned_and_forged_requests_are_refused_but_health_is_open(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/x").status_code == 403
    assert client.get("/api/v1/x", headers={"x-unfold-client": "v1.1.1.1.bad"}).status_code == 403
    forged = proxy_auth.sign(b"other" * 8, "203.0.113.1")
    assert client.get("/api/v1/x", headers={"x-unfold-client": forged}).status_code == 403


def test_a_signed_request_passes(client: TestClient) -> None:
    signed = proxy_auth.sign(SECRET, "203.0.113.1")
    assert client.get("/api/v1/x", headers={"x-unfold-client": signed}).status_code == 200
