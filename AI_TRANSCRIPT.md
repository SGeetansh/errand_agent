# AI Assistant Transcript & Engineering Log

## Overview
This document logs the interaction history, architectural reasoning, and implementation trajectory between the engineer and the AI coding assistant (Antigravity) while developing the **Errand Agent** home-service booking system.

---

## Session Context & Directives
- **Goal:** Implement an end-to-end autonomous home-services chat agent ("Errand Agent") adhering strictly to the staff engineer design document (`DESIGN.md`).
- **Target Platform:** macOS (MacBook Air M4, 24 GB unified memory), Python 3.11+, `uv` package manager, SQLite stdlib, pytest. No Docker, no GPU server.
- **Key Invariants:**
  1. *Approval Enforcement*: Mutating actions (`book`, `cancel`, `reschedule`) can NEVER be triggered directly by the LLM. They must be gated by application code requiring explicit user confirmation and cryptographically bound `ApprovalGrant` tokens.
  2. *Deterministic Workflows*: Explicit 13-state finite state machine with strict transition guards and per-state tool allowlists.
  3. *Idempotency & WAL*: Every mutating action logs to an SQLite Write-Ahead Intent Log with deterministic SHA-256 idempotency keys prior to tool execution.
  4. *Privacy Filters*: Forgotten memories and unconfirmed inferred facts must be excluded at the SQL query level, never leaked into LLM prompts.
  5. *Offline Automated Evals*: 27 automated scenarios run against deterministic mocks with auto-generated Markdown eval table.

---

## Milestone-by-Milestone Trajectory

### Milestone 1: Project Setup and Mock Tool Environment
- **Actions:**
  - Initialized repository with `uv init --lib`.
  - Configured `pyproject.toml` with strict dependencies (`pydantic`, `httpx`, `tiktoken`, `pyyaml`, `pytest`, `pytest-timeout`).
  - Implemented 8 mocked tools in `src/tools/mock_env.py` conforming to `src/tools/contracts.py`:
    - `search_providers`, `get_provider`, `hold_slot`, `book`, `get_booking_status`, `cancel`, `reschedule`, `list_bookings`.
  - Added deterministic scenario injection (simulating price drift, 503 transient errors, slot conflicts, prompt injections in reviews, and timeouts).
- **Commit:** `0774649 feat(milestone-1): project setup and mock tool environment`

### Milestone 2: State Machine Workflow & Slot Filler
- **Actions:**
  - Built `src/state_machine.py` implementing `State` enum (13 states), `VALID_TRANSITIONS` set, `ALLOWED_TOOLS_MAP`, and transition guard logic.
  - Created `ErrandSlots` and `SlotFiller` with partial-update preservation.
  - Added unit tests in `tests/test_state_machine.py`.
- **Commit:** `d3449ab feat(milestone-2): state machine workflow, transition guards, and slot filler`

### Milestone 3: Approval Gate & Cryptographic Grant Binding
- **Actions:**
  - Built `src/approval.py` with `ApprovalGate`, `ApprovalGrant`, and `detect_confirmation()` pattern matching.
  - Implemented HMAC/SHA-256 cryptographic binding over `(action, provider_id, slot, price, booking_id)`.
  - Built `src/executor.py` requiring valid single-use `ApprovalGrant` to execute mutating tools.
  - Added unit tests in `tests/test_approval.py` and `tests/test_executor.py`.
- **Commit:** `3df5379 feat(milestone-3): approval gate and executor dispatch with cryptographic grant binding`

### Milestone 4: Idempotency Key Derivation & WAL Recovery
- **Actions:**
  - Built `src/idempotency.py` with deterministic SHA-256 key derivation from `(user_id, action, provider_id, slot, attempt)`.
  - Implemented SQLite Write-Ahead Log in `src/recovery.py` (`IntentLog`) with WAL mode enabled.
  - Built `RecoveryManager` for handling ambiguous timeouts and startup reconciliation against `get_booking_status`.
  - Added unit tests in `tests/test_idempotency.py` and `tests/test_recovery.py`.
- **Commit:** `cd2b302 feat(milestone-4): idempotency key derivation and write-ahead intent log (WAL) recovery`

### Milestone 5: Persistent Memory Store & Privacy Filters
- **Actions:**
  - Built `src/memory.py` (`MemoryStore`) with SQLite persistence.
  - Enforced SQL-level filtering (`WHERE valid_to IS NULL AND (provenance = 'explicit' OR confirmed = 1)`).
  - Implemented seed loading from `eval/seed_memories.json`.
  - Added unit tests in `tests/test_memory.py`.
- **Commit:** `90d426e feat(milestone-5): persistent memory store with SQL-level privacy filters and seed memories`

### Milestone 6: Input Sanitization & Token-Bounded Context Builder
- **Actions:**
  - Built `src/sanitizer.py` stripping control tokens (`<SYSTEM>`, `<DATA>`, `[REDACTED]`) and wrapping untrusted data in `<DATA>` XML blocks.
  - Built `src/context.py` (`ContextBuilder`) managing a strict 4000-token budget using `tiktoken`.
  - Emitted `ContextManifest` structured logs per turn.
  - Added unit tests in `tests/test_sanitizer.py` and `tests/test_context.py`.
- **Commit:** `51ecb76 feat(milestone-6): input sanitization pipeline and token-bounded context builder`

### Milestone 7: Budget Tracker & Structured JSONL Tracer
- **Actions:**
  - Built `src/budget.py` (`Budget`) tracking model turns (cap: 25), tool calls (cap: 10), wall clock time (cap: 120s), and per-tool retry limits.
  - Built `src/tracer.py` (`Tracer`) with RFC 3339 timestamps and regex PII redaction.
  - Added unit tests in `tests/test_budget.py`.
- **Commit:** `642c559 feat(milestone-7): budget bounding tracker and structured JSONL tracer with PII redaction`

### Milestone 8: LLM Provider Interfaces
- **Actions:**
  - Built `src/models/` interface:
    - `ScriptedModel`: offline rule-based matcher for deterministic evals.
    - `RecordedModel`: replay past trace fixtures.
    - `OllamaModel`: local API integration for `qwen2.5:7b` with retry and JSON fallback.
  - Added unit tests in `tests/test_models.py`.
- **Commit:** `ccfd707 feat(milestone-8): LLM provider interfaces (scripted, recorded, and ollama)`

### Milestone 9: Orchestrator, CLI REPL & Proactive Handler
- **Actions:**
  - Built central `Orchestrator` (`src/orchestrator.py`) binding all subsystems.
  - Implemented proactive cancellation recovery (`src/proactive.py`) and event polling (`src/event_queue.py`).
  - Created interactive CLI interface (`src/cli.py`).
  - Added integration tests in `tests/test_orchestrator.py` and `tests/test_proactive.py`.
- **Commit:** `c0f70c3 feat(milestone-9): central orchestrator, proactive handler, CLI REPL, and approval workflow`

### Milestone 10: Automated Eval Harness & Scenario Suite
- **Actions:**
  - Implemented `eval/harness.py` running all 27 evaluation scenarios specified in `DESIGN.md`.
  - Configured test runner `tests/test_eval.py` to auto-generate `eval_table.md`.
  - Verified 100% pass rate across 27 scenarios and 100 total pytest test cases.
- **Commit:** `d9fe403 feat(milestone-10): eval harness, 27 automated scenarios, and auto-generated eval table`

### Milestone 11: Polish & Deliverables
- **Actions:**
  - Documented `BUGS.md` with 3 real bugs encountered, root causes, fixes, and regression tests.
  - Authored 1-page `DESIGN_NOTE.md` with Mermaid state diagram and annotated S10 trace.
  - Authored comprehensive `README.md` with setup, CLI instructions, and architecture guide.
  - Compiled `AI_TRANSCRIPT.md`.
