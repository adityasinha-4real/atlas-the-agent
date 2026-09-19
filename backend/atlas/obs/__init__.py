"""Passive observability (M6, RFC-0004): metrics + log correlation.

Everything in this package is **passive and best-effort** (invariant I-25): it
observes the runtime, never decides anything, never mutates run state, and never
raises into a run. It is import-light on purpose so a failure here cannot break a
run's hot path.
"""
