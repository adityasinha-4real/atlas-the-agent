# ADR-0001 — Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-07-09

## Context
ATLAS is built from a senior design review (`ATLAS-Design-Review-V2.md`) that
overturns several choices from an earlier V1 SDD. Those decisions — and the
reasons for the reversals — need to be discoverable without re-reading the whole
review, and future contributors need a lightweight way to add their own.

## Decision
Keep lightweight ADRs in `docs/adr/`, one file per decision, numbered and dated,
using a Context → Decision → Consequences template. The V2 review is treated as
the project specification; ADRs capture the load-bearing decisions distilled from
it plus any new ones made during implementation.

## Consequences
- A recruiter or reviewer can open `docs/adr/` and understand the "why" in
  minutes (design doc §8 identifies this as high-value).
- Reversals from V1 are documented as first-class decisions rather than hidden in
  diffs.
- Superseding a decision means adding a new ADR that references the old one, not
  editing history.
