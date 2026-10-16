"""Small units: redaction, hashing, transitions, settings validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.audit import redact
from app.services.hashing import payload_hash
from app.workflows.transitions import TERMINAL, can_transition


def test_redaction_masks_pii_and_secrets() -> None:
    out = redact(
        {
            "email": "aarav@example.com",
            "note": "mail aarav.sharma@example.com or call +91 98765 43210",
            "card": "4111 1111 1111 1111",
            "nested": [{"token": "abc"}],
            "auth": "Bearer eyJabc.def",
        }
    )
    assert out["email"] == "[REDACTED]"
    assert "a***@example.com" in out["note"] and "98765" not in out["note"]
    assert out["card"] == "[REDACTED_NUMBER]"
    assert out["nested"][0]["token"] == "[REDACTED]"
    assert out["auth"] == "Bearer [REDACTED]"


def test_payload_hash_is_canonical() -> None:
    assert payload_hash({"a": 1, "b": [1, 2]}) == payload_hash({"b": [1, 2], "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


def test_transitions() -> None:
    assert can_transition("queued", "running")
    assert can_transition("running", "awaiting_approval")
    assert can_transition("awaiting_approval", "queued")
    assert not can_transition("awaiting_approval", "completed")
    assert not can_transition("queued", "completed")
    for t in TERMINAL:
        assert not can_transition(t, "running")


def test_production_settings_require_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", _env_file=None)  # type: ignore[call-arg]
    ok = Settings(
        environment="production",
        jwt_secret="x" * 40,
        mock_admin_token="secret-admin",
        cors_origins="https://app.example",
        _env_file=None,
    )  # type: ignore[call-arg]
    assert ok.cors_origin_list == ["https://app.example"]


def test_database_url_normalised_and_validated() -> None:
    s = Settings(database_url="postgres://u:p@h:5432/d", _env_file=None)  # type: ignore[call-arg]
    assert s.database_url.startswith("postgresql+psycopg://")
    with pytest.raises(ValidationError):
        Settings(database_url="mysql://u:p@h/d", _env_file=None)  # type: ignore[call-arg]


def test_missing_llm_key_falls_back_to_mock() -> None:
    s = Settings(llm_provider="anthropic", llm_api_key="", _env_file=None)  # type: ignore[call-arg]
    assert s.llm_provider == "mock"
    assert "LLM_API_KEY" in s.llm_fallback_reason
