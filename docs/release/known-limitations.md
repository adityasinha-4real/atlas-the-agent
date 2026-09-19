# Known limitations — v0.7.0

What ATLAS v0.7.0 deliberately does **not** do. None of these are defects; each
is a conscious scope decision recorded in an ADR/RFC or the security review.

- **Single-user, no auth.** No authentication, authorization, rate limiting, or
  transport encryption. Bind to `127.0.0.1` outside a trusted machine. See
  [security-review.md](security-review.md).
- **Local model needed for the full flow.** The dependency-free `echo` provider
  plans a single task equal to the goal, so the full multi-task
  plan → execute → synthesize path requires Ollama (`qwen2.5:7b-instruct`). All
  agent logic is nonetheless covered deterministically by the scripted FakeLLM
  tests.
- **Single-node persistence.** SQLite (WAL) with single-writer discipline; not
  horizontally scalable and no concurrent multi-process writer. Postgres/Alembic
  are deferred (ADR-0015).
- **Keyword memory recall, not semantic.** Episodic recall uses SQLite FTS5
  term-overlap ranking, not embeddings; a semantic/FAISS tier is a deferred
  drop-in behind the `MemoryStore` seam (ADR-0004).
- **Recall at plan time only.** Lessons are injected when planning, not during
  execution or reflection (a tracked additive follow-up).
- **Sequential task list, not a DAG.** Plans are ordered lists, not dependency
  graphs (ADR-0002).
- **Network egress tools are unrestricted.** `web_search`/`web_fetch` can reach
  any URL the goal induces.
- **Frontend dependency advisories.** Two `next`/`postcss` advisories remain
  pending a `next@16` major upgrade; see [dependency-audit.md](dependency-audit.md).
- **Frontend has no lint or unit-test runner.** The frontend gate is `tsc`
  (`npm run typecheck`) plus the production build. The frontend `runReducer`
  has no automated tests of its own; the backend canonical reducer
  (`fold_events`) is covered by golden ledgers (ADR-0023). ESLint is not
  installed or configured, so `npm run lint` is not a gate.
- **Full demo needs a local model.** The model-free Docker path
  (`docker-compose.echo.yml`) runs the whole stack, but the echo provider
  never produces a visible retry/replan; that needs Ollama.
