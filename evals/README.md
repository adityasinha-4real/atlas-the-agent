# ATLAS evals

Design rationale: [RFC-0004](../docs/rfc/0004-m6-hardening.md) §21.

Reproducible, offline agent-quality evals. Each **golden** is a goal plus the exact
`ScriptedGateway` responses that drive the run and the properties the outcome must
satisfy. Everything runs against the FakeLLM — no Ollama, no network — so evals are
deterministic and CI-safe. The harness *measures* the agent; it never changes agent
behavior.

## Run

```bash
# from the repo root, with the backend venv
backend/.venv/Scripts/python.exe -m evals          # run all -> evals/scorecard.md
backend/.venv/Scripts/python.exe -m evals --json   # also print results.json path

# or via make
make evals
```

Exits non-zero if any eval fails, so CI can gate on it.

## What's covered

| Eval | Category |
|---|---|
| `single_task` | single-task plan, no tools |
| `multi_task` | multi-task plan + synthesis |
| `tool_use` | calculator tool call → answer |
| `retry_then_accept` | reflection: a poor attempt is retried and accepted |
| `replan` | reflection: a `replan` verdict swaps the remaining plan |
| `partial_abort` | reflection: an `abort` yields a graceful partial answer |
| `retry_exhaustion_fails` | recovery exhausted → run fails cleanly |
| `memory_lift` | run A writes a lesson; a related run B recalls it |

## Output

- `scorecard.md` — committed, human-readable: pass-rate + per-eval status, steps
  (= model calls, a token-budget proxy), and tool usage.
- `results.json` — machine-readable, for trend tracking.

## Adding a golden

Add a `Golden(...)` to `goldens.py` with its scripted responses (authored against
the runtime call order: planner → per-task executor turns → reflector when
reflection is on → synthesizer for multi-output plans) and the expected properties.
Extra trailing responses are harmless — the gateway only errors when *exhausted*.
A real-model run (local Ollama) is a future option; the scripted path is the CI
reference.
