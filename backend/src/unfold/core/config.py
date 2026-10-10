"""Typed, environment-driven settings (LLD §2). One instance per process via `get_settings()`."""

from __future__ import annotations

import ipaddress
import json
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# Conservative free-tier defaults. Override with LLM_MODEL_LIMITS (JSON) when providers change quotas.
# Gemini 2.5 models are closed to new API users since 2026 (404 "no longer available"); Pro tiers have no
# free quota. Each capability tier uses a *different* model so per-model free quotas add up (HLD D1).
DEFAULT_MODEL_LIMITS: dict[str, dict[str, int]] = {
    "gemini-3.5-flash-lite": {"rpm": 15, "tpm": 250_000, "rpd": 1000, "concurrency": 4},
    "gemini-3.5-flash": {"rpm": 10, "tpm": 250_000, "rpd": 250, "concurrency": 3},
    "gemini-3.6-flash": {"rpm": 10, "tpm": 250_000, "rpd": 250, "concurrency": 3},
    "openai/gpt-oss-20b": {"rpm": 30, "tpm": 8_000, "rpd": 1000, "concurrency": 2},
    "openai/gpt-oss-120b": {"rpm": 30, "tpm": 8_000, "rpd": 1000, "concurrency": 2},
    "*": {"rpm": 5, "tpm": 200_000, "rpd": 100, "concurrency": 2},
}

_DEV_NEO4J_PASSWORD = "unfold-neo4j-pass"  # noqa: S105 - documented local-dev default, rejected in production
_DEV_OBJECT_STORE_SECRET = "unfold-minio-secret"  # noqa: S105

_DEV_OBJECT_STORE_ACCESS_KEY = "unfold"


def _trusts_everyone(entry: str) -> bool:
    if "*" in entry:
        return True
    try:
        return ipaddress.ip_network(entry, strict=False).prefixlen == 0
    except ValueError:
        return False  # a hostname-like entry; uvicorn matches it literally


_SECRET_FIELDS = (
    "gemini_api_key_1",
    "gemini_api_key_2",
    "gemini_api_key_3",
    "gemini_api_key_4",
    "groq_api_key_1",
    "groq_api_key_2",
    "groq_api_key_3",
    "groq_api_key_4",
    "github_token",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore", case_sensitive=False)

    # app
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    public_web_origin: str = "http://localhost:3000"
    cookie_secure: bool = False
    session_cookie_name: str = "unfold_session"

    # stores
    database_url: str = "postgresql+asyncpg://unfold:unfold@localhost:5432/unfold"
    redis_url: str = "redis://localhost:6379/0"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = SecretStr(_DEV_NEO4J_PASSWORD)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_prefix: str = "unfold_chunks"
    object_store_endpoint: str = "localhost:9000"
    object_store_access_key: str = "unfold"
    object_store_secret_key: SecretStr = SecretStr(_DEV_OBJECT_STORE_SECRET)
    object_store_bucket: str = "unfold"
    object_store_secure: bool = False

    # llm
    gemini_api_key_1: SecretStr | None = None
    gemini_api_key_2: SecretStr | None = None
    gemini_api_key_3: SecretStr | None = None
    gemini_api_key_4: SecretStr | None = None
    groq_api_key_1: SecretStr | None = None
    groq_api_key_2: SecretStr | None = None
    groq_api_key_3: SecretStr | None = None
    groq_api_key_4: SecretStr | None = None
    gemini_model_fast: str = "gemini-3.5-flash-lite"
    gemini_model_balanced: str = "gemini-3.5-flash"
    gemini_model_reasoning: str = "gemini-3.6-flash"
    groq_model_fast: str = "openai/gpt-oss-20b"
    groq_model_balanced: str = "openai/gpt-oss-120b"
    llm_model_limits: str = ""
    llm_quota_scope: Literal["shared", "per_key"] = "shared"
    analysis_llm_call_budget: int = 150
    analysis_llm_token_budget: int = 1_500_000  # input + output tokens per job; further calls are refused
    llm_request_timeout_seconds: float = 120.0

    # local models
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    model_cache_dir: str = ".model-cache"

    # limits
    session_ttl_seconds: int = 86_400
    session_max_lifetime_seconds: int = 259_200
    max_zip_bytes: int = 50 * 1024 * 1024
    max_extracted_bytes: int = 300 * 1024 * 1024
    max_file_count: int = 20_000
    max_single_file_bytes: int = 1024 * 1024
    max_path_depth: int = 32
    max_compression_ratio: int = 100
    max_repo_archive_bytes: int = 100 * 1024 * 1024
    max_projects_per_hour: int = 10
    max_chat_messages_per_minute: int = 30
    job_timeout_seconds: int = 1800
    max_job_attempts: int = 3
    max_concurrent_jobs: int = 2
    section_concurrency: int = 2
    # per-chapter investigation caps (docs/design/investigation-engine.md §5); inside the per-job LLM budget
    research_max_rounds: int = 2
    research_max_retrievals: int = 12
    research_max_llm_calls: int = 4
    research_brief_tokens: int = 6000
    max_sessions_per_ip_per_hour: int = 20
    max_sessions_per_hour: int = 500
    max_projects_per_ip_per_hour: int = 30
    max_projects_per_hour_global: int = 200

    # network trust: only these peers may set X-Forwarded-For (comma separated IPs/CIDRs; never "*")
    trusted_proxy_ips: str = "127.0.0.1"

    # misc
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_codeload_url: str = "https://codeload.github.com"
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "unfold"
    work_dir: str = ""

    @field_validator(*_SECRET_FIELDS, mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if v in ("", None) else v

    @model_validator(mode="after")
    def _production_guard(self) -> Settings:
        """Refuse to start in production with dev defaults (audit S-3)."""
        if self.app_env != "production":
            return self
        problems: list[str] = []
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true")
        if self.neo4j_password.get_secret_value() == _DEV_NEO4J_PASSWORD:
            problems.append("NEO4J_PASSWORD is the development default")
        if self.object_store_secret_key.get_secret_value() == _DEV_OBJECT_STORE_SECRET:
            problems.append("OBJECT_STORE_SECRET_KEY is the development default")
        if make_url(self.database_url).password in (None, "", "unfold"):
            problems.append("DATABASE_URL has an empty or development password")
        if self.object_store_access_key == _DEV_OBJECT_STORE_ACCESS_KEY:
            problems.append("OBJECT_STORE_ACCESS_KEY is the development default")
        if not self.trusted_proxies or any(_trusts_everyone(p) for p in self.trusted_proxies):
            problems.append("TRUSTED_PROXY_IPS must list specific proxies, not '*' or a /0 network")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def trusted_proxies(self) -> list[str]:
        return [p.strip() for p in self.trusted_proxy_ips.split(",") if p.strip()]

    @property
    def gemini_keys(self) -> list[SecretStr]:
        keys = (self.gemini_api_key_1, self.gemini_api_key_2, self.gemini_api_key_3, self.gemini_api_key_4)
        return [k for k in keys if k is not None]

    @property
    def groq_keys(self) -> list[SecretStr]:
        keys = (self.groq_api_key_1, self.groq_api_key_2, self.groq_api_key_3, self.groq_api_key_4)
        return [k for k in keys if k is not None]

    def model_limits(self, model: str) -> dict[str, int]:
        limits = dict(DEFAULT_MODEL_LIMITS)
        if self.llm_model_limits.strip():
            limits.update(json.loads(self.llm_model_limits))
        return limits.get(model) or limits["*"]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    # --- phase 3: chat, exports, cleanup ---------------------------------------------------------------------
    chat_llm_call_budget: int = (
        40  # model calls one chat may use in total (hard stop at 1.3x, see the gateway)
    )
    chat_llm_token_budget: int = 400_000  # input + output tokens per chat
    chat_context_tokens: int = 6_000
    chat_history_messages: int = 6
    max_chats_per_project: int = 20
    export_ttl_seconds: int = 86_400
    max_exports_per_hour: int = 10  # per project
    export_download: Literal["proxy", "presigned"] = "proxy"
    export_presigned_ttl_seconds: int = 300
    sweep_interval_seconds: int = (
        300  # in-process sweeper period of the API; 0 = an external scheduler runs it
    )
    # --- end phase 3 -----------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
