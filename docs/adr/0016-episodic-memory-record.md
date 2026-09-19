# ADR-0016 — Episodic memory record & distillation

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
M5 gives the agent long-term memory (RFC-0003). The question is *what* to store per
run and *when*. Storing raw run transcripts (tool outputs, fetched pages, file
contents) would bloat the store, leak data, and make recall noisy; injecting raw
text into a later planner prompt is also a prompt-injection surface. We want small,
reusable, low-risk records.

## Decision
Store **one distilled record per finished run** — `goal`, `outcome`, a 1–2 sentence
`summary`, transferable `lessons`, and the `tools_used` — written **at
finalization**, after the answer is delivered. Distillation is a single LLM call
(`MEMORY` budget category) that is instructed to produce goal-agnostic advice and to
omit secrets/PII/verbatim content; if it fails or is unparseable it falls back to a
**zero-LLM heuristic** (outcome + tool approach). Writing is **idempotent per run**
(`UNIQUE(run_id)` upsert), so replay/re-finalization never duplicates.

## Consequences
- Records are tiny and reusable; recall stays cheap and on-topic.
- Only distilled, model-written text + the goal are persisted — never raw
  outputs (privacy, invariant I-18).
- Distillation cost is one call **after** the user's answer, off the perceived-
  latency path; the heuristic keeps memory working with `MEMORY_DISTILL_WITH_LLM=false`
  or under budget pressure.
- Trade-off: a distilled lesson can lose nuance a full transcript would keep; that
  is an acceptable price for a bounded, safe, reusable store. Richer per-attempt
  provenance already lives in the event ledger and `task_attempts`.
