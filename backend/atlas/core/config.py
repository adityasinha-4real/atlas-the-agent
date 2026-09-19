"""Application configuration, sourced entirely from the environment.

Settings use the ``ATLAS_`` prefix and are validated by pydantic-settings. No
secrets are hardcoded; a ``.env`` file is loaded for local development only.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProvider = Literal["ollama", "echo"]


class Settings(BaseSettings):
    """Typed application settings.

    Every field maps to an ``ATLAS_``-prefixed environment variable, e.g.
    ``ATLAS_LLM_PROVIDER``. Defaults are safe for local development.
    """

    model_config = SettingsConfigDict(
        env_prefix="ATLAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "ATLAS"
    environment: Literal["development", "test", "production"] = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # --- Persistence ---
    data_dir: str = "./data"
    database_url: str = "sqlite+aiosqlite:///./data/atlas.db"

    # --- LLM gateway ---
    llm_provider: LLMProvider = "ollama"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_base_url: str = "http://localhost:11434"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 120.0

    # --- Agent (M2: executor ReAct loop + JSON repair) ---
    agent_max_iterations: int = Field(default=5, ge=1, le=20)
    agent_repair_attempts: int = Field(default=2, ge=0, le=5)

    # --- Planner / context (M3) ---
    # Hard cap on the planner's task list (design doc §1.1: a list, not a DAG).
    agent_max_tasks: int = Field(default=5, ge=1, le=10)
    # Per prior-task output budget when composing a later task's context (§1.11:
    # summarize-at-source keeps prompt size roughly constant across tasks).
    context_prior_output_chars: int = Field(default=600, ge=100, le=4000)
    # When False, a single-task plan short-circuits synthesis (relays the task
    # output) to save an LLM call; set True to always run the synthesis step.
    agent_force_synthesis: bool = False

    # --- Self-correction (M4, RFC-0002) ---
    # Master switch for the reflect -> retry/replan/abort loop. Defaults to False
    # so the M4 scaffolding does not alter M3 behavior until the loop is wired in
    # and reflection is explicitly enabled (RFC-0002 §17).
    agent_enable_reflection: bool = False
    # Retries per task (attempts = retries + 1) and replans per run.
    agent_max_retries: int = Field(default=2, ge=0, le=5)
    agent_max_replans: int = Field(default=1, ge=0, le=3)
    # Hard per-run caps on total LLM and tool calls (deterministic termination).
    agent_max_model_calls: int = Field(default=60, ge=1, le=1000)
    agent_max_tool_calls: int = Field(default=40, ge=1, le=1000)
    # Below this confidence, a retry/replan verdict downgrades to accept
    # (0.0 disables the gate).
    agent_reflection_min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # --- Episodic memory (M5, RFC-0003) ---
    # Master switch. Defaults to False so behavior is byte-for-byte M4 until memory
    # is explicitly enabled (RFC-0003 §17, invariant I-15).
    memory_enabled: bool = False
    # Recall: how many past lessons to inject at plan time, their total character
    # budget, and the minimum relevance (fraction of goal terms a memory must
    # match) below which a candidate is not injected. ``k = 0`` disables recall
    # while leaving writing on.
    memory_recall_k: int = Field(default=3, ge=0, le=10)
    memory_recall_char_budget: int = Field(default=800, ge=100, le=4000)
    memory_recall_min_score: float = Field(default=0.15, ge=0.0, le=1.0)
    # Store bound and expiry: prune keeps at most ``max_records`` active memories
    # and hard-deletes rows soft-expired longer than ``expiry_days``.
    memory_max_records: int = Field(default=500, ge=10, le=100000)
    memory_expiry_days: int = Field(default=90, ge=1, le=3650)
    # Distill each finished run into a memory with an LLM call; False uses a
    # zero-LLM heuristic writer. Runs with fewer than ``min_tasks`` tasks are not
    # written (skip trivial/echo runs). ``write_outcomes`` gates which run outcomes
    # produce a memory.
    memory_distill_with_llm: bool = True
    memory_min_tasks: int = Field(default=1, ge=0, le=10)
    memory_write_outcomes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["done", "partial"]
    )

    # --- Tools ---
    # Path jail for file_read/file_write. All file access is confined here.
    workspace_dir: str = "./data/workspace"
    tool_timeout_seconds: float = Field(default=30.0, gt=0)
    web_search_max_results: int = Field(default=5, ge=1, le=20)
    web_fetch_max_chars: int = Field(default=2000, ge=200, le=20000)

    # --- Observability (M6, RFC-0004 §28-30) ---
    # Passive, best-effort, off by default so behavior is byte-for-byte M5
    # (invariant I-23). ``metrics_enabled`` turns on the in-process metrics
    # registry and the ``/metrics`` endpoint; ``log_format`` selects the text
    # (default, unchanged) or opt-in JSON log formatter.
    metrics_enabled: bool = False
    log_format: Literal["text", "json"] = "text"

    # --- Recovery & integrity (M6, RFC-0004 §13-14) ---
    # ``recovery_enabled`` (default on) reconciles runs left non-terminal by a
    # crash to a consistent terminal state at startup — a no-op for a clean DB, so
    # behavior is preserved for cleanly-terminated runs (RFC-0004 §27, invariant
    # I-26). ``db_integrity_check`` runs ``PRAGMA quick_check`` at startup and
    # fails fast on corruption; default off keeps startup fast.
    recovery_enabled: bool = True
    db_integrity_check: bool = False

    # --- CORS ---
    # List settings are ``NoDecode`` so pydantic-settings hands the raw env string
    # to ``_split_csv`` instead of JSON-decoding it (a plain ``http://...`` value
    # is not valid JSON and would crash startup).
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("cors_origins", "memory_write_outcomes", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allow list settings as a comma-separated string (or a JSON list)."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` instance."""
    return Settings()
