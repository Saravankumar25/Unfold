"""Application settings loaded from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Local development fallback only. This value is intentionally fake and exists to exercise secret redaction.
DEV_OPENAI_API_KEY = "sk-test-FAKE0000FAKE0000FAKE0000FAKE"


class Settings(BaseSettings):
    """Runtime configuration for the API and the worker."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://docqa:docqa@localhost:5432/docqa"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    collection_name: str = "docqa_chunks"
    openai_api_key: str = DEV_OPENAI_API_KEY
    chat_model: str = "gpt-4o-mini"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 8
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
