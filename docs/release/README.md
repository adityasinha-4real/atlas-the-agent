# ATLAS v0.6.0 release audit

Point-in-time release-engineering audit for **v0.6.0**, based on the current
implementation. Each report states scope, concrete findings (✅ pass · ⚠️ fixed
this pass · ◽ accepted/deferred), and links the canonical living doc for full
detail. These snapshots do not replace the living docs — they record what was
verified at release time.

| Report | Focus | Living doc |
|---|---|---|
| [api-consistency.md](api-consistency.md) | REST/WS surface, response models, status codes | [../API.md](../API.md) |
| [configuration-audit.md](configuration-audit.md) | `ATLAS_*` settings, defaults, parity | [../../backend/.env.example](../../backend/.env.example) |
| [security-review.md](security-review.md) | Threat model, controls, accepted risks | [../SECURITY.md](../SECURITY.md) |
| [dependency-audit.md](dependency-audit.md) | Backend/frontend pins, advisories | — |
| [documentation-audit.md](documentation-audit.md) | Doc/code parity, corrections this pass | [../DOC_AUDIT.md](../DOC_AUDIT.md) |
| [technical-debt.md](technical-debt.md) | Suppressions, resolved/deferred debt | [../TECH_DEBT.md](../TECH_DEBT.md) |
| [known-limitations.md](known-limitations.md) | What v0.6.0 deliberately does not do | — |
| [release-checklist.md](release-checklist.md) | Gate status for cutting v0.6.0 | [../RELEASE.md](../RELEASE.md) |
| [release-notes-v0.6.0.md](release-notes-v0.6.0.md) | User-facing notes, additions since v0.5.0 | [../../CHANGELOG.md](../../CHANGELOG.md) |
| [release-notes-v0.7.0.md](release-notes-v0.7.0.md) | v0.7.0 (M7 ship): fixes and packaging since v0.6.0 | [../../CHANGELOG.md](../../CHANGELOG.md) |

> **v0.7.0 update.** v0.7.0 changed no runtime code apart from a
> config-parsing fix, so this audit set still applies. Two reports were
> updated in place: [known-limitations.md](known-limitations.md) (now v0.7.0)
> and [api-consistency.md](api-consistency.md) (the drift gate landed). The
> v0.7.0 gate status is in [`FINAL_RELEASE_CHECKLIST.md`](../../FINAL_RELEASE_CHECKLIST.md).

**Version:** `0.6.0` — consistent across `atlas.__version__`,
`backend/pyproject.toml`, `frontend/package.json`, and `GET /health`
(asserted by `backend/tests/test_docs_parity.py`).

**Scope note:** this is release engineering only — CI, audits, and release
notes. No runtime code, tests, APIs, config, or dependencies were changed to
produce it; the only edits are documentation and one CI least-privilege
hardening.
