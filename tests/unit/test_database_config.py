"""DATABASE_URL handling for managed PostgreSQL (Neon) — no credentials ever echoed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import ConfigurationError, Settings, get_settings

SECRET = "s3cr3t-Pa55word"
DIRECT = f"postgresql://app_user:{SECRET}@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb"
POOLED = f"postgresql://app_user:{SECRET}@ep-cool-name-123456-pooler.ap-southeast-1.aws.neon.tech/neondb"


def settings(url: str) -> Settings:
    return Settings(database_url=url, _env_file=None)  # type: ignore[call-arg]


def test_neon_direct_url_keeps_ssl_and_channel_binding() -> None:
    s = settings(DIRECT + "?sslmode=require&channel_binding=require")
    assert s.database_url.startswith("postgresql+psycopg://")
    assert "sslmode=require" in s.psycopg_dsn and "channel_binding=require" in s.psycopg_dsn


def test_neon_url_without_sslmode_gets_require() -> None:
    assert settings(DIRECT).psycopg_dsn.endswith("/neondb?sslmode=require")


def test_explicit_sslmode_is_never_downgraded_or_duplicated() -> None:
    dsn = settings(DIRECT + "?sslmode=verify-full").psycopg_dsn
    assert dsn.count("sslmode=") == 1 and "sslmode=verify-full" in dsn


def test_neon_pooled_url_rejected_with_guidance() -> None:
    with pytest.raises(ValidationError, match="pooled endpoint"):
        settings(POOLED + "?sslmode=require")


def test_search_path_startup_option_rejected() -> None:
    with pytest.raises(ValidationError, match="must not set search_path"):
        settings(DIRECT + "?sslmode=require&options=-csearch_path%3Dlanggraph")


def test_other_hosts_unchanged() -> None:
    url = "postgresql://u:p@db.internal:5432/app?sslmode=disable"
    assert settings(url).psycopg_dsn == url


def test_configuration_errors_never_contain_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", POOLED)
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigurationError) as exc:
            get_settings()
    finally:
        get_settings.cache_clear()
    text = str(exc.value) + repr(exc.value)
    assert "database_url" in text and "pooled endpoint" in text
    assert SECRET not in text and "app_user" not in text
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_checkpointer_sends_no_startup_options() -> None:
    import inspect

    from app.workflows import checkpointer

    src = inspect.getsource(checkpointer._pool)
    assert '"options"' not in src and "configure=_use_checkpoint_schema" in src
