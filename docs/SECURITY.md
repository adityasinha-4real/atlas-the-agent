# ATLAS security review

**Audit: security (RFC-0004 §26).** ATLAS is a **local, single-user** runtime.
This document states that threat model, the surfaces reviewed for v0.6.0, and the
risks consciously accepted. No new auth/multi-tenant model is introduced in M6 —
the review hardens the existing posture.

## Threat model

- **Trust boundary:** the operator runs ATLAS on their own machine and trusts
  themselves. There is **no authentication** and no per-user isolation; anyone who
  can reach the bound port can drive the agent. Bind to `127.0.0.1` (or a trusted
  network) in any shared setting.
- **In scope:** input handling that could crash the process, corrupt the store, or
  escape the file/prompt boundaries; egress via network tools; secret/PII leakage
  into the store or logs.
- **Out of scope (accepted):** authn/z, rate limiting, tenant isolation,
  transport encryption — deferred with the single-user posture (revisit before any
  multi-user deployment).

## Reviewed surfaces

| Surface | Finding | Control |
|---|---|---|
| **FTS `?q=`** (`/memories`) | `MATCH` syntax could error or inject | Queries are parameterized; malformed `MATCH` degrades to a `LIKE` fallback, never a 500 (`test_api_memory`, `test_memory_store`). |
| **Path params / bodies** | Unknown ids, oversized/odd input | Pydantic validation → `422`; unknown id → `404`; no unbounded work. |
| **File tools** | Path traversal out of the workspace | `file_read`/`file_write` are confined to a resolved `workspace_dir` **path jail**; `..`/absolute escapes are rejected (`test_tools`). |
| **`web_fetch` / `web_search`** | SSRF, local-file/metadata read | Unauthenticated **egress** tools by design; scheme/URL validation; documented as network egress. Off the hot path and covered by arg-validation tests (no live calls in CI). |
| **Prompt injection** | Crafted goal/lesson text escaping the prompt | Recalled memory is injected as **clearly-delimited untrusted hints** (I-19); the recorded `memory.recalled` text is what was injected, so replay shows exactly what the model saw. |
| **Secrets / PII** | Raw output or secrets persisted/logged | Memory distillation stores summaries/lessons, not raw model I/O (I-18); default (`text`) logging does not emit full prompts/responses; JSON logging carries only run/task ids + levels. |
| **CORS** | Over-permissive origins | `ATLAS_CORS_ORIGINS` defaults to `http://localhost:3000`; **no wildcard** in shipped config. |
| **Dependencies** | Supply-chain surface | Wheel-only installs (ADR-0006) with pinned floors; `pip audit` / `npm audit` are advisory in the dev workflow. |

## Accepted risks

- **No auth** — inherent to the single-user model; mitigate by binding locally.
- **Network egress tools** (`web_fetch`/`web_search`) can reach arbitrary URLs the
  operator's goal induces; acceptable for a local research agent, revisit for
  multi-user.
- **`pip/npm audit` are advisory**, not blocking, to avoid third-party-advisory
  flakiness gating merges; reviewed manually at release.

## Follow-ups (non-blocking)

- Adopt `ruff` `S` (bandit) rules selectively once the false-positive surface is
  triaged.
- Add an adversarial-lesson prompt-injection test to lock the delimiter contract.
- Revisit the entire model (authn, rate limits, TLS) **before any multi-user or
  hosted deployment** — tracked in [TODO](TODO.md).
