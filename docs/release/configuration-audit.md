# Configuration / env audit — v0.6.0

**Scope:** every `ATLAS_*` setting on `atlas.core.config.Settings`, checked
against [../../backend/.env.example](../../backend/.env.example). Precedence is
**env var > `.env` > default**; nothing is hardcoded elsewhere.

## Findings

- ✅ **Full parity, CI-enforced.** `test_docs_parity.py` asserts that every
  `Settings` field is documented in `.env.example`
  (`test_every_setting_is_documented_in_env_example`) and that `.env.example`
  contains no phantom `ATLAS_*` keys (`test_env_example_has_no_unknown_variables`).
  A drift in either direction fails CI.
- ✅ **Safe defaults.** Both agent-capability switches are **off** by default,
  so a default install runs the baseline plan → execute → synthesize flow:
  - `ATLAS_AGENT_ENABLE_REFLECTION=false` — no retry/replan/abort.
  - `ATLAS_MEMORY_ENABLED=false` — memory store never read or written.
- ✅ **Passive hardening defaults.** The observability/recovery knobs preserve
  behavior at their defaults:
  - `ATLAS_METRICS_ENABLED=false` → `/metrics` returns `404`.
  - `ATLAS_LOG_FORMAT=text` → no structured-JSON logging.
  - `ATLAS_RECOVERY_ENABLED=true` → startup reconcile, but a **no-op on a clean
    DB** (only touches runs left non-terminal by a crash).
  - `ATLAS_DB_INTEGRITY_CHECK=false` → no startup `PRAGMA quick_check`.
- ✅ **No secrets in config.** No API keys or credentials are required or read;
  the LLM is a local Ollama endpoint or the dependency-free `echo` provider.
- ✅ **CORS is not permissive.** `ATLAS_CORS_ORIGINS` defaults to
  `http://localhost:3000`; no wildcard ships.

## Notes

- The frontend reads a single public variable, `NEXT_PUBLIC_API_BASE`
  (default `http://localhost:8000`), documented in
  [../../frontend/.env.local.example](../../frontend/.env.local.example).
- The README configuration table lists the most-used knobs; the full set with
  inline rationale lives in `.env.example` (the source of truth the parity test
  reads).
