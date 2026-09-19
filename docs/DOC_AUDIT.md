# ATLAS documentation audit

**Audit: documentation (RFC-0004 §18).** Docs match code exactly — no stale
counts, versions, or claims. Where a check can be automated it is a test
(`test_docs_parity.py`), so the drift becomes a CI failure rather than a shipped
error.

## Automated (enforced in CI)

| Check | Mechanism | Status |
|---|---|---|
| Version string consistent (backend == pyproject == frontend == `/health`) | `test_docs_parity.py::test_version_is_consistent_across_the_repo` + `test_health_reports_v060` | ✅ |
| Current version is `0.6.0` | `test_docs_parity.py::test_version_is_the_m6_release` | ✅ |
| Every `ATLAS_*` setting documented in `.env.example` | `test_docs_parity.py::test_every_setting_is_documented_in_env_example` | ✅ |
| No phantom vars in `.env.example` | `test_docs_parity.py::test_env_example_has_no_unknown_variables` | ✅ |
| CHANGELOG has the current version's section | `test_docs_parity.py::test_changelog_documents_the_current_version` | ✅ |
| OpenAPI documents the route set | `test_api_memory.py::test_openapi_documents_memory_routes` | ✅ |

## Manual checklist (verified for v0.6.0)

- [x] **Test count** updated everywhere it appears (README, ARCHITECTURE) →
  **307** (was 243 at M5). Value taken from `pytest --collect-only`.
- [x] **Version** `0.6.0` in README status, CHANGELOG, milestone doc, backend +
  frontend manifests.
- [x] **Roadmap markers** — M1–M5 ✅, **M6 ✅**, M7 pending — consistent across
  README, ARCHITECTURE §11, and TODO.
- [x] **Config parity** — `Settings` fields == `.env.example` keys == README config
  table (the four M6 knobs added to the README table).
- [x] **Every referenced ADR/RFC exists** — ADR-0001…0024 and RFC-0001…0004 present
  (`docs/adr/`, `docs/rfc/`), index in `docs/adr/README.md` current.
- [x] **Repo-layout** in README/ARCHITECTURE lists `atlas/obs/`, `atlas/recovery/`,
  `bench/`, `evals/` with one-line purposes.
- [x] **Diagrams** — ARCHITECTURE/README note the passive obs + recovery seams and
  that they don't alter run behavior.
- [x] **New audit docs cross-linked** — API, SECURITY, RELEASE, TECH_DEBT, this file.

## Notes

The former `249 → 243` class of mismatch is now structurally prevented for the
version/config axes (they are asserted). The test-count string remains
hand-updated at release (it is self-referential — asserting it would change it);
the release checklist (RELEASE §6) covers it, and the collect-only value is the
source of truth.
