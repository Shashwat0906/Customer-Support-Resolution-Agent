"""Application settings, validated at startup.

All configuration comes from environment variables (optionally a local `.env`).
Invalid or missing required values fail fast with a clear error instead of
surfacing later as confusing runtime failures.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-0123456789"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+psycopg://support:support@localhost:5432/support_agent"
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_connect_retries: int = Field(default=10, ge=0, le=60)

    # HTTP
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    port: int = 8000

    # Auth
    jwt_secret: str = DEV_JWT_SECRET
    jwt_ttl_minutes: int = Field(default=720, ge=5)
    demo_auth_enabled: bool = True

    # Mock external services
    payments_base_url: str = "http://localhost:8001/payments"
    shipping_base_url: str = "http://localhost:8001/shipping"
    ticketing_base_url: str = "http://localhost:8001/ticketing"
    mock_admin_token: str = "dev-mock-admin-token"
    payments_api_key: str = "dev-payments-key"
    external_timeout_seconds: float = Field(default=3.0, gt=0, le=60)

    # LLM
    llm_provider: Literal["mock", "anthropic", "openai"] = "mock"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: float = 30.0
    llm_max_repair_attempts: int = Field(default=1, ge=0, le=3)
    llm_input_cost_per_mtok: float = 3.0
    llm_output_cost_per_mtok: float = 15.0
    llm_fallback_reason: str = ""

    # Embeddings
    embedding_provider: Literal["local", "openai"] = "local"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 256

    # Workflow / agents
    agent_mode: Literal["specialist", "single"] = "specialist"
    max_workflow_steps: int = Field(default=40, ge=5, le=500)
    max_cost_usd_per_run: float = Field(default=0.50, gt=0)
    max_agent_iterations: int = Field(default=6, ge=1, le=20)
    max_read_retries: int = Field(default=3, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.2, ge=0)
    max_refund_attempts: int = Field(default=3, ge=1, le=10)
    max_reconcile_attempts: int = Field(default=5, ge=1, le=50)
    max_ticket_update_attempts: int = Field(default=4, ge=1, le=10)
    max_clarifications: int = Field(default=2, ge=0, le=5)
    max_proposal_revisions: int = Field(default=2, ge=0, le=5)
    approval_ttl_minutes: int = Field(default=1440, ge=1)

    # Worker
    run_embedded_worker: bool = False
    worker_poll_seconds: float = 1.0
    job_lease_seconds: int = Field(default=120, ge=5)
    job_max_attempts: int = Field(default=5, ge=1)

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Hosting providers commonly hand out postgres:// or postgresql:// URLs.
        if v.startswith("postgres://"):
            v = "postgresql+psycopg://" + v[len("postgres://") :]
        elif v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        if not v.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL (postgresql://...)")
        return v

    @model_validator(mode="after")
    def _production_checks(self) -> Settings:
        if self.environment == "production":
            if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be set to a random value of at least 32 characters in production")
            if self.mock_admin_token == "dev-mock-admin-token":
                raise ValueError("MOCK_ADMIN_TOKEN must be changed in production")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must list explicit origins in production")
        if self.llm_provider != "mock" and not self.llm_api_key:
            # Degrade gracefully: the app still runs in deterministic mock mode.
            self.llm_fallback_reason = f"LLM_PROVIDER={self.llm_provider} but LLM_API_KEY is empty; using mock provider"
            self.llm_provider = "mock"
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def psycopg_dsn(self) -> str:
        """DSN for raw psycopg (LangGraph checkpointer)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
