# ATLAS developer tasks. Verification is one command per milestone (design §10).
#
# On Windows the venv Python lives in Scripts/; on POSIX override:
#   make PY=backend/.venv/bin/python test
PY ?= backend/.venv/Scripts/python.exe

.DEFAULT_GOAL := help

.PHONY: help
help: ## List available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Setup -----------------------------------------------------------------
.PHONY: setup
setup: setup-backend setup-frontend ## Install all dependencies

.PHONY: setup-backend
setup-backend: ## Create backend venv and install dev deps
	cd backend && py -m venv .venv && \
	  .venv/Scripts/python.exe -m pip install --upgrade pip && \
	  .venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements-dev.txt

.PHONY: setup-frontend
setup-frontend: ## Install frontend deps
	cd frontend && npm install

# --- Verify ----------------------------------------------------------------
.PHONY: test test-m1 test-m2 test-m3
test test-m1 test-m2 test-m3: ## Run the backend test suite (milestone gate)
	cd backend && ../$(PY) -m pytest

.PHONY: lint
lint: ## Lint backend (ruff) and typecheck frontend
	cd backend && ../$(PY) -m ruff check atlas tests
	cd frontend && npm run typecheck

.PHONY: build-frontend
build-frontend: ## Production build of the frontend
	cd frontend && npm run build

# --- Codegen (M7) ----------------------------------------------------------
.PHONY: gen-api
gen-api: ## Regenerate the frontend API types from the backend OpenAPI schema
	cd backend && ../$(PY) -m scripts.dump_openapi
	cd frontend && npm run gen:api

# --- Benchmarks (M6, RFC-0004) --------------------------------------------
.PHONY: bench bench-ci
bench: ## Run the full benchmark suite -> bench/out/
	$(PY) -m bench

bench-ci: ## Run the short benchmark subset + threshold guard (CI)
	$(PY) -m bench --ci

# --- Evals (M6, RFC-0004) --------------------------------------------------
.PHONY: evals
evals: ## Run the golden evals -> evals/scorecard.md
	$(PY) -m evals

# --- Run (dev) -------------------------------------------------------------
.PHONY: run-backend
run-backend: ## Run backend with the echo provider (no Ollama needed)
	cd backend && ATLAS_LLM_PROVIDER=echo ../$(PY) -m uvicorn atlas.main:app --reload

.PHONY: run-frontend
run-frontend: ## Run the Next.js dev server
	cd frontend && npm run dev

.PHONY: demo-m1
demo-m1: ## Print the M1 demo steps
	@echo "M1 demo:"
	@echo "  1) make run-backend      # http://localhost:8000 (echo provider)"
	@echo "  2) make run-frontend     # http://localhost:3000"
	@echo "  3) Open the UI, type a goal, watch tokens stream as live events."

.PHONY: demo-m2
demo-m2: ## Print the M2 demo steps
	@echo "M2 demo (tool-using agent):"
	@echo "  With Ollama (full tool loop):"
	@echo "    1) ollama serve && ollama pull qwen2.5:7b-instruct"
	@echo "    2) make run-backend  (set ATLAS_LLM_PROVIDER=ollama)"
	@echo "    3) make run-frontend; ask 'What is 15% of France's population?'"
	@echo "       -> feed shows thought -> tool.call -> tool.result -> answer."
	@echo "  Without a model: 'make test-m2' drives the loop via the FakeLLM."

.PHONY: demo-m3
demo-m3: ## Print the M3 demo steps
	@echo "M3 demo (planning agent):"
	@echo "  With Ollama (multi-task plan):"
	@echo "    1) ollama serve && ollama pull qwen2.5:7b-instruct"
	@echo "    2) make run-backend  (set ATLAS_LLM_PROVIDER=ollama)"
	@echo "    3) make run-frontend; ask 'Compare the populations of France and Germany.'"
	@echo "       -> plan checklist renders; tasks tick pending->running->done;"
	@echo "          a synthesized answer appears."
	@echo "  Without a model: 'make test-m3' drives plan->execute->synthesize via FakeLLM."

# --- Docker ----------------------------------------------------------------
.PHONY: up
up: ## Build and start the full stack (Ollama + backend + frontend)
	docker compose up --build

.PHONY: down
down: ## Stop the stack
	docker compose down
