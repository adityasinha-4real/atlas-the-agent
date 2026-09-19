# Security review — v0.6.0

**Scope:** input handling, tool egress, and secret/PII exposure for a **local,
single-user** runtime. Full threat model and control table:
[../SECURITY.md](../SECURITY.md).

## Threat model (summary)

The operator runs ATLAS on their own machine and trusts themselves. There is
**no authentication and no per-user isolation** — anyone who can reach the bound
port can drive the agent. In any shared setting, bind to `127.0.0.1`.

## Controls verified

- ✅ **File tools are path-jailed.** `file_read`/`file_write` resolve inside
  `ATLAS_WORKSPACE_DIR`; `..` and absolute-path escapes are rejected (tested).
- ✅ **FTS search is injection-safe.** `/memories?q=` is parameterized; a
  malformed `MATCH` degrades to a `LIKE` scan and never 500s (tested).
- ✅ **Request validation.** Pydantic rejects malformed bodies/params with
  `422`; unknown ids return `404`; no route does unbounded work (list routes
  clamp `limit`/`offset` server-side).
- ✅ **Prompt-injection containment.** Recalled memory is injected as
  clearly-delimited *untrusted hints*, and the exact injected text is recorded in
  the `memory.recalled` event, so replay shows what the model actually saw.
- ✅ **Secrets / PII.** Distillation stores summaries/lessons, not raw model
  I/O; default (`text`) logging does not emit prompts/responses; JSON logging
  carries only run/task ids and levels.
- ✅ **CORS.** No wildcard ships; origins are restricted to
  `ATLAS_CORS_ORIGINS`.

## Accepted risks (unchanged for v0.6.0)

- ◽ **No auth / no transport encryption** — inherent to the single-user model;
  mitigate by binding locally. Revisit before any multi-user or hosted deploy.
- ◽ **Network egress tools** (`web_search`/`web_fetch`) can reach arbitrary URLs
  the operator's goal induces; acceptable for a local research agent.
- ◽ **`pip audit` / `npm audit` are advisory**, not merge-blocking, to avoid
  third-party-advisory flakiness gating CI. Reviewed manually at release — see
  [dependency-audit.md](dependency-audit.md).

## Follow-ups (non-blocking, tracked)

- Adopt `ruff` `S` (bandit) rules once the false-positive surface is triaged.
- Add an adversarial-lesson prompt-injection test to lock the delimiter contract.
- Full authn/authz + rate limiting + TLS before any multi-user deployment.
