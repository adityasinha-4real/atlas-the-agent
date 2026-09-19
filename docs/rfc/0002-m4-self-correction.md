# RFC 0002 — Milestone M4: Self-Correction Agent

- **Status:** **Implemented** (2026-07-10, v0.4.0 — 9 phases, all 12 amendments
  applied; see §21) — architecture validated in the companion
  [0002-m4-architecture-validation.md](0002-m4-architecture-validation.md);
  shipped as milestone [M4](../milestones/M4.md).
- **Author:** ATLAS engineering
- **Date:** 2026-07-09
- **Builds on:** M3 planning agent (v0.3.0), [RFC-0001](0001-m3-planning-agent.md)
- **Spec basis:** `ATLAS-Design-Review-V2.md` §1.3 (reflector), §1.4 (retry/replan),
  §1.5 (budgets), §3 (workflow), §4 (state machine), §9 (M4 row)
- **Target version:** **0.4.0**

---

## 0. TL;DR

Today a run plans a task list, executes each task once, and **fails the whole run
on the first task error** (M3, deliberate). M4 makes the agent *recover*: after
every task attempt a **Reflector** judges the result and chooses
`accept | retry | replan | abort`. Retries re-run a task with critique (≤2/task,
all attempts preserved); a replan re-invokes the planner from the current state
(≤1/run, completed tasks immutable); hard **budgets** (retries, replans, model
calls, tool calls) guarantee termination; and cancellation is defined precisely
at every phase, resolving the M3 "stale RUNNING task on cancel" audit finding.

The change is additive and event-sourced: new task states (`RETRYING`,
`CANCELLED`), new events, a `task_attempts` table, and two new columns on `tasks`.
The M3 executor, planner, synthesizer, context builder, `emit()`, and tool
registry are **reused**; M4 is a new `Reflector`, a `RunBudget`, and orchestration
changes in `RunManager`.

---

## 1. Objectives & success criteria

**Objective.** The agent recovers from recoverable mistakes instead of failing,
within deterministic bounds.

**Success criteria.**
1. After every completed task attempt, a `task.reflected` event records a
   validated decision (`accept | retry | replan | abort`) with a reason and
   confidence.
2. A recoverable task is retried up to `agent_max_retries` (default 2) with the
   prior attempt's output + critique fed back; **all** attempts persist.
3. At most `agent_max_replans` (default 1) replans per run; a replan keeps
   completed tasks immutable and replaces only the remaining ones.
4. Budgets (retries/task, replans/run, total model calls, total tool calls) are
   enforced; **exhaustion is deterministic** → `budget.exceeded` → run `FAILED`.
5. Cancellation is defined and tested during planning, execution, reflection, and
   synthesis, with well-defined task states (no task left `RUNNING` on a terminal
   run — closes the M3 SHOULD-FIX).
6. `abort` (or exhausted recovery) produces a **graceful partial answer**
   synthesized from completed tasks, not a bare crash.
7. Deterministic FakeLLM tests cover every path (§11); M1–M3 tests stay green.
   Target **~28 new tests, ~114 total**.
8. Reflection can be disabled (`agent_enable_reflection=false`) → exact M3
   behavior (backward-compatible kill switch).
9. `make test-m4` / `make demo-m4`; docs + ADRs updated.

**Explicit non-goals (later milestones).** Episodic memory / recall-at-plan-time /
eval harness (M5); crash-resume, `code_sandbox`, `PAUSED` (M6). Reflection uses
only the current run's state — no cross-run learning (that is M5).

---

## 2. Overall architecture

M4 inserts a **Reflect → Decide → (Retry | Replan | Abort | Continue)** control
loop around the M3 per-task execution, governed by a per-run **budget meter**.

```
                 ┌──────────────────────────────────────────────────────────┐
 goal ─►RunManager│ PLANNING  → Planner.plan(goal)  [budgeted]               │
                 │              │                                             │
                 │ RUNNING: for each pending task (by index):                │
                 │   ┌─ attempt = 1..(max_retries+1) ─────────────────────┐  │
                 │   │ ContextBuilder.build(ctx, attempt, critique)        │  │
                 │   │ Executor.run(messages) [budgeted]  → output         │  │
                 │   │ Reflector.reflect(task, output, attempt) [budgeted] │  │
                 │   │   → accept | retry | replan | abort                 │  │
                 │   └─────────────────────────────────────────────────────┘  │
                 │      accept → task DONE, next task                        │
                 │      retry  → task RETRYING → next attempt (if budget)    │
                 │      replan → Planner.replan(state) → swap remaining      │
                 │      abort  → remaining SKIPPED → graceful partial        │
                 │                                                            │
                 │ SYNTHESIS: Synthesizer.answer(goal, done_outputs) [budgeted]│
                 │ DONE  (or FAILED on abort/budget/unrecoverable)           │
                 └──────────────────────────────────────────────────────────┘
        RunBudget (model_calls, tool_calls, retries/task, replans/run) — checked
        centrally by BudgetedGateway + BudgetedToolRegistry wrappers.
```

New module: `atlas/agent/reflector.py`. New runtime helper:
`atlas/runtime/budget.py`. Everything else is orchestration in
`runtime/manager.py` plus additive contracts/persistence/events/config.

---

## 3. Component interaction

```
RunManager (per run)
  ├─ RunBudget(model_calls, tool_calls, per-task retries, replans)
  ├─ BudgetedGateway(gateway, budget)         # wraps LLMGateway.complete/stream
  ├─ BudgetedToolRegistry(registry, budget)   # wraps ToolRegistry.execute
  │
  ├─ Planner(budgeted_gateway, registry, settings)          .plan / .replan
  ├─ ContextBuilder(registry, settings)                     .build(TaskContext)
  ├─ Executor(budgeted_gateway, budgeted_registry, emit, s) .run(messages, task_id, attempt)
  ├─ Reflector(budgeted_gateway, settings)                  .reflect(...) → ReflectionResult
  └─ Synthesizer(budgeted_gateway, settings)                .answer(...)

  Persistence: RunRepository, TaskRepository (+ attempts), TaskAttemptRepository
  Events: emit(plan.created | task.started | task.reflected | task.retrying |
               task.completed | task.failed | task.skipped | plan.replanned |
               budget.exceeded | run.*)
```

Unchanged seams: `emit()` single-writer `seq`, `EventHub`/WS, repositories, the
executor loop body, the tolerant `jsonio` parser. `BudgetExceeded` propagates
cleanly through the executor (it is **not** trapped as a tool observation — see
§4/§8) and is caught by `RunManager`.

---

## 4. Runtime flow (per task)

```
run_tasks(goal, tasks):
  for task in ordered_pending(tasks):
     if cancelled(): finalize_cancelled(); return
     attempt = 1
     critique = None
     emit(task.started {task_id, index, attempt=1})
     while True:
        mark_running(task); persist attempt row (attempt, RUNNING)
        ctx = TaskContext(goal, task, prior_outputs, attempt, previous_attempts)
        messages = ContextBuilder.build(ctx)          # includes critique if any
        output = Executor.run(messages, task_id, attempt)   # budgeted
        record_attempt(task, attempt, output)

        if not reflection_enabled:                    # M3 behavior
            decision = ACCEPT
        else:
            if cancelled(): finalize_cancelled(); return
            decision = Reflector.reflect(goal, task, output, attempt, prior=…) # budgeted
        emit(task.reflected {task_id, index, attempt, decision, reason, confidence})
        persist reflection onto attempt row

        match decision:
          ACCEPT:
             mark_done(task, output); emit(task.completed); prior_outputs += output
             break                                    # next task
          RETRY:
             if budget.retries_left(task):
                critique = build_critique(output, reason)
                attempt += 1; mark_retrying(task)
                emit(task.retrying {task_id, index, attempt, reason}); continue
             else: escalate()                         # → REPLAN or ABORT (below)
          REPLAN:
             if budget.replans_left(): do_replan(...); return   # re-enter run_tasks
             else: escalate()
          ABORT:
             graceful_abort(reason); return
```

`escalate()` is a deterministic ladder: **retry-exhausted → replan (if a replan
remains) → else abort**. `graceful_abort()` marks the current task `FAILED`, all
later `PENDING` tasks `SKIPPED`, then runs SYNTHESIS over completed outputs and
finalizes the run `FAILED` **with a partial answer persisted** (§ ADR-0014).

---

## 5. Task state machine

M4 adds `RETRYING` and `CANCELLED` (both already requested); `SKIPPED` (declared
in M3) is now exercised.

```
                 ┌─────────────► SKIPPED   (dropped by replan, or after abort)
                 │
   PENDING ──► RUNNING ──► DONE           (reflection: accept)
      │           │  │
      │           │  └──► FAILED          (unrecoverable: abort, or retry+replan
      │           │                        budget exhausted, or budget.exceeded)
      │           │
      │           └──► RETRYING ──► RUNNING   (reflection: retry, budget allows)
      │
      └──────────────────► CANCELLED      (run cancelled before/while this task ran)
```

| Transition | Trigger |
|---|---|
| `PENDING → RUNNING` | Task selected for its (first) attempt. |
| `RUNNING → DONE` | Reflection `accept` (or reflection disabled). |
| `RUNNING → RETRYING` | Reflection `retry` **and** `retries_left(task) > 0`. |
| `RETRYING → RUNNING` | Next attempt begins. |
| `RUNNING → FAILED` | `abort`; or retry+replan both exhausted; or `BudgetExceeded` during this task. |
| `RUNNING → CANCELLED` | Cancellation observed while this task is the in-flight one. |
| `PENDING → CANCELLED` | Run cancelled; this task had not started. |
| `PENDING → SKIPPED` | A replan drops this task, or an abort finalizes the run. |
| `RUNNING/RETRYING → SKIPPED` | *(not allowed)* — an in-flight task resolves to DONE/FAILED/CANCELLED, never SKIPPED. |

Terminal task states: `DONE`, `FAILED`, `SKIPPED`, `CANCELLED`. **Invariant: when a
run reaches a terminal state, no task is left `RUNNING`/`RETRYING`/`PENDING`** —
enforced by the finalizers (fixes M3 audit SHOULD-FIX).

**Run FSM** is unchanged in shape (`CREATED → PLANNING → RUNNING → DONE`, +`FAILED`,
`CANCELLED`); `PLANNING` may be **re-entered once** on replan (documented, not a new
state). `PAUSED` remains reserved (M6).

---

## 6. Event model

All additive; envelope shape (`run_id, seq, type, payload, ts`) unchanged.
Event-sourcing preserved: the checklist, attempt history, and replay are all
reconstructable from the ledger.

**Naming decision (Amendment 2 — approved).** The reserved
`EventType.REFLECTION = "reflection"` and `REPLAN = "replan"` were placeholders,
never emitted or persisted. M4 **renames them to the established `subject.verb`
convention** — `TASK_REFLECTED = "task.reflected"` and
`PLAN_REPLANNED = "plan.replanned"` — and adds four more (`task.retrying`,
`task.skipped`, `task.cancelled`, `budget.exceeded`). Zero data impact (nothing
ever wrote the old values).

| Event | When | Payload |
|---|---|---|
| `task.reflected` *(reserved→renamed)* | after each attempt is judged | `{task_id, index, attempt, decision, reason, confidence, reflection_version}` |
| `task.retrying` *(new)* | before a retry attempt starts | `{task_id, index, attempt, reason}` (`attempt` = upcoming) |
| `task.skipped` *(new)* | task dropped (replan/abort) | `{task_id, index, reason}` |
| `task.cancelled` *(new — Amendment 1)* | a non-terminal task is cancelled | `{task_id, index, phase}` (`phase` = `execution`\|`reflection`\|`pending`) |
| `plan.replanned` *(reserved→renamed)* | after a replan | `{generation, parent_generation, dropped_task_ids:[…], tasks:[{id,index,description,success_criteria,suggested_tool}]}` |
| `budget.exceeded` *(new)* | any budget hit | `{budget:"model_calls"\|"tool_calls"\|"planner_calls"\|"reflection_calls"\|"synthesis_calls"\|"retries"\|"replans", limit, used, task_id?}` |

**Reused/extended:** `task.started` gains `attempt` (always `1` at first start);
executor `thought`/`tool.call`/`tool.result` payloads gain optional `attempt`
(alongside the existing `task_id`) so the UI can group events per attempt. All
additive — old consumers ignore unknown keys.

**Canonical happy/failure/replan/cancel streams** (abbreviated) are enumerated in
§9. A `task.reflected` is emitted for **every** attempt (accept, retry, replan, and
abort alike), so the trajectory is fully auditable.

---

## 7. Reflection prompt & the Reflector

**Statelessness (Amendment 10 — approved).** The `Reflector` is **completely
stateless**: `reflect(...)` is a pure function of its arguments (goal, task,
output, attempt, prior attempts) and the injected gateway/settings. It holds no
per-run or per-task mutable fields, keeps no history, and mutates nothing — all
attempt/critique history is passed in by `RunManager` and all counting lives in
`RunBudget`. This makes reflection trivially testable, safe to call concurrently,
and replay-neutral. Every emitted `task.reflected` carries a
`reflection_version` (Amendment 6) = the constant `REFLECTION_PROMPT_VERSION`
(bumped whenever the prompt/parse semantics change), so historical reflections
remain interpretable after a prompt revision.

### 7.1 Reflector algorithm (`atlas/agent/reflector.py`)

```
reflect(goal, task, output, attempt, prior_attempts) -> ReflectionResult:
    # 1) Deterministic pre-check (no LLM call) — cheap, high-precision gates:
    if output is empty or output.startswith("ERROR:"):
        return ReflectionResult(RETRY, "empty/error output", confidence=0.9, source="precheck")
    if attempt >= max_retries+1 and prior all failed:
        return ReflectionResult(ACCEPT, "attempts exhausted; accept best effort",
                                confidence=0.3, source="precheck")   # escalation handled by RunManager
    # 2) LLM verdict (budgeted), tolerant parse + repair ≤ agent_repair_attempts:
    raw = gateway.complete(reflection_messages(goal, task, output))
    result = parse_reflection(raw)              # jsonio + schema validation
    if result is None:                          # unparseable after repair
        return ReflectionResult(ACCEPT, "reflection unparseable; accepting (bias)",
                                confidence=0.0, source="degraded")   # ACCEPTANCE BIAS
    # 3) Confidence gate (optional): downgrade low-confidence retries to accept
    if result.decision in (RETRY, REPLAN) and result.confidence < reflection_min_confidence:
        result = result.as_accept("low confidence; accepting to avoid churn")
    return result
```

**Acceptance bias** (consistent with ADR-0008): when the model cannot produce a
valid verdict, or is under-confident, ATLAS **accepts** rather than looping — small
models over-criticize, and unbounded self-doubt is worse than a mediocre answer
the Reflector (or M4's budgets) already bound.

### 7.2 Prompt (`agent/prompts.py :: build_reflection_prompt`)

System prompt (versioned template):

```
You are ATLAS's reflector. Judge whether a task's result satisfies its success
criteria, and decide the single best next action. Respond with EXACTLY ONE JSON
object and nothing else:

  {"decision": "accept|retry|replan|abort",
   "reason": "<one concise sentence>",
   "confidence": <number 0.0–1.0>}

Definitions:
- accept  — the result meets the success criteria; move on.
- retry   — the same task can plausibly succeed with another attempt; say what to fix.
- replan  — the overall plan is wrong for the goal; the remaining steps should change.
- abort   — the goal cannot be achieved with the available tools; stop.

Rules:
- Prefer "accept" when the result is good enough; do not demand perfection.
- Choose "retry" only for a fixable, task-local problem.
- Choose "replan" only when later steps (not this one) are the problem.
- "confidence" is your certainty in the decision, 0.0–1.0.
```

User turn: goal, task description + success_criteria, the attempt's output
(truncated to `context_prior_output_chars`), the attempt number, and — on retries
— the previous attempts' reasons.

### 7.3 Validation rules (`parse_reflection`)

- Extract JSON tolerantly (`jsonio.extract_json`), `json.loads`, require an object.
- `decision`: required; lowercased; must be one of `{accept, retry, replan, abort}`;
  otherwise a parse failure (→ repair, then acceptance bias).
- `reason`: optional string; default `""`; truncated to ~300 chars.
- `confidence`: optional; coerced to float; clamped to `[0.0, 1.0]`; default `0.5`
  if missing/non-numeric.
- Unknown keys ignored (`extra="ignore"`).

New contract `ReflectionResult` (Pydantic, in `agent/schemas.py`) with a
`ReflectionDecision` `StrEnum` (`ACCEPT/RETRY/REPLAN/ABORT`), plus a `source`
field (`precheck|llm|degraded`) and `reflection_version: int`. `ReflectionResult`
is a frozen value object — it carries no reference back to run/task state,
reinforcing the stateless-Reflector invariant.

---

## 8. Failure budget (`atlas/runtime/budget.py`)

A per-run **`RunBudget`** dataclass counts and bounds these quantities.
**Amendment 7 (approved):** category counters are kept **separately** from the
global `model_calls` cap, so a run's LLM spend is attributable by role.

| Counter | Config cap | Scope | Enforced as a hard cap? | On exceed |
|---|---|---|---|---|
| `model_calls` (total LLM) | `agent_max_model_calls` (60) | per run | **Yes** | `BudgetExceeded("model_calls")` |
| `tool_calls` | `agent_max_tool_calls` (40) | per run | **Yes** | `BudgetExceeded("tool_calls")` |
| `planner_calls` | *(no separate cap)* | per run | No — observability only | — |
| `reflection_calls` | *(no separate cap)* | per run | No — observability only | — |
| `synthesis_calls` | *(no separate cap)* | per run | No — observability only | — |
| retries per task | `agent_max_retries` (2) | per task | Yes (control-flow) | escalate (retry→replan→abort) |
| replans per run | `agent_max_replans` (1) | per run | Yes (control-flow) | escalate (replan→abort) |

Every LLM call increments **both** `model_calls` **and** exactly one category
counter (`planner_calls`, `reflection_calls`, `synthesis_calls`, or the implicit
executor category — see the accounting table in the validation doc §5). Only
`model_calls` and `tool_calls` are *hard caps*; the category counters exist for
attribution, `budget.exceeded` diagnostics, and eval metrics (M5). This keeps a
single global ceiling while still answering "where did the calls go?".

**Enforcement is central**, via thin wrappers created per run:
- `BudgetedGateway(inner, budget, category)` — every `complete()`/`stream()`
  calls `budget.charge_model_call(category)` **before** delegating; that method
  bumps `model_calls` + the category counter, then raises `BudgetExceeded` (a
  plain `Exception`, not `LLMError`, so it is not double-handled) if the global
  cap is now exceeded. Each component receives a wrapper bound to its category:
  Planner→`planner`, Reflector→`reflection`, Synthesizer→`synthesis`,
  Executor→`executor`. One counting site, no threading through call sites.
- `BudgetedToolRegistry(inner, budget)` — `execute()` calls
  `budget.charge_tool_call()` **before** delegating to the real registry. Because
  the check is *outside* the real registry's try/except, `BudgetExceeded`
  propagates out of `Executor.run` (it is **not** trapped into a `ToolResult`
  observation) and is caught by `RunManager`.

`retries`/`replans` are checked explicitly in `RunManager` (not via wrappers)
because they gate control-flow decisions, not I/O.

**Inspection methods (Amendment 11 — approved).** `RunBudget` exposes read-only
inspection so the manager, tests, and the (M5) eval harness can reason about
remaining headroom without mutation:
- `snapshot() -> BudgetSnapshot` — an immutable copy of all seven counters + caps.
- `remaining("model_calls" | "tool_calls") -> int`.
- `retries_left(task_id) -> int`, `replans_left() -> int`.
- `would_exceed(category) -> bool` — non-mutating pre-check.
These are pure reads; only the `charge_*` methods mutate, keeping a single write
path for counting (mirrors the single-writer discipline).

**Deterministic exhaustion.** On `BudgetExceeded`, `RunManager`:
1. emits `budget.exceeded {budget, limit, used, task_id?}`,
2. marks the in-flight task `FAILED` and remaining `PENDING` tasks `SKIPPED`,
3. finalizes the run `FAILED` with a clear error (no partial synthesis for a
   model/tool budget hit — the run is out of resources; retry/replan exhaustion
   *does* get a graceful partial via the abort path). See Open Question Q3.

Budgets are generous defaults chosen so a normal 5-task run (≈1 plan + 5×(≤5 iters)
+ 5 reflections + 1 synth ≈ 30–40 model calls) fits comfortably; they exist to
bound pathological loops, not normal work (§13 performance).

---

## 9. Sequence diagrams

Legend: `E`=emit event, columns are RunManager → (Executor|Reflector|Planner) →
Gateway/Registry.

### 9.1 Successful retry
```
E plan.created
E task.started {attempt:1}
  Executor.run ── model×N, tool×M ─► output₁ (poor)
E task.reflected {attempt:1, decision:retry, reason:"missing figure"}
E task.retrying {attempt:2}
  Executor.run (ctx += critique) ── model×N ─► output₂ (good)
E task.reflected {attempt:2, decision:accept}
E task.completed {attempt:2, output:output₂}
… next tasks …
E answer.completed ; E run.completed        [run DONE]
```

### 9.2 Failed retry (retry exhaustion → abort/partial)
```
E task.started {attempt:1}          → reflected:retry → E task.retrying{2}
E …               {attempt:2}       → reflected:retry → retries_left==0 → escalate
   escalate: replans_left==0 → ABORT
E task.reflected {attempt:2, decision:retry}     (last judgment recorded)
E task.failed {task_id, index, error:"unrecoverable after 3 attempts"}
E task.skipped × (remaining)                     (graceful abort)
E answer.completed {partial synthesis}           (best-effort over completed tasks)
E run.failed {error:"aborted after retry exhaustion"}      [run FAILED, partial answer]
```

### 9.3 Replan
```
E task.started{0} … E task.completed{0}          (task 0 accepted)
E task.started{1} … Executor.run → output
E task.reflected {index:1, decision:replan, reason:"wrong approach for remaining"}
   replans_left==1 → Planner.replan(goal, completed=[t0], failed_ctx=t1)   [budgeted]
E task.skipped {t1}, task.skipped {t2 …}          (old remaining dropped)
E plan.replanned {generation:1, dropped_task_ids:[t1,t2], tasks:[t3,t4]}   (new indices ≥ max+1)
   continue run_tasks over the new remaining (t3, t4)
E task.started{3} … E run.completed                        [run DONE]
```

### 9.4 Budget exceeded (model calls)
```
E task.started{2} … Executor.run → BudgetedGateway.charge_model_call() overflows
   raise BudgetExceeded("model_calls")  ─► propagates out of Executor.run
E budget.exceeded {budget:"model_calls", limit:60, used:60, task_id}
E task.failed {task_id, error:"model-call budget exhausted"}
E task.skipped × remaining
E run.failed {error:"budget exceeded: model_calls"}         [run FAILED, no partial]
```

### 9.5 User cancellation (mid-reflection)
```
E task.started{1} … Executor.run → output
   cancel signalled
   Reflector phase: cancel checked BEFORE the reflection call → observed
E task.cancelled {index:1, phase:"reflection"}   (in-flight task → CANCELLED)
E task.cancelled {index:2, phase:"pending"}       (each remaining non-terminal task)
E run.cancelled                                             [run CANCELLED]
```
(Amendment 1 — approved: a dedicated `task.cancelled` event is emitted for every
non-terminal task at cancellation, in addition to the task's `CANCELLED` **status**
in the `tasks` table. This makes the cancel boundary explicit in the ledger and
keeps replay purely event-derived without inferring from `run.cancelled` + the
last `task.started`.)

---

## 10. Cancellation semantics (per phase)

The per-run `asyncio.Event` is checked at these points; on observe → run
`CANCELLED` and **all non-terminal tasks are marked `CANCELLED`** (in-flight and
pending), each emitting a `task.cancelled {phase}` event (Amendment 1); completed
tasks keep `DONE`. No hard `task.cancel()`, so finalization writes always run.

| Phase | Check points | In-flight task result |
|---|---|---|
| Planning | before & after `Planner.plan` | (no tasks yet) → `CANCELLED` run, none persisted |
| Execution | between ReAct iterations (existing), before each attempt | current task → `CANCELLED` |
| Reflection | before & after `Reflector.reflect` | current task → `CANCELLED` (attempt recorded, unjudged) |
| Between tasks | top of the task loop (existing) | next task never starts → `CANCELLED` |
| Replan | before & after `Planner.replan` | remaining → `CANCELLED` |
| Synthesis | before & between answer chunks (existing) | n/a (tasks already terminal) |

A long token-less model call (plan/reflect/synth) still delays observation
(documented limitation; the model-call budget is the hard backstop). This section
also **closes the M3 audit SHOULD-FIX**: an interrupted task can no longer be left
`RUNNING` on a terminal run.

---

## 11. Data model & persistence

### 11.1 New table `task_attempts`
Preserves every attempt (criterion 2) and its reflection.

| Column | Type | Notes |
|---|---|---|
| `attempt_id` | str PK | stable opaque id `{task_id}#{attempt_number}` (Amendment 8) |
| `task_id` | FK → tasks.id (CASCADE) | |
| `run_id` | FK → runs.id (CASCADE) | denormalized for queries (M5 evals) |
| `attempt_number` | int | 1-based ordinal; `UNIQUE(task_id, attempt_number)` (Amendment 8) |
| `output` | Text \| null | this attempt's result |
| `error` | Text \| null | executor error, if any |
| `reflection_decision` | str \| null | `accept/retry/replan/abort` |
| `reflection_reason` | Text \| null | |
| `reflection_confidence` | float \| null | |
| `reflection_source` | str \| null | `precheck/llm/degraded` |
| `reflection_version` | int \| null | prompt/parse version at judgement time (Amendment 6) |
| `created_at` | datetime(tz) | |

**Amendment 8 (approved):** `attempt_id` (stable PK, safe to reference from events
/ future FKs) is persisted **in addition to** `attempt_number` (the 1-based
ordinal used for ordering and the `UNIQUE` constraint). Index on
`(task_id, attempt_number)`; cascade-deleted with the task/run.

### 11.2 New columns on `tasks`
| Column | Type | Default | Notes |
|---|---|---|---|
| `attempt_count` | int | `0` | denormalized latest attempt (query convenience) |
| `replan_generation` | int | `0` | plan generation this task belongs to |
| `parent_generation` | int \| null | `null` | generation this task's plan was replanned *from* (Amendment 9); `null` for the original plan (gen 0) |

**Amendment 9 (approved):** `parent_generation` records replan lineage on every
task produced by a replan (the generation it descended from), and is mirrored in
the `plan.replanned` event payload. Generation 0 (the original plan) has
`parent_generation = null`. This lets the History/eval views reconstruct the full
plan-evolution tree, not just the latest generation.

`TaskView`/`TaskContext` gain `attempt`/`attempt_count` and `previous_attempts`
(additive to the frozen contracts).

### 11.3 Migration strategy
- `task_attempts` is created by `Base.metadata.create_all` (additive, no-op if
  present) — no migration needed.
- The two **new `tasks` columns require `ALTER TABLE`** (SQLite `create_all` does
  not alter existing tables). M4 adds a tiny, dependency-free, idempotent step in
  `Database.create_all`: `_apply_light_migrations(conn)` reads
  `PRAGMA table_info(tasks)` and issues `ALTER TABLE tasks ADD COLUMN …` only for
  missing columns (both are nullable/defaulted, so back-fill is trivial). This is
  deterministic and safe on existing v0.3.0 databases; **no data loss**. Full
  Alembic remains deferred until Postgres (§7 spec, ADR-0007). Documented as
  ADR-0015.
- Task **index numbering stays monotonic per run** (never reused). A replan
  appends new tasks at `index > max(existing index)`, so `UNIQUE(run_id, index)`
  holds and the `{run_id}:{index}` id scheme is preserved without rewriting rows.

New repository work: `TaskAttemptRepository` (create/list per task), plus
`TaskRepository` gains `mark_retrying`, `mark_cancelled`, `mark_skipped`,
`bulk_skip(remaining)`, and `set_generation`.

---

## 12. API changes

Minimal and additive.
- **`GET /runs/{id}/tasks`** — `TaskView` now includes `attempt_count` and
  `replan_generation` (additive fields; existing consumers unaffected).
- **`GET /runs/{id}/tasks/{index}/attempts` → `list[TaskAttemptView]`** —
  **deferred to M5** (History/evals are the real consumer; the live UI
  reconstructs attempts from events, ADR-0009 pattern). Listed here only so the
  `TaskAttemptView` contract is designed with it in mind.
- `GET /runs/{id}`, `/runs`, events, cancel, WS: unchanged.

---

## 13. Prompt / template changes (`agent/prompts.py`)

1. **`build_reflection_prompt()`** (§7.2) — new.
2. **`build_critique(previous_output, reason)`** — new; renders the retry critique
   block the Context Builder injects on attempts ≥ 2 ("Your previous attempt
   produced: … It was judged insufficient because: … Fix this and try again.").
3. **Replan prompt** — extends the planner prompt with a "given these completed
   tasks and their outputs, plan the REMAINING work to reach the goal; do not
   repeat completed work" preamble (`build_replan_prompt(...)`).
4. Context Builder gains an optional critique section (attempt > 1). Envelope and
   planner base rules unchanged.

---

## 14. Configuration (all new values)

| Env var | Field | Default | Bounds | Purpose |
|---|---|---|---|---|
| `ATLAS_AGENT_ENABLE_REFLECTION` | `agent_enable_reflection` | `true` | bool | Master switch; `false` = exact M3 behavior. |
| `ATLAS_AGENT_MAX_RETRIES` | `agent_max_retries` | `2` | `0–5` | Retries per task (attempts = retries+1). |
| `ATLAS_AGENT_MAX_REPLANS` | `agent_max_replans` | `1` | `0–3` | Replans per run. |
| `ATLAS_AGENT_MAX_MODEL_CALLS` | `agent_max_model_calls` | `60` | `1–1000` | Hard cap on LLM calls/run. |
| `ATLAS_AGENT_MAX_TOOL_CALLS` | `agent_max_tool_calls` | `40` | `1–1000` | Hard cap on tool calls/run. |
| `ATLAS_AGENT_REFLECTION_MIN_CONFIDENCE` | `agent_reflection_min_confidence` | `0.0` | `0.0–1.0` | Below this, `retry/replan` downgrade to `accept` (0 = disabled). |

Reuses existing `agent_repair_attempts` for reflection JSON repair and
`context_prior_output_chars` for attempt/critique truncation. All documented in
`.env.example` and the README config table.

---

## 15. Testing strategy (`make test-m4`)

Deterministic via `ScriptedGateway`. Reflection makes call order explicit: per
attempt = *(executor turns…)* then *(one reflection turn)*; a helper
`_reflect(decision, reason, confidence)` builds the reflection JSON. New/updated
files: `test_reflector.py`, `test_budget.py`, `test_runtime_m4.py`,
`test_persistence.py` (attempts), plus small updates to `test_runtime_m3.py` /
`test_runtime_m2.py` scripts (they run with `agent_enable_reflection=false` to stay
byte-for-byte M3, or gain trailing reflection turns — see Migration §17).

| Scenario | Script sketch & assertions |
|---|---|
| **Successful retry** | plan(1 task) → exec(finish "bad") → reflect(retry) → exec(finish "good") → reflect(accept). Assert 2 attempts persisted, `task.retrying` emitted, task DONE, answer="good". |
| **Retry exhaustion** | reflect(retry)×3 with `max_retries=2` → escalate→abort. Assert `task.failed` + remaining `task.skipped` + partial `answer.completed` + run FAILED. |
| **Replan** | task0 accept; task1 reflect(replan) → planner replan → `plan.replanned` (new indices) → new tasks run → DONE. Assert dropped tasks `SKIPPED`, `replan_generation==1`, immutable task0. |
| **Replan exhaustion** | reflect(replan) twice with `max_replans=1` → 2nd replan escalates to abort. Assert one `plan.replanned`, then graceful partial + FAILED. |
| **Reflection parse repair** | reflect returns junk then valid JSON (`repair_attempts=2`). Assert recovery; and a junk-only variant → acceptance bias (task DONE, `source=degraded`). |
| **Budget: model calls** | `max_model_calls=3`; assert `BudgetExceeded` → `budget.exceeded{model_calls}` → run FAILED, no partial. |
| **Budget: tool calls** | `max_tool_calls=1`; second tool call raises → `budget.exceeded{tool_calls}`; observation is **not** produced for the blocked call. |
| **Mid-reflection cancellation** | cancel set after the executor turn (gateway side-effect) → reflection skipped → task `CANCELLED`, remaining `CANCELLED`, run CANCELLED. |
| **Mid-retry cancellation** | cancel set during attempt 2's executor loop → task `CANCELLED`; assert no task left `RUNNING`. |
| **Event replay** | full retry+replan run → reconstruct task states from events only; assert equals the `tasks`/`task_attempts` projection (checklist ↔ ledger parity). |
| **Reflection disabled** | `agent_enable_reflection=false` → zero reflection calls, exact M3 sequence. |
| **Deterministic pre-check** | empty/`ERROR:` output → retry without an LLM reflection call (assert gateway not consulted for that reflection). |

**Persistence tests:** attempts bulk create + ordered read; cascade delete with
task/run; light-migration idempotency on a pre-seeded v0.3.0-shaped DB.

---

## 16. Files created / modified

**Created (backend)**
- `atlas/agent/reflector.py` — `Reflector`, `ReflectionResult` handling.
- `atlas/runtime/budget.py` — `RunBudget`, `BudgetExceeded`, `BudgetedGateway`,
  `BudgetedToolRegistry`.
- `tests/test_reflector.py`, `tests/test_budget.py`, `tests/test_runtime_m4.py`.
- `docs/milestones/M4.md`, ADRs (§18).

**Created (frontend)**
- `components/AttemptTrail.tsx` (or extend `PlanChecklist`) — per-task attempt/
  reflection/retry badges.

**Modified (backend)**
- `agent/schemas.py` — `ReflectionDecision`, `ReflectionResult`, `TaskAttemptView`;
  `TaskContext`/`TaskView` gain `attempt`/`previous_attempts`/`attempt_count`/
  `replan_generation` (additive).
- `agent/planner.py` — add `replan(goal, completed, remaining_context)`.
- `agent/context.py` — inject critique on attempts ≥ 2.
- `agent/prompts.py` — reflection, critique, replan templates.
- `agent/executor.py` — accept/emit `attempt` in event payloads (small, additive).
- `events/types.py` — rename `REFLECTION→TASK_REFLECTED`, `REPLAN→PLAN_REPLANNED`;
  add `TASK_RETRYING`, `TASK_SKIPPED`, `BUDGET_EXCEEDED`.
- `persistence/models.py` — `TaskAttemptRow`; `tasks.attempt_count`,
  `tasks.replan_generation`.
- `persistence/repositories.py` — `TaskAttemptRepository`; `TaskRepository`
  transitions (`mark_retrying/cancelled/skipped`, `bulk_skip`, `set_generation`).
- `persistence/database.py` — `_apply_light_migrations`.
- `runtime/manager.py` — the reflect/retry/replan/abort loop, budget wiring,
  cancellation-state cleanup, finalizers.
- `core/config.py` — the six new settings (§14).
- `api/routes.py` — `TaskView` fields; optional attempts endpoint.
- `__init__.py` + `pyproject.toml` — **v0.4.0**.

**Modified (frontend/docs)**
- `lib/types.ts`, `lib/useRunStream.ts` (attempt/reflection/retry/skip/replan
  handling), `components/{RunPage,EventFeed,PlanChecklist}.tsx`.
- `Makefile` (`test-m4`, `demo-m4`), README(s), CHANGELOG, TODO, ARCHITECTURE,
  ADR index, `.env.example`, memory.

---

## 17. Migration strategy from M3

1. **Backward-compatible default is NOT M3** — M4 turns reflection on by default.
   To keep M1–M3 unit tests untouched, they construct settings with
   `agent_enable_reflection=false` (a one-line fixture change), preserving their
   exact scripts. New reflection behavior is proven by M4 tests. *(Alternative:
   default reflection off and flip in a later minor — rejected; M4's whole point is
   self-correction on by default.)*
2. **Contracts additive** — new enums/fields/events; the two reserved event
   members are renamed (never emitted, zero data impact).
3. **Schema** — `task_attempts` via `create_all`; `tasks` columns via the light
   migration (idempotent, safe on existing DBs).
4. **Executor** — signature stable (`run(run_id, messages, cancel, *, task_id,
   attempt=1)`); `attempt` is an optional additive kwarg.
5. **Frontend** — checklist gains retry/skip/reflection affordances; existing
   panels unchanged.
6. **Version → 0.4.0**; tag `v0.4.0` at the end (on approval, per workflow).

---

## 18. ADRs to write before implementation

- **ADR-0011 — Reflection = deterministic pre-check + bounded LLM verdict with
  acceptance bias.** Reconciles the V2 3-verdict reflector with M4's 4th verdict
  (`replan`); records the acceptance-bias-on-uncertainty rule (kin to ADR-0008).
- **ADR-0012 — Failure budgets & deterministic exhaustion.** Per-run `RunBudget`
  with **separate category counters** (planner/reflection/synthesis) plus the two
  hard caps (model/tool calls), enforced centrally via gateway/registry wrappers;
  read-only inspection methods; `BudgetExceeded` bypasses the tool-observation
  trap; exhaustion is a clean terminal failure with no partial synthesis.
- **ADR-0013 — Replan from current state with immutable completed tasks.** Extends
  ADR-0003 (planner re-invocation over a replanner module): replan reuses the
  planner, keeps completed tasks/indices immutable, appends a new generation, and
  `SKIP`s dropped tasks.
- **ADR-0014 — Graceful abort yields a partial synthesized answer.** Abort / recovery
  exhaustion synthesizes over completed tasks and finalizes `FAILED` *with* a
  persisted partial answer (vs. a bare crash). Budget hits are the exception (no
  partial — out of resources).
- **ADR-0015 — Light, idempotent SQLite column migrations.** `ALTER TABLE … ADD
  COLUMN` guarded by `PRAGMA table_info`, until Alembic (still deferred).

*(ADR-0011..0015. The event-rename is small enough to record inline in ADR-0011 /
M4.md rather than its own ADR.)*

---

## 19. Implementation phases

Each phase ends **compiling, tests-green, ruff-clean, frontend typecheck/build
green** where touched, and is independently reviewable.

Post-review, the original 8 phases become **9** — the old Phase 4 (the largest,
0.5–1 d) is split so the retry loop and the cancellation/finalizer rework are
independently reviewable. Every phase compiles, is ruff-clean, keeps the full
suite green, and is PR-sized. Dependency order is strict (each builds on the
prior); phases 1–3 have no user-visible behavior change (pure scaffolding), so
they are safe to land ahead of the behavioral phases 4–6.

| Phase | Scope | Tests | Est. |
|---|---|---|---|
| **1. Contracts + config** | `ReflectionDecision`/`ReflectionResult`(+`source`,`reflection_version`)/`TaskAttemptView`; `TaskContext`/`TaskView` additive fields; event enum rename + 4 additions; six config values. No behavior change. | contract construction, config bounds. | ~0.25 d |
| **2. Persistence + migration** | `TaskAttemptRow` (`attempt_id`+`attempt_number`, `reflection_*`, `reflection_version`); `tasks.attempt_count`/`replan_generation`/`parent_generation`; `_apply_light_migrations`; `TaskAttemptRepository` + `TaskRepository` transitions (`mark_retrying/cancelled/skipped`, `bulk_skip`, `set_generation`). | attempts CRUD + ordering, cascade delete, migration idempotency on a v0.3.0-shaped DB. | ~0.5 d |
| **3. Reflector** | `reflector.py` (stateless), reflection prompt + `REFLECTION_PROMPT_VERSION`, deterministic pre-check, parse/validate/repair, acceptance bias, confidence gate. | `test_reflector.py` (all verdicts, precheck, repair, degraded bias, statelessness). | ~0.5 d |
| **4. Budgets** | `budget.py` (`RunBudget` w/ 5 counters + inspection, `BudgetSnapshot`, `BudgetExceeded`, `BudgetedGateway(category)`, `BudgetedToolRegistry`); wire wrappers in `RunManager` construction (no loop changes yet). | `test_budget.py` (per-category counting, overflow, propagation-not-trapped, inspection reads). | ~0.5 d |
| **5. Retry loop (core)** | `RunManager` attempt loop; attempt-row persistence; `task.reflected`/`task.retrying`; critique injection in Context Builder; `attempt` in payloads. Reflection-disabled parity. | retry success, retry exhaustion→abort, reflection-disabled = M3. | ~0.5 d |
| **6. Cancellation + finalizers** | cancellation checks per phase; `task.cancelled {phase}`; finalizers guaranteeing no orphan RUNNING (I-4); terminal-state cleanup. | mid-retry & mid-reflection cancel, cancel during planning/pending, invariant assertions. | ~0.5 d |
| **7. Replan** | `Planner.replan`, replan prompt; swap-remaining + generations + `parent_generation` + `SKIP`; `plan.replanned`; escalation ladder retry→replan. | replan, replan exhaustion, task0 immutability, monotonic indices. | ~0.5 d |
| **8. Graceful abort + budget finalize** | abort path + partial synthesis (I-12); `budget.exceeded` finalization with no synthesis; full escalation retry→replan→abort. | budget model/tool (no partial), abort partial, event-replay parity. | ~0.5 d |
| **9. Frontend + docs + tag** | attempt/reflection/retry/skip/replan/cancel in `useRunStream` + checklist/feed + RunPage; M4.md, ADR-0011..0015, CHANGELOG/TODO/ARCH/memory, `.env.example`, `v0.4.0`. | frontend typecheck + build; full suite (`~114`). | ~0.5 d |

**Total ≈ 3.5–4 days** (9 PR-sized phases).

---

## 20. Risks & trade-offs

| Risk | Likelihood | Mitigation |
|---|---|---|
| Reflection loops (retry thrash) inflate latency/cost | High | hard retry cap + model/tool budgets + acceptance bias + confidence gate |
| 7B reflector is unreliable/over-critical | High | deterministic pre-check first; acceptance bias on uncertainty; tolerant parse + repair |
| Replan corrupts task ordering / index reuse | Med | monotonic per-run indices; completed tasks immutable; generations; `UNIQUE(run_id,index)` |
| `ALTER TABLE` migration on existing DBs | Med | idempotent `PRAGMA`-guarded add-column; nullable/defaulted; covered by a migration test |
| `BudgetExceeded` accidentally trapped as a tool observation | Med | enforce in the **wrapper** outside the registry try/except; explicit "not-trapped" test |
| M1–M3 test churn from reflection-on-by-default | Low | tests set `enable_reflection=false`; isolated fixture change |
| Partial-answer semantics confuse users (FAILED with an answer) | Med | ADR-0014 + UI badge ("partial — aborted after N attempts") |
| Cancellation still delayed by a long reflection/plan call | Low | documented; model-call budget is the hard backstop (M6 adds real budgets/timeouts) |

**Key trade-off:** M4 accepts more model calls and latency per run in exchange for
robustness. Budgets bound the worst case; acceptance bias keeps the common case
from over-correcting. The alternative (perfectionist reflection) is rejected as
both slower and, on a 7B model, less reliable.

---

## 21. Reviewer decisions — approved amendments (2026-07-09)

RFC-0002 is **approved** with the following 12 amendments, all folded into the
sections above and into the [architecture-validation companion](0002-m4-architecture-validation.md).

| # | Amendment | Where applied |
|---|---|---|
| 1 | Add a dedicated `task.cancelled` event. | §6 table, §9.5, §10, validation §3/§4 |
| 2 | Keep `task.reflected` and `plan.replanned` (rename confirmed). | §6 naming decision |
| 3 | Retry/replan exhaustion → synthesize a partial answer. | §4 `graceful_abort`, ADR-0014, validation §4 |
| 4 | Budget exhaustion → fail immediately, **no** synthesis. | §8 finalize, §9.4, ADR-0014, validation §4 |
| 5 | Reflect after **every** task. | §7, §6 ("every attempt"), validation §5 |
| 6 | Persist `reflection_version` on reflection records. | §7 (stateless note), §11.1, §6 payload |
| 7 | Keep **separate** counters: planner / reflection / synthesis / model / tool. | §8 counter table, validation §5 |
| 8 | Persist `attempt_id` **in addition to** `attempt_number`. | §11.1 |
| 9 | Persist `parent_generation` for replans. | §11.2, §6 `plan.replanned` payload |
| 10 | Keep the Reflector **completely stateless**. | §7 (statelessness), Invariants §I-7 |
| 11 | Expose budget **inspection** methods on `RunBudget`. | §8 inspection methods |
| 12 | Add an **Architectural Invariants** section. | §23 (below) |

The formerly-open questions Q1–Q5 are resolved by amendments 2, 1, 4, and 5
respectively; the attempts endpoint (old Q4) **remains deferred to M5** (the live
UI reconstructs attempts from events).

---

## 22. Deliverables recap

- **This RFC** (`docs/rfc/0002-m4-self-correction.md`) — architecture, FSM, events,
  persistence, prompts, budgets, cancellation, sequence diagrams, tests, risks.
- **Implementation phases** — §19 (8 phases, each green).
- **Required file changes** — §16.
- **New tests** — §15.
- **Migration notes** — §11.3 / §17.
- **Expected version bump** — **0.3.0 → 0.4.0**.
- **Implementation estimate** — **≈ 3–3.5 days** (9 phases post-review — §19 / validation §7).
- **Architecture validation** — [0002-m4-architecture-validation.md](0002-m4-architecture-validation.md):
  run/task FSMs, event timelines, failure matrix, budget accounting, replay proof,
  finalized phase plan.

_RFC approved 2026-07-09 with 12 amendments (§21). Architecture validated.
Implementation may begin, phase by phase (§19)._

---

## 23. Architectural invariants (Amendment 12)

These are the properties every M4 phase must preserve; each has a guarding test.
They are the contract the implementation is reviewed against.

**Event & replay**
- **I-1 (Ledger is truth).** Run/task/attempt/replan/budget state is fully
  reconstructable from the event ledger alone, with no reference to live runtime
  objects (proof: validation §6). Run/`task`/`task_attempts` rows are projections.
- **I-2 (Monotonic seq).** `emit()` remains the single writer of per-run `seq`;
  sequence numbers are strictly increasing and gap-free per run.
- **I-3 (Additive contracts).** All new events/fields are additive; old consumers
  ignore unknown keys. The two reserved event members are renamed, never
  re-valued.

**State machine**
- **I-4 (No orphan RUNNING).** When a run reaches a terminal state
  (`DONE/FAILED/CANCELLED`), **no** task is left `PENDING/RUNNING/RETRYING`
  (closes the M3 audit finding). Enforced by the finalizers.
- **I-5 (Legal transitions only).** Every task transition is one of the edges in
  the task FSM (validation §2); `RUNNING/RETRYING → SKIPPED` is impossible.
- **I-6 (Monotonic indices).** Task `index` is unique and never reused within a
  run; replans append at `index > max(index)`, preserving `UNIQUE(run_id,index)`
  and the `{run_id}:{index}` id scheme. Completed tasks are immutable.

**Reflection & budgets**
- **I-7 (Stateless Reflector).** `Reflector.reflect` is pure w.r.t. injected
  deps; it holds no per-run/per-task state and mutates nothing (Amendment 10).
- **I-8 (Single counting path).** All call counting flows through `RunBudget`'s
  `charge_*` methods (the only mutators); everything else reads via `snapshot()`
  / inspection (Amendment 11). `model_calls`/`tool_calls` are the only hard caps.
- **I-9 (BudgetExceeded is not an observation).** `BudgetExceeded` is charged
  *before* the wrapped I/O and *outside* the tool registry's try/except, so it
  propagates to `RunManager` and is never trapped into a `ToolResult`.
- **I-10 (Bounded termination).** Every run terminates: attempts ≤
  `max_retries+1` per task, replans ≤ `max_replans`, and the model/tool caps
  bound total work — no unbounded reflection/retry loop exists.

**Failure & recovery**
- **I-11 (No exception crosses the run boundary).** Planner/executor/reflector/
  synthesizer/DB failures are converted to `run.failed` (or the recovery ladder);
  the runtime never raises out of the background task.
- **I-12 (Partial-answer rule).** Retry/replan **exhaustion** and `abort`
  synthesize a partial answer and finalize `FAILED`-with-answer (Amendment 3);
  **budget** exhaustion finalizes `FAILED` with **no** synthesis (Amendment 4).
  These two paths are mutually exclusive and individually tested.
- **I-13 (Reflection kill-switch parity).** With `agent_enable_reflection=false`,
  behavior is byte-for-byte M3: zero reflection calls, no retries/replans, single
  attempt per task.
