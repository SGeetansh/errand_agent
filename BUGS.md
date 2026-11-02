# Bug Log

## Bug 1: SQLite In-Memory Database Isolation in WAL and MemoryStore

**What broke**: During Milestone 4 and Milestone 5 unit tests (`tests/test_recovery.py` and `tests/test_memory.py`), querying the intent log or memory store after inserting records failed with `sqlite3.OperationalError: no such table: intent_log` (or `memories`) when initialized with `db_path=":memory:"`.

**Why**: Calling `sqlite3.connect(":memory:")` on every method call creates a brand new, isolated in-memory database per connection. Schema creation in `_init_db()` ran on an initial temporary connection and was immediately discarded, leaving subsequent connection handles with empty databases.

**Fix**: Cached the active database connection handle `self._conn` in `IntentLog` and `MemoryStore` so that for `:memory:` instances (as well as file-based databases during the object's lifecycle), all initialization, write, and read queries share the same persistent connection. Added explicit `close()` methods for cleanup.

**Regression test**:
- File: `tests/test_recovery.py::test_wal_log_and_status_update` and `tests/test_memory.py::test_memory_crud_explicit`
- Command: `uv run pytest tests/test_recovery.py tests/test_memory.py -v`

---

## Bug 2: State Machine Illegal Transition from IDLE to AWAITING_APPROVAL in Cancellation & Reschedule

**What broke**: In evaluation scenarios `test_s12_slot_conflict_on_reschedule` and `test_s13_cancel_with_transient_503`, the orchestrator raised `InvalidTransitionError: Cannot transition from IDLE to AWAITING_APPROVAL`. In `test_s09_repeated_confirmation`, repeated user confirmations when already in `DONE` state raised `InvalidTransitionError: Cannot transition from DONE to COMPARING`.

**Why**: `VALID_TRANSITIONS` in `src/state_machine.py` strictly enforced valid state transitions. When proposing a cancellation or reschedule from `IDLE`, the orchestrator jumped straight to `AWAITING_APPROVAL` without traversing `CANCELLING` or `RESCHEDULING`. Furthermore, `VALID_TRANSITIONS` was missing `(CANCELLING, AWAITING_APPROVAL)`, `(AWAITING_APPROVAL, CANCELLING)`, and `(AWAITING_APPROVAL, RESCHEDULING)`. In `DONE` state, incoming confirmation fell through to `_handle_llm_turn` which attempted a new search transition.

**Fix**: Updated `_propose_cancellation` and `_propose_reschedule` in `src/orchestrator.py` to transition `IDLE -> CANCELLING -> AWAITING_APPROVAL` and `IDLE -> RESCHEDULING -> AWAITING_APPROVAL`. Added `(State.CANCELLING, State.AWAITING_APPROVAL)`, `(State.AWAITING_APPROVAL, State.CANCELLING)`, and `(State.AWAITING_APPROVAL, State.RESCHEDULING)` to `VALID_TRANSITIONS`. Added explicit short-circuit handling in `run_turn` for `State.DONE` to acknowledge completed bookings idempotently without triggering provider searches.

**Regression test**:
- File: `tests/test_eval.py::test_s09_repeated_confirmation`, `tests/test_eval.py::test_s12_slot_conflict_on_reschedule`, `tests/test_eval.py::test_s13_cancel_with_transient_503`
- Command: `uv run pytest tests/test_eval.py -k "test_s09 or test_s12 or test_s13" -v`

---

## Bug 3: Hatchling Build Failure on Flat src/ Package Layout

**What broke**: Running `uv build` or packaging the project resulted in `hatchling.build.PackagingError: Unable to determine which files to ship inside the wheel... no directory that matches agentic_orange`.

**Why**: `uv init --lib` initialized `pyproject.toml` expecting a nested package directory structure matching the project name (`src/agentic_orange`), whereas the architectural specification designated `src/` itself as the root package containing modules (`src/state_machine.py`, `src/orchestrator.py`, etc.).

**Fix**: Configured `[tool.hatch.build.targets.wheel] packages = ["src"]` in `pyproject.toml` and removed the empty `src/agentic_orange` directory so that Hatchling builds wheel packages directly from `src/`.

**Regression test**:
- File: `tests/test_mock_env.py::test_search_providers_happy_path` (verifies imports from `src.*` resolve properly in editable and test environments)
- Command: `uv run pytest tests/test_mock_env.py -v`
