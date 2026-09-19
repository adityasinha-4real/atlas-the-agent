# RFC-0002 — M4 Architecture Validation

- **Status:** Final pre-implementation validation for [RFC-0002](0002-m4-self-correction.md)
  (approved 2026-07-09 with 12 amendments).
- **Purpose:** Prove the M4 design is internally consistent before any code is
  written — complete run/task state machines, exact event timelines, a failure
  matrix, unambiguous budget accounting, a replay proof, and the finalized
  PR-sized phase plan.
- **Scope:** Design validation only. No source code is changed by this document.

> **Enum grounding (verified against the current tree).**
> `RunStatus = {created, planning, running, paused, done, failed, cancelled}`
> ([schemas.py](../../backend/atlas/agent/schemas.py)); terminal success is
> **`done`** (event `run.completed`). `TaskStatus (M3) = {pending, running, done,
> failed, skipped}`; **M4 adds `retrying`, `cancelled`**. Events are the renamed
> `task.reflected` / `plan.replanned` plus `task.retrying`, `task.skipped`,
> `task.cancelled`, `budget.exceeded`.

---

## 1. Runtime (run) state machine

The user-requested lifecycle names `REFLECTING`, `REPLANNING`, `SYNTHESIZING`,
`COMPLETED`. These are the **logical** run phases M4 moves through. To avoid a
gratuitous schema/enum change (and to keep event-sourcing the single source of
truth), M4 does **not** add persisted `RunStatus` members for the three
intra-execution phases — they are sub-phases of persisted `running`, and the
active phase is always derivable from the event ledger. The logical FSM and its
persisted mapping:

| Logical state | Persisted `RunStatus` | How the ledger reveals it |
|---|---|---|
| `CREATED` | `created` | `run.created` is the last lifecycle event |
| `PLANNING` | `planning` | after `run.started`, before `plan.created` |
| `RUNNING` | `running` | after `plan.created`, executor events flowing |
| `REFLECTING` | `running` | between a task's last executor event and its `task.reflected` |
| `REPLANNING` | `running` | between a `replan` verdict and `plan.replanned` |
| `SYNTHESIZING` | `running` | after the final `task.completed`, `answer.token`s flowing |
| `COMPLETED` | `done` | `answer.completed` → `run.completed` |
| `FAILED` | `failed` | `run.failed` (optionally preceded by `answer.completed` = partial) |
| `CANCELLED` | `cancelled` | `run.cancelled` |

### 1.1 Complete run FSM (logical)

```
                         ┌─────────────────────────────────────────────┐
                         │                                             │
  CREATED ──run.started──► PLANNING ──plan.created──► RUNNING ◄──────┐  │
     │                        │                        │  │  │        │  │
     │                        │                        │  │  │ (task done, more tasks)
     │                        │                        │  │  └────────┘  │
     │                        │             (task attempt ends)          │
     │                        │                        ▼                 │
     │                        │                   REFLECTING             │
     │                        │              accept │ retry │ replan     │
     │                        │        ┌────────────┼───────┼─────────┐  │
     │                        │  (accept, last)  (retry) (replan)     │  │
     │                        │        │            │       ▼         │  │
     │                        │        │            └──► RUNNING   REPLANNING
     │                        │        │            (next attempt)    │  │
     │                        │        │                     plan.replanned
     │                        │        ▼                              │  │
     │                        │   SYNTHESIZING ◄─────────(new tasks)──┘  │
     │                        │        │                                 │
     │                        │        │ answer.completed                │
     │                        │        ▼                                 │
     │                        │    COMPLETED                             │
     │                        │                                         │
     ▼ (cancel any phase)     ▼ (planner err / abort / budget / task err after recovery)
  CANCELLED  ◄───────────────  FAILED  ◄────────(SYNTHESIZING partial on abort)
```

### 1.2 Legal run transitions (exhaustive)

| # | From → To | Trigger | Emits |
|---|---|---|---|
| R1 | `CREATED → PLANNING` | background task starts | `run.started` |
| R2 | `CREATED → CANCELLED` | cancel before start observed | `run.cancelled` |
| R3 | `PLANNING → RUNNING` | plan produced & persisted | `plan.created` |
| R4 | `PLANNING → FAILED` | `PlannerError` / `LLMError` | `run.failed` |
| R5 | `PLANNING → CANCELLED` | cancel observed before/after plan | `run.cancelled` |
| R6 | `PLANNING → FAILED` | `BudgetExceeded` (planner path) | `budget.exceeded` → `run.failed` |
| R7 | `RUNNING → REFLECTING` | a task attempt finishes (reflection enabled) | executor events → (reflection) |
| R8 | `RUNNING → RUNNING` | `accept`, more tasks remain | `task.completed` |
| R9 | `RUNNING → FAILED` | executor/LLM error with reflection disabled (M3 parity) | `task.failed` → `run.failed` |
| R10 | `RUNNING → FAILED` | `BudgetExceeded` (executor/tool path) | `budget.exceeded` → `run.failed` |
| R11 | `RUNNING → CANCELLED` | cancel between iterations / tasks | `task.cancelled`\* → `run.cancelled` |
| R12 | `REFLECTING → RUNNING` | `retry` and retries remain | `task.retrying` |
| R13 | `REFLECTING → RUNNING` | `accept`, more tasks remain | `task.completed` |
| R14 | `REFLECTING → SYNTHESIZING` | `accept` on the **last** task | `task.completed` |
| R15 | `REFLECTING → REPLANNING` | `replan` and replans remain | (verdict recorded) `task.reflected` |
| R16 | `REFLECTING → SYNTHESIZING` | `abort` / retry+replan exhausted → partial | `task.failed`, `task.skipped`\* |
| R17 | `REFLECTING → FAILED` | `BudgetExceeded` during the reflection call | `budget.exceeded` → `run.failed` |
| R18 | `REFLECTING → CANCELLED` | cancel observed before/after reflection call | `task.cancelled`\* → `run.cancelled` |
| R19 | `REPLANNING → RUNNING` | new remaining tasks produced | `plan.replanned` |
| R20 | `REPLANNING → FAILED` | `PlannerError` on replan | `run.failed` |
| R21 | `REPLANNING → CANCELLED` | cancel observed before/after replan | `run.cancelled` |
| R22 | `REPLANNING → FAILED` | `BudgetExceeded` (planner path) | `budget.exceeded` → `run.failed` |
| R23 | `SYNTHESIZING → COMPLETED` | answer composed (happy path) | `answer.completed` → `run.completed` |
| R24 | `SYNTHESIZING → FAILED` | abort partial finalize (run already doomed) | `answer.completed` (partial) → `run.failed` |
| R25 | `SYNTHESIZING → CANCELLED` | cancel between answer chunks | `run.cancelled` |
| R26 | `SYNTHESIZING → FAILED` | `SynthesizerError` / `LLMError` | `run.failed` (no answer) |

\* per-task events emitted for each affected non-terminal task.

**Terminal run states:** `COMPLETED (done)`, `FAILED`, `CANCELLED`. Each is a sink
(no outgoing edges). `PAUSED` remains reserved for M6 and has no M4 edges.

**Reachability & liveness.** Every non-terminal state has at least one edge to a
terminal state (R2/R4/R6; R9/R10/R11; R16/R17/R18; R20/R21/R22; R23/R24/R25/R26),
and the only cycles are the bounded `RUNNING⇄REFLECTING` retry loop (≤
`max_retries+1` per task) and the once-only `REFLECTING→REPLANNING→RUNNING`
detour (≤ `max_replans`). With the model/tool hard caps as an outer bound, **no
infinite path exists** — the run always reaches a terminal state (Invariant I-10).

---

## 2. Task state machine

M4 exercises all seven task states: `PENDING`, `RUNNING`, `RETRYING`, `DONE`,
`FAILED`, `SKIPPED`, `CANCELLED`.

```
                    replan drops it │ run aborts
        ┌───────────────────────────┼──────────────► SKIPPED (terminal)
        │                           │
   PENDING ──select──► RUNNING ──accept──────────────► DONE    (terminal)
        │                 │  │
        │                 │  ├──retry (budget ok)────► RETRYING ──next attempt──► RUNNING
        │                 │  │
        │                 │  └──abort │ retry+replan exhausted │ BudgetExceeded ─► FAILED (terminal)
        │                 │
        │                 └──cancel observed────────► CANCELLED (terminal)
        │
        └──run cancelled (never started)───────────► CANCELLED (terminal)
```

### 2.1 Transition table (every legal edge)

| # | From → To | Trigger / guard |
|---|---|---|
| T1 | `PENDING → RUNNING` | task selected for attempt 1 |
| T2 | `PENDING → SKIPPED` | replan drops it, or abort finalizes the run |
| T3 | `PENDING → CANCELLED` | run cancelled before this task started |
| T4 | `RUNNING → DONE` | reflection `accept` (or reflection disabled) |
| T5 | `RUNNING → RETRYING` | reflection `retry` **and** `retries_left(task) > 0` |
| T6 | `RUNNING → FAILED` | `abort`; or retry **and** replan both exhausted; or `BudgetExceeded` in this task |
| T7 | `RUNNING → CANCELLED` | cancel observed while this task is in-flight (exec or reflect phase) |
| T8 | `RETRYING → RUNNING` | next attempt begins |
| T9 | `RETRYING → CANCELLED` | cancel observed between the retry decision and the next attempt |

### 2.2 Adjacency matrix — proof no invalid transition exists

Rows = from, columns = to. `✓` = legal (edge #), blank = **forbidden**. Terminal
states (`DONE/FAILED/SKIPPED/CANCELLED`) have no outgoing edges by construction.

| from＼to | PENDING | RUNNING | RETRYING | DONE | FAILED | SKIPPED | CANCELLED |
|---|---|---|---|---|---|---|---|
| **PENDING** | — | ✓ T1 | | | | ✓ T2 | ✓ T3 |
| **RUNNING** | | | ✓ T5 | ✓ T4 | ✓ T6 | | ✓ T7 |
| **RETRYING** | | ✓ T8 | | | | | ✓ T9 |
| **DONE** | | | | — | | | |
| **FAILED** | | | | | — | | |
| **SKIPPED** | | | | | | — | |
| **CANCELLED** | | | | | | | — |

**Forbidden transitions and why they cannot occur:**

1. **`RUNNING/RETRYING → SKIPPED`** — an in-flight task always resolves to
   `DONE/FAILED/CANCELLED`; `SKIPPED` is reserved for tasks that *never ran*
   (dropped by replan or abort). The manager only calls `mark_skipped` on tasks
   whose status is `PENDING`. *(Invariant I-5.)*
2. **`PENDING → RUNNING → PENDING`** — no code path returns a task to `PENDING`;
   attempts move forward via `RETRYING`. Indices are never reused (I-6).
3. **`* → PENDING`** — `PENDING` is only an initial state (set at `bulk_create`);
   nothing transitions *into* it.
4. **Any edge out of a terminal state** — finalizers set a terminal status once;
   `RunManager` never re-selects a terminal task (the task loop iterates the
   ordered list once per generation and skips non-`PENDING`).
5. **`RETRYING → DONE/FAILED` directly** — a retry always re-enters `RUNNING`
   first (T8) so the next attempt is a real execution; the verdict on that
   attempt then yields `DONE/FAILED`. This keeps "every judged attempt has an
   executor run" true.
6. **`DONE → RETRYING`** — a completed task is immutable (I-6); reflection is
   never re-run on an accepted task.

Since the matrix enumerates **exactly** the 9 edges the manager can emit (each
guarded), and every other cell is unreachable by the arguments above, the task
FSM admits no invalid transition. ∎

---

## 3. Event timeline — exact emitted sequence per scenario

Notation: one event per line, in `emit()` order (⇒ strictly increasing `seq`).
Executor-internal `thought` / `tool.call` / `tool.result` are shown as
`…executor(attempt=k)…` for brevity; each carries `task_id` + `attempt`.
Baseline M3 order (verified in [manager.py](../../backend/atlas/runtime/manager.py)):
`run.created → run.started → plan.created → (task.started → …executor… →
task.completed)\* → answer.token\* → answer.completed → run.completed`.

### 3.1 Successful task (single attempt, reflection accepts)
```
run.created
run.started
plan.created            {tasks:[t0]}
task.started            {index:0, attempt:1}
…executor(attempt=1)…
task.reflected          {index:0, attempt:1, decision:accept, confidence, reflection_version}
task.completed          {index:0, attempt:1, output}
answer.token …          (per word; skipped if single-task short-circuit)
answer.completed        {text}
run.completed
```

### 3.2 Retry (attempt 1 retry → attempt 2 accept)
```
run.created
run.started
plan.created            {tasks:[t0]}
task.started            {index:0, attempt:1}
…executor(attempt=1)…
task.reflected          {index:0, attempt:1, decision:retry, reason}
task.retrying           {index:0, attempt:2, reason}
…executor(attempt=2)…   (context includes critique)
task.reflected          {index:0, attempt:2, decision:accept}
task.completed          {index:0, attempt:2, output}
answer.completed
run.completed
```

### 3.3 Retry exhaustion (max_retries=2 → 3 attempts, all retry → abort → partial)
```
run.created
run.started
plan.created            {tasks:[t0,t1]}
task.started            {index:0, attempt:1}
…executor(attempt=1)…
task.reflected          {index:0, attempt:1, decision:retry}
task.retrying           {index:0, attempt:2}
…executor(attempt=2)…
task.reflected          {index:0, attempt:2, decision:retry}
task.retrying           {index:0, attempt:3}
…executor(attempt=3)…
task.reflected          {index:0, attempt:3, decision:retry}   (last judgment recorded)
                        (retries_left==0 → escalate; replans_left==0 → ABORT)
task.failed             {index:0, error:"unrecoverable after 3 attempts"}
task.skipped            {index:1, reason:"aborted"}
answer.completed        {text: partial synthesis over completed tasks}   (Amendment 3)
run.failed              {error:"aborted after retry exhaustion"}
```

### 3.4 Replan (task0 accept; task1 replan → new remaining)
```
run.created
run.started
plan.created            {tasks:[t0,t1,t2]}
task.started            {index:0, attempt:1}
…executor(attempt=1)…
task.reflected          {index:0, attempt:1, decision:accept}
task.completed          {index:0}
task.started            {index:1, attempt:1}
…executor(attempt=1)…
task.reflected          {index:1, attempt:1, decision:replan, reason}
                        (replans_left==1 → Planner.replan)
task.skipped            {index:1, reason:"replaced by replan"}
task.skipped            {index:2, reason:"replaced by replan"}
plan.replanned          {generation:1, parent_generation:0, dropped_task_ids:[t1,t2], tasks:[t3,t4]}
task.started            {index:3, attempt:1}     (indices continue past max)
…executor(attempt=1)…
task.reflected          {index:3, attempt:1, decision:accept}
task.completed          {index:3}
task.started            {index:4, attempt:1}
…executor(attempt=1)…
task.reflected          {index:4, attempt:1, decision:accept}
task.completed          {index:4}
answer.completed
run.completed
```

### 3.5 Replan exhaustion (max_replans=1; second replan verdict → abort → partial)
```
… task0 accept … task.completed{0}
task.started            {index:1, attempt:1}
…executor(attempt=1)…
task.reflected          {index:1, attempt:1, decision:replan}
task.skipped            {index:1}, task.skipped{index:2}
plan.replanned          {generation:1, parent_generation:0, tasks:[t3]}
task.started            {index:3, attempt:1}
…executor(attempt=1)…
task.reflected          {index:3, attempt:1, decision:replan}
                        (replans_left==0 → escalate; retries not applicable → ABORT)
task.failed             {index:3, error:"replan budget exhausted"}
answer.completed        {partial}                              (Amendment 3)
run.failed              {error:"aborted after replan exhaustion"}
```

### 3.6 Cancellation during reflection
```
… plan.created …
task.started            {index:1, attempt:1}
…executor(attempt=1)…                             (completes; output recorded)
                        (cancel observed BEFORE the reflection call)
task.cancelled          {index:1, phase:"reflection"}     (Amendment 1)
task.cancelled          {index:2, phase:"pending"}
run.cancelled
```
No `task.reflected` for the in-flight task (its verdict was never produced); the
attempt row persists with a null reflection.

### 3.7 Cancellation during execution
```
… plan.created …
task.started            {index:1, attempt:1}
…executor(attempt=1) partial…                     (cancel observed between ReAct iterations)
task.cancelled          {index:1, phase:"execution"}
task.cancelled          {index:2, phase:"pending"}
run.cancelled
```

### 3.8 Budget exceeded (model calls) — no synthesis (Amendment 4)
```
… plan.created …
task.started            {index:2, attempt:1}
…executor(attempt=1) … BudgetedGateway.charge_model_call() overflows
                        (BudgetExceeded propagates out of Executor.run)
budget.exceeded         {budget:"model_calls", limit:60, used:60, task_id}
task.failed             {index:2, error:"model-call budget exhausted"}
task.skipped            {index:3, reason:"budget exceeded"}
run.failed              {error:"budget exceeded: model_calls"}
                        (NO answer.completed — out of resources)
```
Tool-call budget is identical with `budget:"tool_calls"`; the blocked tool call
produces **no** `tool.result` observation (charge happens before delegation).

### 3.9 Synthesis failure
```
… all tasks accepted … task.completed{last}
                        (Synthesizer.answer raises LLMError/SynthesizerError)
run.failed              {error:"LLM error: …"}
                        (NO answer.completed; tasks remain DONE)
```

---

## 4. Failure matrix

For each failure: does M4 retry? replan? produce a partial answer? what terminal
run state? what events (in order, lifecycle-level)?

| Failure | Retry? | Replan? | Partial answer? | Terminal run state | Emitted (lifecycle) events |
|---|---|---|---|---|---|
| **Planner failure** (`PlannerError`/parse) | No | No | No | `FAILED` | `run.started` → `run.failed` |
| **Executor failure** (`ExecutorError`/`LLMError` in a task) | **Yes** — surfaces as empty/`ERROR:` → pre-check `retry` (≤ budget) | Via escalation if retries exhausted | Yes, if it lands on abort | `FAILED` (or recovers → `DONE`) | `task.started` → `task.reflected(retry)` → `task.retrying` → … → (`task.completed`) or (`task.failed` → `task.skipped`\* → `answer.completed`(partial) → `run.failed`) |
| **Tool failure** (inside a tool) | No new attempt by itself — trapped into a `ToolResult` observation by the registry (unchanged M2/M3) | No | n/a | Depends on task outcome | `tool.call` → `tool.result{ok:false}` (executor continues; may still finish) |
| **Reflection parse failure** (unparseable after repair) | No | No | n/a | continues (`accept`, bias) | `task.reflected{decision:accept, source:degraded, confidence:0.0}` → `task.completed` |
| **Reflection = retry verdict** | **Yes** (≤ `retries_left`) | on exhaustion | on abort | `DONE`/`FAILED` | `task.reflected(retry)` → `task.retrying` → … |
| **Budget exceeded** (model/tool) | No | No | **No** (Amendment 4) | `FAILED` | `budget.exceeded` → `task.failed` → `task.skipped`\* → `run.failed` |
| **Cancellation** | No | No | No | `CANCELLED` | `task.cancelled`\* → `run.cancelled` |
| **Database write failure** | No | No | No | `FAILED` (best-effort) | caught by `_execute`'s `except Exception` → `_finalize_failed` → `run.failed` (if the failing write is itself the finalize, the run row keeps its prior status but the error is logged — see note) |
| **Synthesizer failure** (`LLMError`/parse) | No | No | No | `FAILED` | `run.failed` (no `answer.completed`) |

**Escalation ladder (deterministic).** On a task that cannot be accepted:
`retry (retries_left>0)` → else `replan (replans_left>0)` → else `abort`.
`abort` and retry/replan **exhaustion** run partial synthesis (I-12, Amendment 3);
**budget** exhaustion does not (I-12, Amendment 4).

**DB-write-failure note.** All writes go through repositories inside
`_execute`'s try/except; an unexpected `Exception` (incl. `OperationalError`) is
converted to `run.failed` (I-11). The one irreducible edge — a failure *of the
finalize write itself* — cannot emit a reliable terminal event; M4 logs it and
leaves the ledger authoritative (the run appears non-terminal and is reconciled
on the M6 crash-resume pass). This is an accepted, documented limitation, not a
regression (M3 has the same property).

---

## 5. Budget accounting — exactly what consumes each counter

`RunBudget` has five counters (Amendment 7). **Every** `LLMGateway.complete()`/
`.stream()` increments `model_calls` (the hard cap) via `charge_model_call(cat)`;
the same call increments exactly one *named* category or the *implicit executor*
tally. Tool calls are counted separately.

| Operation | `model_calls` | `planner_calls` | `reflection_calls` | `synthesis_calls` | `tool_calls` |
|---|:---:|:---:|:---:|:---:|:---:|
| `Planner.plan` LLM call | +1 | +1 | | | |
| Planner **JSON-repair** retry call | +1 | +1 | | | |
| `Planner.replan` LLM call (+repairs) | +1 each | +1 each | | | |
| `Reflector` deterministic **pre-check** | — | — | — | — | — |
| `Reflector` LLM verdict call | +1 | | +1 | | |
| Reflection **JSON-repair** retry call | +1 | | +1 | | |
| Reflection resolved by **acceptance bias** (unparseable) | (only the calls actually made) | | (as made) | | |
| `Executor` ReAct model call (per iteration) | +1 | | | | |
| `Executor` tool invocation | | | | | +1 |
| Tool call **blocked** by `BudgetExceeded` | | | | | +1 *(charged, then raises; no tool runs)* |
| `Synthesizer.answer` LLM call | +1 | | | +1 | |
| **Single-task synthesis short-circuit** (no LLM) | — | — | — | — | — |

**Derived identity:** `executor_model_calls = model_calls − planner_calls −
reflection_calls − synthesis_calls`. Executor calls are the implicit remainder;
they need no dedicated counter (Amendment 7 names exactly the five above).

**Charge ordering (critical for determinism):** `charge_*` runs **before** the
wrapped I/O. So a call that trips the cap is *counted and rejected* (it never
reaches the model/tool), which is why a budget-blocked tool call yields **no**
`tool.result` observation (§3.8) and cannot be mistaken for a tool that returned
empty. Only `charge_*` mutates; `snapshot()/remaining()/retries_left()/
replans_left()/would_exceed()` are pure reads (I-8).

**Worked example (nominal 5-task run, reflection on, no retries):**
1 `plan` + Σ executor(≤5 iters×5 tasks ≈ 15–25) + 5 `reflection` + 1 `synth`
≈ **22–32 model calls**, ~10–20 tool calls — comfortably inside the 60/40 caps.
The caps bound pathology, not normal work.

---

## 6. Replay proof — the ledger reconstructs everything

**Claim.** From the ordered event ledger of a run (and nothing else — no live
`RunManager`, no in-memory budget), a consumer can reconstruct: run state, every
task's state, full retry history, every replan, every attempt, and the final
budget tallies.

**Construction (a pure fold over events, ascending `seq`).**

- **Run state.** The last lifecycle event determines it: `run.completed→done`,
  `run.failed→failed`, `run.cancelled→cancelled`; otherwise the newest of
  `run.started`(planning), `plan.created`(running), `plan.replanned`(running),
  `answer.token`(synthesizing) gives the live phase (§1 mapping). Deterministic
  because `seq` is strictly monotonic and single-writer (I-2).
- **Task states.** Fold per `index`: `plan.created`/`plan.replanned` → `PENDING`;
  `task.started` → `RUNNING`; `task.retrying` → `RETRYING` then `RUNNING` on the
  next `task.started`-less attempt boundary (the `attempt` field disambiguates);
  `task.completed` → `DONE`; `task.failed` → `FAILED`; `task.skipped` →
  `SKIPPED`; `task.cancelled` → `CANCELLED`. Because M4 emits a `task.cancelled`
  for **every** cancelled task (Amendment 1), no task state is inferred from
  run-level events — the projection is total.
- **Retry history.** The ordered `task.reflected {index, attempt, decision}` +
  `task.retrying {attempt}` events for an `index` reproduce the exact attempt
  ladder and each verdict; `reflection_version` on each makes historical verdicts
  interpretable after a prompt change (Amendment 6).
- **Replans.** Each `plan.replanned {generation, parent_generation,
  dropped_task_ids, tasks}` records the full lineage; folding them yields the
  plan-evolution tree (I-6 guarantees indices never collide).
- **Attempts.** Every attempt has a `task.reflected` (or, if cancelled before
  judgment, a `task.cancelled`), so the attempt count and per-attempt outcome are
  event-derivable; the `task_attempts` table is a convenience projection, not a
  source of truth (matches ADR-0009 for the plan checklist).
- **Budgets.** `model_calls`/`tool_calls` used = count of the corresponding
  executor/planner/reflection/synthesis and tool events; category tallies =
  counts of `plan.*`, `task.reflected`, `answer.*` producers. A `budget.exceeded`
  event records the exact `{limit, used}` at the trip. Thus final budget state is
  reconstructable without the live `RunBudget`.

**Why it holds:** every state-changing action in `RunManager` emits its event in
the **same** transaction-ordered path as the write (the established single-writer
`emit()` discipline), and M4 adds an event for the *only* previously-implicit
transition (task cancellation). Therefore the ledger is a complete, ordered log
of state transitions ⇒ the fold above is a faithful inverse (Invariant I-1). ∎

*Tested by:* the "Event replay" case (§15 of the RFC) reconstructs task states
from events only and asserts equality with the `tasks`/`task_attempts`
projection — checklist ↔ ledger parity.

---

## 7. Phase review — finalized, PR-sized plan

The original 8 phases were reviewed against the "6–10 independently reviewable,
each compiles/lints/tests-green" target. **One split** was made: old Phase 4
("retry loop") bundled the attempt loop *and* the cancellation/finalizer rework —
two separately-reviewable concerns and the highest-risk change. It is now Phase 5
(retry loop core) + Phase 6 (cancellation + finalizers). Result: **9 phases**,
all within target. Contracts and persistence were also split (Phase 1 vs 2) so a
reviewer sees the additive schema/migration in isolation. Final plan:

| # | Phase | Independently reviewable because… | Green gate |
|---|---|---|---|
| 1 | **Contracts + config** | pure additive types/enums/config; zero behavior change | contract + config unit tests |
| 2 | **Persistence + migration** | schema + repositories, no runtime wiring | attempts CRUD, cascade, migration idempotency |
| 3 | **Reflector** | stateless pure component, tested via `ScriptedGateway` | `test_reflector.py` (all verdicts/precheck/repair/bias) |
| 4 | **Budgets** | `budget.py` + wrapper wiring only; loop unchanged | `test_budget.py` (counting/overflow/not-trapped/inspection) |
| 5 | **Retry loop (core)** | attempt loop + critique + reflection events; cancel unchanged | retry success/exhaustion, reflection-disabled=M3 |
| 6 | **Cancellation + finalizers** | isolates the I-4 "no orphan RUNNING" guarantee + `task.cancelled` | mid-retry/mid-reflection/planning cancel, invariant asserts |
| 7 | **Replan** | `Planner.replan` + generations + escalation retry→replan | replan, replan exhaustion, immutability, monotonic indices |
| 8 | **Abort + budget finalize** | partial-synthesis vs no-synthesis split (I-12) + full ladder | budget (no partial), abort (partial), replay parity |
| 9 | **Frontend + docs + tag** | UI + docs; no backend logic change | typecheck/build; full suite (~114); `v0.4.0` |

**Ordering constraints.** 1→2 (types before rows), 2→3/4 (reflector & budget
consume the contracts), 3+4→5 (loop needs both), 5→6 (finalizers wrap the loop),
6→7 (replan reuses the loop + cancel-safe finalizers), 7→8 (abort closes the
ladder), 8→9 (frontend consumes the finalized event set). Phases 1–4 land **no**
user-visible change (scaffolding), so they can merge ahead of the behavioral
phases with zero risk to M3 behavior. Each phase leaves `main` releasable:
reflection stays *dormant* until Phase 5 wires the loop, and even then
`agent_enable_reflection=false` preserves exact M3 behavior (I-13).

**Revised estimate:** ≈ **3.5–4 days** (was 3–3.5; the extra half-phase of
review rigor and the cancellation split).

---

## 8. Validation verdict

- Run FSM: 9 logical states, 26 legal transitions enumerated, all terminal states
  reachable, only bounded cycles → **consistent**.
- Task FSM: 7 states, 9 edges, adjacency matrix proves no invalid transition →
  **consistent**.
- Event timelines: 9 scenarios fully ordered against the verified M3 baseline →
  **consistent**.
- Failure matrix: 9 failure classes, each with a defined recovery/partial/terminal
  outcome and event trace → **complete**.
- Budget accounting: every operation's counter impact tabulated, charge-ordering
  fixed → **unambiguous**.
- Replay: pure fold reconstructs run/task/retry/replan/attempt/budget state; the
  one previously-implicit transition (task cancel) now has an event → **proven**.
- Phases: 9 PR-sized, each green and independently reviewable → **meets target**.

**No further RFC changes are required beyond the 12 amendments already folded in.**
Implementation may begin at Phase 1.
