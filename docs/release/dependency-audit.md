# Dependency audit — v0.6.0

**Method:** reviewed the pinned manifests, ran `npm audit` (frontend) and
`pip check` (backend) on the committed lockfile/environment.

## Backend (Python ≥ 3.11; developed on 3.14)

Runtime pins ([../../backend/requirements.txt](../../backend/requirements.txt)),
floored so **prebuilt wheels exist for CPython 3.14** (no source builds, ADR-0006):

| Package | Constraint |
|---|---|
| fastapi | `>=0.115,<1.0` |
| uvicorn | `>=0.34,<1.0` |
| websockets | `>=14,<16` |
| pydantic | `>=2.11,<3.0` |
| pydantic-settings | `>=2.7,<3.0` |
| SQLAlchemy | `>=2.0.40,<2.1` |
| aiosqlite | `>=0.20,<1.0` |
| httpx | `>=0.28,<1.0` |
| ddgs | `>=9.0,<10` |
| lxml | `>=5.0,<7` |

Dev-only: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`.

- ✅ `pip check` — **no broken requirements**.
- ✅ Install is wheel-only (`--only-binary=:all:`); plain `uvicorn` + pure-Python
  `websockets` avoid Rust/native build toolchains.

## Frontend (Node 24)

Runtime: `next ^14.2.35`, `react`/`react-dom 18.3.1`, `openapi-fetch ^0.13.8`.
Dev: `openapi-typescript ^7.13.0`, `typescript 5.6.3`, `tailwindcss`, `postcss`,
`autoprefixer`, `@types/*`.

- ⚠️ `npm audit` — **2 advisories (1 high, 1 moderate)**, both in the
  **pre-existing** `next` / transitive `postcss` chain (App Router CSP,
  cache-poisoning, image-optimizer DoS class; PostCSS stringify XSS). The only
  offered fix is `next@16.2.10`, a **breaking major** bump.
- ✅ **Not introduced by this release.** The advisories predate the OpenAPI
  client work; the newly added `openapi-fetch` / `openapi-typescript` packages
  contribute **no** advisories.
- **Disposition:** accepted for v0.6.0, tracked as a known limitation. A Next.js
  major upgrade is a deliberate, separately-verified change (build/runtime
  behavior), out of scope for a behavior-preserving release-engineering pass.

## Recommendation

- ◽ Schedule the `next@16` upgrade as its own change with a full frontend
  build + manual smoke, then clear both advisories.
