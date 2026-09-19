"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.config import Settings


def test_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.app_name == "ATLAS"
    assert settings.llm_provider == "ollama"
    assert settings.llm_model == "qwen2.5:7b-instruct"
    assert settings.llm_temperature == 0.0


def test_m4_self_correction_defaults() -> None:
    """Reflection is OFF by default so M4 scaffolding preserves M3 behavior."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.agent_enable_reflection is False
    assert settings.agent_max_retries == 2
    assert settings.agent_max_replans == 1
    assert settings.agent_max_model_calls == 60
    assert settings.agent_max_tool_calls == 40
    assert settings.agent_reflection_min_confidence == 0.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agent_max_retries", 6),  # le=5
        ("agent_max_retries", -1),  # ge=0
        ("agent_max_replans", 4),  # le=3
        ("agent_max_model_calls", 0),  # ge=1
        ("agent_max_tool_calls", 1001),  # le=1000
        ("agent_reflection_min_confidence", 1.5),  # le=1.0
        ("agent_reflection_min_confidence", -0.1),  # ge=0.0
    ],
)
def test_m4_settings_bounds_are_enforced(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})  # type: ignore[call-arg]


def test_memory_defaults_are_off() -> None:
    """Memory is disabled by default so behavior stays byte-for-byte M4 (I-15)."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.memory_enabled is False
    assert settings.memory_recall_k == 3
    assert settings.memory_recall_char_budget == 800
    assert settings.memory_recall_min_score == 0.15
    assert settings.memory_max_records == 500
    assert settings.memory_expiry_days == 90
    assert settings.memory_distill_with_llm is True
    assert settings.memory_min_tasks == 1
    assert settings.memory_write_outcomes == ["done", "partial"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("memory_recall_k", 11),  # le=10
        ("memory_recall_k", -1),  # ge=0
        ("memory_recall_char_budget", 50),  # ge=100
        ("memory_recall_min_score", 1.5),  # le=1.0
        ("memory_max_records", 5),  # ge=10
        ("memory_expiry_days", 0),  # ge=1
        ("memory_min_tasks", 11),  # le=10
    ],
)
def test_memory_settings_bounds_are_enforced(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})  # type: ignore[call-arg]


def test_memory_write_outcomes_accepts_csv_string() -> None:
    settings = Settings(memory_write_outcomes="done, partial, failed")  # type: ignore[arg-type]
    assert settings.memory_write_outcomes == ["done", "partial", "failed"]


def test_cors_origins_accepts_csv_string() -> None:
    settings = Settings(cors_origins="http://a.com, http://b.com")  # type: ignore[arg-type]
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


def test_cors_origins_accepts_list() -> None:
    settings = Settings(cors_origins=["http://a.com"])
    assert settings.cors_origins == ["http://a.com"]


# Regression: list settings read from the *environment* (or a .env file) used to be
# JSON-decoded by pydantic-settings before the CSV validator ran, so the documented
# ``ATLAS_CORS_ORIGINS=http://localhost:3000`` crashed startup. These go through the
# real env/.env sources rather than init kwargs, which bypass that decoding.


def test_cors_origins_csv_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_CORS_ORIGINS", "http://a,http://b")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.cors_origins == ["http://a", "http://b"]


def test_cors_origins_single_value_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact value shipped in .env.example and docker-compose.yml."""
    monkeypatch.setenv("ATLAS_CORS_ORIGINS", "http://localhost:3000")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.cors_origins == ["http://localhost:3000"]


def test_memory_write_outcomes_csv_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_MEMORY_WRITE_OUTCOMES", "done,partial,failed")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.memory_write_outcomes == ["done", "partial", "failed"]


def test_list_settings_from_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A .env copied verbatim from .env.example loads without error."""
    monkeypatch.delenv("ATLAS_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ATLAS_MEMORY_WRITE_OUTCOMES", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ATLAS_CORS_ORIGINS=http://a,http://b\nATLAS_MEMORY_WRITE_OUTCOMES=done,partial\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.cors_origins == ["http://a", "http://b"]
    assert settings.memory_write_outcomes == ["done", "partial"]


def test_list_settings_still_accept_json_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON-list values (the pre-fix accepted form) keep working."""
    monkeypatch.setenv("ATLAS_CORS_ORIGINS", '["http://a", "http://b"]')
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.cors_origins == ["http://a", "http://b"]


def test_is_test_flag() -> None:
    assert Settings(environment="test").is_test is True
    assert Settings(environment="development").is_test is False
