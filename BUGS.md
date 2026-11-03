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

## Bug 3: Conversational Whitespace and Snake-Case Mismatch in Memory Deletion (`_handle_memory_commands`)

**What broke**: During live-LLM testing (`tests/live/test_live_ollama.py::test_live_memory_lifecycle`), issuing natural language forgetting commands such as "forget preferred area" or "forget my preferred area" failed to match the database fact key `preferred_area`. The orchestrator replied `"I couldn't find any memory matching 'preferred area' to forget."`, leaving the memory active and leaking forgotten preferences into subsequent model context manifests.

**Why**: `_handle_memory_commands` performed a raw, unnormalized substring check `if target in f.fact.lower()` without stripping conversational filler words ("my", "about") and without normalizing between natural language whitespace (`"preferred area"`) and database schema snake_case identifiers (`"preferred_area"`). In addition, the loop performed an early `return` on the first matched record ID, leaving duplicate or historical versions of the same key intact in `active` state.

**Fix**: Updated `_handle_memory_commands` in `src/orchestrator.py` to:
1. Strip conversational filler words (`"my"`, `"about"`, `"that i"`).
2. Compute normalized space-to-underscore (`target_norm = raw_target.replace(" ", "_")`) and underscore-to-space representations (`fact_norm = f.fact.lower().replace("_", " ")`) before checking membership against `f.fact` and `f.value`.
3. Iterate across all matching active memory records to forget all instances of the requested preference and confirm their deletion in the final message.

**Regression test**:
- Offline File: `tests/test_memory.py::test_bug3_forget_command_natural_language_and_duplicates`
- Live File: `tests/live/test_live_ollama.py::test_live_memory_lifecycle`
- Command: `uv run pytest tests/test_memory.py -k "test_bug3" -v`

---

## Bug 4: Hedged Confirmations Incorrectly Triggering Mutating Tool Execution

**What broke**: User inputs containing conditional or contradictory phrasing such as `"yes but make it cheaper"` or `"yes, not that one"` were previously matched by naive substring detection (`"yes" in user_text`) and interpreted as unconditional approval grants, risking unauthorized mutations.

**Why**: `detect_confirmation()` in `src/approval.py` checked positive confirmation tokens without scanning for hedging qualifiers (`but`, `except`, `not`, `if`, `unless`, `however`, `instead`, `cheaper`, `different`).

**Fix**: Enhanced `detect_confirmation()` to detect hedge words. If any hedge word is detected in the input, the confirmation status is downgraded to `"ambiguous"`, which prompts the user for clarification rather than executing the proposed action.

**Regression test**:
- File: `tests/test_approval_edge_cases.py::test_orchestrator_hedged_confirmation_rejects_execution`
- Command: `uv run pytest tests/test_approval_edge_cases.py -k "test_orchestrator_hedged" -v`

---

## Bug 5: Metrics Duplication for Non-Model Deterministic Turns in Live Collector

**What broke**: The live metrics summary reported 17 turns and ~90s model execution time, which failed to reconcile with the total wall-clock runtime of 55.5s.

**Why**: Turns following approval (e.g., `"yes"`), price re-approvals, and direct memory commands were executed deterministically in application code without invoking the LLM. The metrics collector was grabbing `model_events[-1]` unconditionally, recording the previous model turn's latency and token counts multiple times.

**Fix**: Updated `LiveMetricsCollector.record()` to filter strictly for model events matching the current turn (`turn_id == orch.turn_id`). Non-model turns are skipped, and each model turn is persisted as a distinct record to `tests/live/results/live_metrics.jsonl`. Total model time (76.08s, 67.93s, 74.50s across 3 runs) now matches the wall-clock test runtime within 0.25 seconds.

**Regression test**:
- Script: `scripts/live_summary.py`
- Command: `uv run python scripts/live_summary.py`

---

## Bug 6: Illegal State Machine Transition (`COMPARING -> COMPARING`) After Declining Action

**What broke**: In Streamlit and multi-turn conversations, after declining a proposed action in `AWAITING_APPROVAL` (which moves the agent to `COMPARING`), sending any follow-up message (e.g. asking for alternative providers or re-entering the prompt) crashed with `InvalidTransitionError: Cannot transition from COMPARING to COMPARING: Illegal transition from COMPARING to COMPARING`.

**Why**: `_dispatch_state_action` in `src/orchestrator.py` checked `if self.sm.current_state in (State.IDLE, State.CLARIFYING):` before transitioning to `SEARCHING`. Because `State.COMPARING` was missing from this guard, the agent skipped transitioning into `SEARCHING`, remained in `COMPARING`, and then executed `self.sm.transition(State.COMPARING, trigger="providers_found")`. Self-transitions are not in `VALID_TRANSITIONS` and raised `InvalidTransitionError`.

**Fix**: Updated `_dispatch_state_action` in `src/orchestrator.py` to `if self.sm.current_state in (State.IDLE, State.CLARIFYING, State.COMPARING): self.sm.transition(State.SEARCHING, trigger="slots_complete")`. This enables the workflow to cleanly transition `COMPARING -> SEARCHING -> COMPARING -> AWAITING_APPROVAL`.

**Regression test**:
- File: `tests/test_orchestrator.py::test_orchestrator_followup_search_after_declined_approval`
- Command: `uv run pytest tests/test_orchestrator.py -k "test_orchestrator_followup_search_after_declined_approval" -v`

---

## Bug 7: Premature LLM Booking Confirmation Claims in `CLARIFYING` State

**What broke**: During multi-turn slot filling in manual testing and live LLM interactions, when a user responded "yes" to an intermediate clarifying question (e.g., verifying budget after providing time preference), the LLM generated conversational prose claiming the service was booked and confirmed (`"Great! I have booked a plumber... The booking is confirmed"`). Because required slots (such as `area`) were still `None`, the backend FSM correctly prevented any tool execution and remained in `CLARIFYING`. However, returning the model's message misled the user into thinking the task was finished, causing subsequent inputs (`"no"`, `"anything else?"`) to repeatedly echo the false confirmation while waiting for `area`.

**Why**: In `_dispatch_state_action()` in `src/orchestrator.py`, if `self.slots.missing_required()` was non-empty, the orchestrator evaluated `return resp.message or f"To help arrange this, could you please specify your {', '.join(missing)}?"`. If `resp.message` was populated, it was passed verbatim to the user without checking whether the model hallucinated a booking action or omitted asking for the missing required slots.

**Fix**:
1. In `src/orchestrator.py` (`_dispatch_state_action`): Added a hallucination guardrail. If required slots remain missing and `resp.message` contains premature confirmation/booking claims (e.g. `"have booked"`, `"booking is confirmed"`, `"has been confirmed"`) or mentions booking/confirmation without asking for the missing slots, the orchestrator intercepts the message and returns an explicit clarification request: `f"To help arrange {article} {self.slots.service or 'service'}, could you please specify your {', '.join(missing)}?"`.
2. In `src/models/ollama.py`: Clarified the system instruction to specify that `service`, `area`, and `date_range` are required before searching or booking, and explicitly instructed the model to never claim a booking is made or confirmed until a provider is explicitly approved by the user.

**Regression test**:
- File: `tests/test_orchestrator.py::test_orchestrator_guardrail_prevents_hallucinated_booking_in_clarifying`
- Command: `uv run pytest tests/test_orchestrator.py -k "test_orchestrator_guardrail_prevents_hallucinated_booking_in_clarifying" -v`

---

## Bug 8: Missing `(State.DONE, State.PROACTIVE)` Transition on Upstream Cancellation

**What broke**: When an upstream provider cancellation event occurred on an already-confirmed booking (`State.DONE`), the orchestrator's proactive handler crashed with `src.state_machine.InvalidTransitionError: Cannot transition from DONE to PROACTIVE: Illegal transition from DONE to PROACTIVE`.

**Why**: `VALID_TRANSITIONS` in `src/state_machine.py` only permitted `(State.DONE, State.IDLE)`. When `ProviderCancelledEvent` was polled from the event queue after a booking reached `State.DONE`, `_handle_proactive_event` attempted `self.sm.transition(State.PROACTIVE, trigger="provider_cancelled")`, which was rejected as an illegal transition.

**Fix**: Added `(State.DONE, State.PROACTIVE)` to `VALID_TRANSITIONS` in `src/state_machine.py`.

**Regression test**:
- File: `tests/test_state_machine.py::test_done_to_proactive_transition`
- Command: `uv run pytest tests/test_state_machine.py -k "test_done_to_proactive_transition" -v`



