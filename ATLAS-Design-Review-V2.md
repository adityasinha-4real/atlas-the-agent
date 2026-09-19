# ATLAS — Design Review & Architecture V2
## Senior Staff Review of SDD v1 · Pre-Implementation Gate

**Verdict on V1: Conditionally rejected.** The core thesis (hand-built agent loop, explicit failure handling, local models) is right and survives. But V1 is designed like a platform, not a product. It contains at least four subsystems that exist because they appear in agent-architecture blog posts, not because this project needs them. A professional team would cut ~40% of V1 before writing a line of code. This document does that cutting and produces V2.

The single biggest sin of V1: **horizontal phasing.** Phases 1–6 produce zero visible software. A student following V1 spends 8 days with nothing demoable and no way to know if the 7B model can even sustain the loop. That is backwards. V2 is vertical-slice milestones: every milestone ends with a runnable, demo-ready app.

The second biggest sin: **speculative generality.** DAG plans, three-tier memory, a model router, a replanner module, an 8-page frontend — all before evidence that a local 7B model can reliably fill a flat JSON schema. V1 optimizes for an imagined future instead of the actual risk, which is: *small local models are erratic.* Everything in V2 is redesigned around that risk.

---

## 1. Subsystem-by-Subsystem Architectural Review

Scoring: Complexity/Risk/Difficulty = Low·Med·High. "MVP?" = belongs in Version 1 of the build.

### 1.1 Planner
- **Purpose:** goal → executable task structure.
- **V1 problem:** it emits a **DAG**. A DAG buys parallelism and complex dependencies — neither of which a single-run, single-worker, laptop-CPU system uses. Meanwhile it costs: topological scheduling, cycle detection, a harder JSON schema for a 7B model to emit correctly, a harder timeline UI, and a much harder replanner (graph patching). This is résumé-driven design.
- **Better pattern:** **ordered task list** (`[{id, description, success_criteria, suggested_tool}]`). Sequential execution. When a graph is genuinely needed later, a list is a degenerate DAG — the migration is additive.
- **Hidden problem:** local models pad plans with filler tasks ("gather requirements", "verify results"). Mitigate with a hard cap (max 5 tasks) and few-shot examples of tight plans.
- **FAANG take:** any staff engineer asks "what requires the graph?" and finds no answer. Cut.
- Complexity: Med→Low · Risk: High→Low · Difficulty: Med · **MVP: yes (as list)**

### 1.2 Executor
- **Purpose:** per-task ReAct loop (think → tool → observe).
- **Assessment:** correctly scoped in V1. This is the heart of the project. Keep.
- **Hidden problems:** (a) 7B models emit tool args that validate against the schema but are semantically wrong (URL in a `query` field) — surface these as observations, not crashes; (b) models "narrate" instead of calling tools — require a structured `{action: tool_call | finish}` envelope every turn; (c) the loop must treat *every* tool outcome as an observation. No exceptions cross the loop boundary, ever.
- Complexity: Med · Risk: Med · Difficulty: Med-High · **MVP: yes**

### 1.3 Reflection Engine
- **Purpose:** grade task output vs success criteria; decide accept / retry / abort.
- **V1 problem:** four verdicts including `revise_plan`, which drags in the replanner. Also a subtle failure mode V1 ignores: **the 7B reflector is a worse judge than the 7B executor is a worker.** Reflection loops on weak models can reject good outputs and cause retry storms.
- **Better pattern:** three verdicts only — `accept | retry_with_critique | abort_task`. Plus a **cheap deterministic pre-check** before invoking the LLM judge: non-empty output? required tool actually called? Deterministic checks are free and catch half the failures. Cap retries at 2, and bias the reflector prompt toward acceptance ("reject only for clear criteria violations").
- Complexity: Med · Risk: Med · Difficulty: Med · **MVP: yes — reflection is the agentic differentiator; without it this is a script runner**

### 1.4 Replanner
- **Purpose (V1):** patch the DAG using reflection feedback via patch operations (replace/insert/drop).
- **Critique:** a whole module and patch-op vocabulary for what is actually one prompt. **Delete the module.** Replanning = calling the Planner again with extra context: original goal, completed tasks + outputs, failure critique → "produce a revised list for the remaining work." One function, ~zero new schema, and demos identically ("the agent revised its plan").
- **Would FAANG build V1's version?** No. Regenerate-with-context is the standard first implementation; graph surgery is what you build when regeneration measurably fails.
- Complexity: High→Low · **MVP: yes, as a planner mode, not a module**

### 1.5 Memory (Working / Episodic / Semantic)
- **V1 problem:** three tiers, FAISS, embeddings, ingestion pipeline — in V1. Semantic memory's demo value is invisible unless you run many related goals; a recruiter never sees it; and it adds an index, an embedding model, and consistency questions.
- **Better pattern for V1:**
  - **Working memory** = the Context Builder's job (keep — it's real engineering).
  - **Episodic memory** = one SQLite table: per-run structured summary (`goal, outcome, lessons`) written at run end; at plan time, retrieve recent/similar rows with **SQLite FTS5 keyword search**. FTS5 is built into SQLite — no new dependency — and "agent recalls lessons from past runs" demos identically to FAISS.
  - **Semantic memory (FAISS)** = Milestone 6+, behind the same `MemoryStore` interface so it's a drop-in.
- **Is SQLite alone sufficient?** Yes, unambiguously, for V1. FTS5 keyword recall over dozens-to-hundreds of run summaries is indistinguishable from vector recall at this scale.
- Complexity: High→Low · Risk: Med→Low · **MVP: episodic only**

### 1.6 Tool Registry
- **Assessment:** correctly designed (ABC + Pydantic schema + ToolResult + timeout). Keep exactly as specified. This is the cleanest part of V1.
- **One simplification:** drop per-tool "sandbox policy" configuration; hardcode the two policies that exist (path jail for file ops, subprocess limits for sandbox).
- Complexity: Low · Risk: Low · **MVP: yes**

### 1.7 Frontend
- **V1 problem:** eight surfaces (dashboard, composer, timeline, monitor, memory explorer, tool inspector, trace viewer, settings). That's a week of UI for pages nobody demos.
- **V2:** **two pages.**
  1. **Run page** — goal input + live plan checklist (task states) + live event feed + final answer. This is the entire demo.
  2. **History page** — past runs; click → same Run page in replay mode (render persisted events instead of live WS). Trace viewing = expandable event rows on the Run page. Memory/tool inspectors: cut (Milestone 6+).
- **Hidden problem:** WS reconnection/ordering. Mitigate: monotonic `seq` per event; on (re)connect, fetch `GET /runs/{id}/events?after=seq` then stream. This also gives replay for free — replay is just rendering the same endpoint. One mechanism, two features.
- Complexity: High→Med · **MVP: yes (two pages)**

### 1.8 Backend / API
- **Assessment:** FastAPI + Pydantic + SQLAlchemy is right. Cut routes to what the two pages need: `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`, `POST /runs/{id}/cancel`, `WS /runs/{id}/stream`, `GET /tools`, `GET /health`. Memory routes wait.
- **Cut from V1:** the **model router**. One model (qwen2.5:7b-instruct, temp 0) until profiling proves a second is needed. The router was optimizing latency before measuring it. The `LLMGateway` keeps `model` as a parameter, so routing later is a config change.
- **MVP: yes**

### 1.9 Event Bus
- **V1 problem:** pub/sub bus with three sink subscribers — an in-process microservice pattern for one producer and two consumers.
- **V2:** one `emit(event)` function that (a) appends the event to SQLite (source of truth, with `seq`), (b) pushes to the run's asyncio queue for WS. JSONL log sink: cut — the events *table* is the trace, and the Run page renders it. `emit()` keeps a stable signature so a real bus can replace it later without touching call sites.
- Complexity: Med→Low · **MVP: yes (as a function, not a bus)**

### 1.10 Persistence
- **Assessment:** SQLite + SQLAlchemy correct. Simplify the schema: V1 has Run/Task/Step/Message/ToolCall/MemoryEntry. Collapse Step/Message/ToolCall into the **events table** (typed JSON payloads). Tables: `runs`, `tasks`, `events`, `memories`. Fewer joins, and the event log doubles as the audit trail and replay source.
- **Hidden problem:** SQLite writes from async code block the loop. Enable WAL; use a single writer pattern (all writes via `emit`/repositories on one connection).
- **MVP: yes**

### 1.11 Context Builder
- **Assessment:** keep — genuinely production-grade thinking. Simplify V1's four-bucket budget to: fixed system prompt + plan state (compact) + last-N observations, each observation truncated/summarized to ≤~150 tokens at *write* time (when the tool returns), not at prompt-assembly time. Summarize-at-source is simpler and bounds storage too.
- **MVP: yes**

### 1.12 Evaluation
- **Assessment:** the golden-goal harness is a top-3 differentiator — keep — but V1 buries it in Phase 8. **Move it early (Milestone 5)** so it can catch prompt regressions during development, which is its entire point. Trim to 8–10 goldens, deterministic assertions only (keywords/regex/tool-was-called); LLM-as-judge later.
- **MVP: yes, earlier**

---

## 2. What Is Removed / Redesigned (Summary Table)

| V1 element | V2 decision | Why |
|---|---|---|
| DAG planner + topological scheduler | **Sequential task list** | No parallelism to exploit; halves planner+UI+replan complexity; easier JSON for 7B models |
| Replanner module w/ patch ops | **Planner re-invocation with feedback** | Same demo, ~5% of the code |
| Semantic memory (FAISS) in core | **Deferred; SQLite FTS5 episodic memory in V1** | Invisible to recruiters at V1 scale; FAISS drops in later behind `MemoryStore` |
| RAG tool + ingestion pipeline | **Optional plugin, post-MVP** (see §6) | Not needed to demonstrate agency |
| Model router (7B/3B) | **One model** | Premature optimization; gateway keeps the seam |
| Event bus + 3 sinks | **`emit()` → events table + WS queue** | Right-sized; events table = trace = replay |
| 8 frontend surfaces | **2 pages** | Demo lives on one page |
| `shell` tool | **Deleted entirely** | Security liability, zero interview value ("it's off by default" impresses no one) |
| `code_sandbox` in MVP | **Should-have (Milestone 6)** | High value but real sandboxing effort; not needed for first demo |
| 6-table schema | **4 tables** (runs, tasks, events, memories) | Event log unifies steps/messages/toolcalls |
| 9 horizontal phases | **7 vertical milestones**, each demo-ready | See §8 |

**MoSCoW:**
- **Must have:** planner (list), executor ReAct loop, reflection (3 verdicts) + retry, replan-via-planner, tool registry + web_search/web_fetch/file_ops/calculator, episodic memory (FTS5), context builder, events table + WS, 2-page frontend, run cancellation, eval harness, Docker.
- **Should have:** code_sandbox, crash-resume, dashboard stats on History page, tool success metrics.
- **Nice to have:** FAISS semantic memory, memory explorer UI, human-approval gate, LLM-as-judge evals, model routing.
- **Future:** RAG plugin, multi-agent, scheduler, plugin system, browser tool, visual workflow builder.

---

## 3. Workflow Review

V1's pipeline drew Reflection and Replanning as pipeline stages. They are not stages — reflection is **inside the per-task loop**, and replanning is a **conditional edge back to the planner**. V2 pipeline:

```
Goal
 ↓
PLANNING ── planner LLM (list of ≤5 tasks) ── invalid JSON → repair (≤2) → FAILED
 ↓
RUNNING:  next pending task
 ↓
   ┌─ Executor ReAct loop (≤5 iters) ──────────────┐
   │  think → {tool_call | finish} → observe        │
   └────────────────────────────────────────────────┘
 ↓
   Reflection gate:
     deterministic pre-check fail ──────────────► retry w/ critique (≤2)
     LLM verdict = accept ──────────────────────► task DONE → next task
     LLM verdict = retry_with_critique (≤2) ───► re-execute
     retries exhausted ─────────────────────────► REPLAN? (≤1 per run)
 ↓                                                    │
   REPLAN: planner(goal, done tasks, critique)  ◄─────┘
           → new remaining-task list → RUNNING
 ↓ all tasks done
Synthesize final answer → write episodic memory → DONE
(any budget breach at any point → graceful abort: partial summary → FAILED)
```

Key change: **replan is rate-limited (once per run)**. Unlimited replanning on a weak model oscillates. One replan is enough for the demo and bounds worst-case cost.

---

## 4. State Machine Review

V1 states: PLANNING → EXECUTING → REFLECTING → REPLANNING → DONE/FAILED (+ WAITING_LLM).

**Problems:** REFLECTING and REPLANNING as *run-level* states conflate run lifecycle with intra-task activity, multiplying transitions and persistence writes. WAITING_LLM likewise describes a condition, not a lifecycle stage.

**V2 run FSM (6 states):**

```
CREATED → PLANNING → RUNNING → DONE
              │          │  ↖──(replan happens inside RUNNING,
              │          │      surfaced as an event, not a state)
              ├──────────┼────→ FAILED
   CANCELLED ◄┴──────────┤
                      PAUSED  (Ollama unreachable / future approval gate) ⇄ RUNNING
```

- **Merge:** REFLECTING + REPLANNING into RUNNING (they're steps; the *event stream* shows them, which is what the UI needs).
- **Add:** CANCELLED (user action ≠ failure — V1 missed this) and PAUSED (absorbs WAITING_LLM; later reused for human approval, a free stretch-goal seam).
- **Task-level FSM stays separate and simple:** PENDING → RUNNING → (DONE | FAILED | SKIPPED). SKIPPED is new — needed when a replan drops remaining tasks.
- **List vs DAG:** list, per §1.1. The strongest practical argument: a checklist UI with live states is *more legible in a 5-minute recruiter skim* than a graph, and legibility is the portfolio's currency.

---

## 5. Tooling Review (Ranked)

| Rank | Tool | Why it exists | Interview value | Eng. complexity | Priority | Postpone? |
|---|---|---|---|---|---|---|
| 1 | **web_search** (ddgs) | Grounds the agent in live data; enables research goals | High — makes demos feel autonomous | Low | P0 | No |
| 2 | **web_fetch** (+extraction, summarize-at-source) | Search snippets alone are too thin for multi-step reasoning | Med-High | Med (extraction, truncation) | P0 | No |
| 3 | **file_write / file_read** | Agent produces artifacts (reports) — tangible output for demos | High — "it wrote this file" lands well | Low (path jail) | P0 | No |
| 4 | **calculator** | Trivial, but enables clean search→compute chains; great first E2E test | Low alone, Med in chains | Trivial | P0 | No |
| 5 | **code_sandbox** | Real computation, data analysis; big talking point (sandboxing) | High | High (resource limits, correct kill semantics, escapes) | P1 | **Yes → Milestone 6** |
| 6 | **rag_query** | Answers over user docs | Med (common, not differentiating) | High (ingestion+FAISS+chunking) | P2 | **Yes → plugin** |
| 7 | **shell** | — | Negative (security smell) | High to do safely | — | **Deleted** |

Four P0 tools are enough: they support research goals, computation chains, and artifact production — the full demo surface.

---

## 6. RAG Review — Make It a Plugin

**Is RAG necessary to demonstrate agentic AI?** No. Agency = planning, tool use, reflection, recovery. RAG is a *tool*, and 2026 recruiters have seen a thousand RAG demos. Including it in core adds an embedding model, index lifecycle, and ingestion UX to V1 for negative differentiation.

**Plugin architecture (so it bolts on with zero runtime changes):**
1. The Tool Registry already discovers `Tool` subclasses — RAG arrives as a package (`atlas_rag/`) exposing a `rag_query` tool via the same registration decorator/entry point. The executor and planner need no changes; the planner sees one more schema.
2. Ingestion ships as a CLI inside the plugin (`python -m atlas_rag ingest ./docs`) writing its own FAISS index + chunk table. Core schema untouched.
3. Config: `plugins: [atlas_rag]` in settings; absent = zero cost.
This *is* the plugin system from the stretch list — RAG becomes its proof-of-concept, which is a better story than RAG-in-core.

---

## 7. Scalability Review (10x Thought Experiment)

**Would become debt at 10x — but is the right call today (do NOT pre-build):**
- SQLite single-writer → Postgres. Cheap later *because* SQLAlchemy + repository pattern are in from day one.
- In-process asyncio runtime + single-run semaphore → worker queue (arq/celery). Cheap later *because* runs are persisted ledgers resumable by any worker.
- In-process `emit()` → Redis/NATS. Cheap later *because* the emit signature is the seam.
- FTS5 memory → vector store. Cheap later *because* of the `MemoryStore` interface.

**Abstractions to introduce NOW (cheap, high option value):** `LLMGateway` (provider seam), `Tool` ABC + `ToolResult` (plugin seam), `MemoryStore` interface, repository pattern, `emit()` function, event `seq` numbers, run-as-persisted-ledger.

**Abstractions to explicitly NOT build now:** message broker, multi-tenant auth, model router, DAG scheduler, config-driven plugin loader beyond entry points, microservices of any kind. Every one is speculative; the seams above make each a bounded refactor later. That sentence is itself a strong ADR/interview answer.

---

## 8. Portfolio Review — The 5-Minute Recruiter Test

A recruiter's actual path: README (90s) → maybe clicks demo GIF → skims folder names → maybe opens one file. Ranked by first-impression impact:

1. **README demo GIF**: goal typed → plan checklist appears → tasks tick live → a retry happens → final answer. If the GIF shows a *visible self-correction*, you've won the 5 minutes. Engineer one golden goal specifically to reliably trigger a retry for this recording.
2. **Architecture diagram** in README (the §3 loop) — signals design ability before any code is read.
3. **Live task timeline UI** — the visual proof of "agentic."
4. **Eval scorecard table committed in the README** ("83% over 12 golden tasks, avg 9.4 steps") — almost no student project has this; screams engineering maturity.
5. **ADR folder** — the thing senior engineers open first.
6. Clean folder structure + green CI badge.
7. **Never noticed by a recruiter:** memory internals, event schema, resume-after-crash, sandbox details, replay. These earn their keep in *interviews*, not skims — fine, but they justify why they're mid/late milestones, not early ones.

---

## 9. Milestones (Replaces V1 Phases) — Every One Demo-Ready

Each milestone = working backend + working frontend + demonstrable feature + git tag + updated README GIF/notes. No invisible-infrastructure milestones.

| M | Name | Demo at the end | Scope | Est. |
|---|---|---|---|---|
| **M1** | Walking skeleton | Type a goal in a minimal web page → backend calls Ollama → streamed answer renders. *(A thin chat — exists only to prove the full stack + model on day one; the agentic layers replace it immediately.)* | FastAPI app, config, LLMGateway, SQLite (runs, events), `emit()`, WS stream, Next.js shell with Run page v0, Docker compose | 1–1.5 d |
| **M2** | Tool-using agent (single task) | Goal: "What is 15% of France's population?" → UI shows think→search→calculate→answer as live events | Tool ABC + registry, 4 P0 tools, JSON-mode + repair loop, executor ReAct loop, event feed UI | 2 d |
| **M3** | Planning agent | Multi-step goal → plan checklist renders → tasks execute sequentially, ticking live → synthesized answer | Planner (list), run FSM, task table, context builder v1, checklist UI, cancellation | 2 d |
| **M4** | Self-correcting agent | Golden demo: a task fails (bad search) → UI shows critique → retry succeeds; second demo shows one replan | Reflector (pre-check + 3 verdicts), retry policy, replan-via-planner, budgets, graceful abort, retry/critique badges in UI | 1.5–2 d |
| **M5** | Memory + evals | Run goal A; run related goal B → event feed shows "recalled lesson from run #A". Eval scorecard generated and committed | Episodic memory (FTS5), memory retrieval at plan time, eval harness + 8–10 goldens + scorecard, History page with replay | 1.5–2 d |
| **M6** | Hardening | Kill process mid-run → resume completes. Sandbox demo: agent writes+runs Python for a data task | Crash-resume, code_sandbox tool, failure-injection test suite, PAUSED state, metrics on History page | 2 d |
| **M7** | Ship | Clean-machine `docker compose up` → demo in <10 min; README with GIF, scorecard, diagram; 6 ADRs | Packaging, docs, ADRs, final GIF, CI badge | 1 d |

**Total: ~11–12 days.** Every risk (model quality, JSON reliability, latency) is confronted by M2 instead of Phase 4.

---

## 10. Claude Code Optimization

Design the repo so each session needs minimal context:

1. **Contract-first files.** `agent/schemas.py` (all Pydantic models) and `events/types.py` are written and frozen in M1–M2. Later sessions receive *only these files + the milestone spec* as context — not the whole codebase. Small, stable contracts are what make small prompts possible.
2. **One milestone = 2–4 sessions, each an isolated seam.** E.g., M4 sessions: (a) reflector module + unit tests (touches 2 files), (b) wire into runtime (touches 1 file), (c) UI badges (frontend only). Backend and frontend never in the same session; the OpenAPI-generated TS client is the boundary.
3. **Spec files in-repo.** `docs/milestones/M3.md` contains objectives, files to create, acceptance tests. Each session prompt = "implement docs/milestones/M3.md section 2; run `make test-m3`." The plan travels in the repo, not in chat context.
4. **Test-gated commits.** Every session ends: targeted tests green → commit. Claude Code never proceeds on red. `Makefile` targets per milestone (`test-m2`, `demo-m2`) so verification is one command, not judgment.
5. **FakeLLM from M2.** All agent-logic tests run against scripted responses — deterministic, fast, no Ollama in CI, and Claude Code can self-verify without a model running.
6. **Minimal file editing:** modules are append-only where possible (new tool = new file + one registry line; new verdict handling = one match arm). Structure chosen so features add files rather than rewrite them.

---

## 11. Engineering Excellence (Prioritized, Not a Wishlist)

Ordered by (differentiation ÷ effort); the top four are in-plan, the rest are earned extras:

1. **Eval scorecard committed to README** (M5) — near-zero incremental cost once the harness exists; extremely rare in student repos.
2. **Run replay** (M5) — free by construction: replay = render `GET /runs/{id}/events`. "Every run is fully replayable from its event log" is a one-line interview grenade.
3. **ADRs** (ongoing, finalized M7) — six honest ones, including *reversals from this review* ("ADR-003: list over DAG", "ADR-005: FTS5 over FAISS"). Documented trade-off reversals read as senior judgment.
4. **Trace visualization** = the Run page event feed itself; don't build a separate viewer.
5. Tool analytics (success rate per tool on History page) — cheap in M6, mildly impressive.
6. Prompt versioning: prompts already live in versioned template files; a `PROMPTS.md` changelog tied to eval-scorecard runs = prompt regression tracking with ~1 hour of effort. **Skip** prompt-diffing tooling — the git diff of template files *is* the diff.
7. **Skip:** benchmark-across-models suite, execution playback with scrubbing, memory-inspection UI — real effort, marginal skim value. Post-M7 only.

---

## 12. Version 2 — Consolidated Architecture

```
┌────────────── FRONTEND (Next.js·TS·Tailwind) ──────────────┐
│   Run Page (goal · live checklist · event feed · answer)    │
│   History Page (runs · stats · replay → Run Page)           │
└────────────────────┬────────────────────────────────────────┘
            REST + WS (seq-numbered events, replayable)
┌────────────────────▼────────────────────────────────────────┐
│                    FASTAPI BACKEND                           │
│   Runtime FSM: CREATED→PLANNING→RUNNING→DONE                │
│                 (│→FAILED, →CANCELLED, RUNNING⇄PAUSED)       │
│                                                              │
│   Planner (task LIST, ≤5) ──► Executor (ReAct ≤5 iters)     │
│        ▲                          │                          │
│        └── replan (≤1/run) ◄── Reflector (pre-check +       │
│                                   accept/retry/abort)        │
│                                                              │
│   LLMGateway (Ollama, ONE model, temp 0, JSON repair ≤2)    │
│   Tool Registry: web_search · web_fetch · file_ops · calc   │
│                  [M6: code_sandbox] [plugin: rag_query]      │
│   MemoryStore: episodic summaries + FTS5 recall             │
│                  [later: FAISS behind same interface]        │
│   emit(event) → events table (seq) + WS queue               │
│   Context Builder: summarize-at-source, token-budgeted      │
├──────────────────────────────────────────────────────────────┤
│   SQLite (WAL): runs · tasks · events · memories            │
│   Eval harness: 8–10 goldens → scorecard.md                 │
└──────────────────────────────────────────────────────────────┘
                    Ollama · qwen2.5:7b-instruct
```

### Change log vs V1 (each with the improvement it buys)

| # | Change | Improvement |
|---|---|---|
| 1 | DAG → ordered task list | −50% planner/runtime/UI complexity; far higher JSON reliability on 7B; more legible demo |
| 2 | Replanner module → planner re-invocation, ≤1/run | Same capability & demo, fraction of the code; prevents replan oscillation |
| 3 | 3-tier memory → SQLite FTS5 episodic (FAISS deferred behind `MemoryStore`) | Removes embedding model + index from critical path; identical demo; clean upgrade seam |
| 4 | RAG → plugin (and proof of the plugin system) | Core stays lean; turns a commodity feature into an architecture story |
| 5 | Run FSM: −REFLECTING −REPLANNING −WAITING_LLM, +CANCELLED +PAUSED; task +SKIPPED | Lifecycle vs activity cleanly separated; fewer transitions; PAUSED doubles as future approval seam |
| 6 | Event bus → `emit()` + events table (seq) | Right-sized; events table = trace = replay = WS backfill — four features, one mechanism |
| 7 | 6 tables → 4 | Simpler persistence; event log as unified audit trail |
| 8 | Model router cut; one model | Removes premature optimization; gateway keeps the seam |
| 9 | shell tool deleted; sandbox → M6; 4 P0 tools | Smaller attack surface; MVP tools cover the whole demo surface |
| 10 | 9 horizontal phases → 7 vertical milestones | Demoable software from day 1–2; model risk confronted at M2 not "Phase 4"; every milestone commit-and-GIF-able |
| 11 | Evals moved from last phase to M5 | Harness catches prompt regressions during development — its actual purpose |
| 12 | Reflection: deterministic pre-checks + 3 verdicts + acceptance bias | Halves LLM-judge calls; prevents weak-judge retry storms |
| 13 | 8 frontend surfaces → 2 pages; replay free via event backfill | ~3 fewer UI days; everything a recruiter sees is on one page |
| 14 | Claude Code plan: contract-first frozen schemas, in-repo milestone specs, test-gated sessions, backend/frontend never co-edited | Sessions fit in small context; verification is mechanical, not judgment |

**Bottom line:** V2 keeps every property that makes ATLAS interview-worthy — hand-built plan/act/reflect loop, visible self-correction, persistent memory, replayable event-sourced runs, an eval scorecard — while deleting the platform cosplay. It is buildable by one student in ~11–12 focused days with Claude Code, and the cuts themselves become ADRs that demonstrate the judgment V1 lacked.
