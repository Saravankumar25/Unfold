"""AWS profile building blocks that need no store: client identity (HMAC), production guard, import weight."""

from __future__ import annotations

import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from unfold.api import proxy_auth
from unfold.api.deps import client_ip
from unfold.core.config import Settings

SECRET = b"s3cret-shared-with-the-proxy"


# --- X-Unfold-Client ----------------------------------------------------------------------------------------------


def test_signed_address_verifies() -> None:
    assert proxy_auth.verified_ip(proxy_auth.sign(SECRET, "203.0.113.7"), SECRET) == "203.0.113.7"
    assert proxy_auth.verified_ip(proxy_auth.sign(SECRET, "2001:db8::1"), SECRET) == "2001:db8::1"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.replace("203.0.113.7", "198.51.100.9"),  # the address was swapped
        lambda v: v[:-1] + ("0" if v[-1] != "0" else "1"),  # the MAC was changed
        lambda v: "v2" + v[2:],  # unknown version
        lambda v: v.rsplit(".", 1)[0],  # no MAC
        lambda v: "",
        lambda v: "x" * 500,
    ],
)
def test_tampered_or_malformed_header_is_rejected(mutate: Any) -> None:
    good = proxy_auth.sign(SECRET, "203.0.113.7")
    assert proxy_auth.verified_ip(mutate(good), SECRET) is None


def test_wrong_secret_stale_and_future_timestamps_are_rejected() -> None:
    now = time.time()
    assert proxy_auth.verified_ip(proxy_auth.sign(b"other", "1.2.3.4"), SECRET) is None
    assert proxy_auth.verified_ip(proxy_auth.sign(SECRET, "1.2.3.4", now=now - 120), SECRET, now=now) is None
    assert proxy_auth.verified_ip(proxy_auth.sign(SECRET, "1.2.3.4", now=now + 120), SECRET, now=now) is None
    assert (
        proxy_auth.verified_ip(proxy_auth.sign(SECRET, "1.2.3.4", now=now - 30), SECRET, now=now) == "1.2.3.4"
    )


def _request(headers: dict[str, str], *, secret: bytes | None, peer: str = "10.0.0.1") -> Request:
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            proxy_hmac_secret=None
            if secret is None
            else SimpleNamespace(get_secret_value=lambda: secret.decode())
        )
    )
    app = SimpleNamespace(state=SimpleNamespace(runtime=runtime))
    scope = {
        "type": "http",
        "app": app,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
    }
    return Request(scope)


def test_client_ip_uses_only_the_signed_address_behind_the_proxy() -> None:
    signed = proxy_auth.sign(SECRET, "203.0.113.7")
    assert client_ip(_request({"x-unfold-client": signed}, secret=SECRET)) == "203.0.113.7"
    # a caller that bypassed the proxy shares one bucket; a forged forwarded-for header changes nothing
    forged = {"x-forwarded-for": "198.51.100.9", "x-unfold-client": "v1.1.198.51.100.9." + "0" * 64}
    assert client_ip(_request(forged, secret=SECRET)) == proxy_auth.UNVERIFIED
    assert client_ip(_request({}, secret=SECRET)) == proxy_auth.UNVERIFIED


def test_client_ip_without_a_secret_is_the_peer_address() -> None:
    assert client_ip(_request({"x-unfold-client": "ignored"}, secret=None, peer="192.0.2.5")) == "192.0.2.5"


# --- production guard ---------------------------------------------------------------------------------------------


def _aws(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "production",
        "cookie_secure": True,
        "persistence_backend": "dynamodb",
        "graph_backend": "memory",
        "dense_backend": "artifact",
        "dynamodb_table": "unfold-prod",
        "jobs_queue_url": "https://sqs.us-east-1.amazonaws.com/1/unfold-prod-jobs",
        "object_store_bucket": "unfold-prod-1",
        "proxy_hmac_secret": "x" * 32,
    }
    return Settings(_env_file=None, **{**base, **over})  # type: ignore[call-arg]


def test_aws_profile_needs_none_of_the_local_store_credentials() -> None:
    assert (
        _aws().is_production
    )  # default Neo4j/MinIO/Postgres passwords and TRUSTED_PROXY_IPS are irrelevant here


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"proxy_hmac_secret": None}, "PROXY_HMAC_SECRET"),
        ({"dynamodb_table": ""}, "DYNAMODB_TABLE"),
        ({"jobs_queue_url": ""}, "JOBS_QUEUE_URL"),
        ({"aws_endpoint_url": "http://localhost:4566"}, "AWS_ENDPOINT_URL"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
    ],
)
def test_aws_profile_refuses_unsafe_production_config(override: dict[str, Any], needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        _aws(**override)


# --- import weight ------------------------------------------------------------------------------------------------


def test_api_import_loads_no_heavy_or_local_only_stack() -> None:
    """The API Lambda must not pay for libraries it never uses (cold start): no analysis, ML or local-store code."""
    code = (
        "import sys, unfold.api.main\n"
        "heavy = ['fastembed','onnxruntime','networkx','tree_sitter','qdrant_client','neo4j','sqlalchemy',"
        "'asyncpg','redis','minio','playwright','unfold.orchestration.pipeline','unfold.worker.queue']\n"
        "print(','.join(m for m in heavy if m in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
