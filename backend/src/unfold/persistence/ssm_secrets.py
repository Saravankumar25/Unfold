"""Secrets from SSM Parameter Store into the process environment, once, before `Settings` is built (ADR 0015).

The API Lambda has no secret in its configuration, only the parameter path. At cold start one paginated
`GetParametersByPath` reads the SecureStrings under it and exports the known names as environment variables.
Anything not on the allow-list is ignored, so a stray parameter can never become a setting. An explicit
environment variable wins, which keeps local runs and tests in control.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from unfold.core.errors import StoreUnavailable

ALLOWED = frozenset(
    {f"gemini_api_key_{i}" for i in range(1, 5)}
    | {f"groq_api_key_{i}" for i in range(1, 5)}
    | {"github_token", "proxy_hmac_secret"}
)


def load_ssm_env(
    prefix: str,
    *,
    environ: MutableMapping[str, str] | None = None,
    client: Any | None = None,
) -> list[str]:
    """Export parameters under `prefix` (e.g. `/unfold/dev/`); returns the environment names it set."""
    env = os.environ if environ is None else environ
    if not prefix:
        return []
    ssm = client or boto3.client("ssm", config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))
    path = prefix.rstrip("/")
    exported: list[str] = []
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, Any] = {"Path": path, "WithDecryption": True, "Recursive": False}
            if token:
                kwargs["NextToken"] = token
            page = ssm.get_parameters_by_path(**kwargs)
            for p in page.get("Parameters", []):
                name = str(p["Name"]).rsplit("/", 1)[-1].lower()
                if name in ALLOWED and name.upper() not in env:
                    env[name.upper()] = p["Value"]
                    exported.append(name.upper())
            token = page.get("NextToken")
            if not token:
                return exported
    except (ClientError, BotoCoreError) as exc:
        raise StoreUnavailable(f"Secrets could not be read ({type(exc).__name__}).", retryable=True) from exc
