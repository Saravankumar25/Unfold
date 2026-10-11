"""Lambda entry point (ADR 0010): `uvicorn unfold.api.aws:create_aws_app --factory`, run behind the Lambda Web
Adapter. Same FastAPI app as everywhere else; the only AWS-specific step is reading secrets from SSM first."""

from __future__ import annotations

import os

from fastapi import FastAPI

from unfold.api.main import create_app
from unfold.core.config import get_settings
from unfold.persistence.ssm_secrets import load_ssm_env


def create_aws_app() -> FastAPI:
    load_ssm_env(os.environ.get("SSM_PATH_PREFIX", ""))
    return create_app(get_settings())
