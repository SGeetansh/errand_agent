# Errand Agent - Home Services Conversational Assistant

A production-grade, highly resilient conversational agent for home-service errands (plumber, AC servicing, electrician) designed according to robust distributed systems principles. Built with a deterministic finite state machine, cryptographic approval gate, SQLite WAL idempotency and recovery, persistent privacy-preserving memory, and bounded context management.

---

## 🌟 Key Architectural Highlights

1. **Deterministic 13-State Workflow Machine**:
   - The LLM acts solely as an NLU slot extractor. All state transitions, guards, and tool allowlists are enforced in application code.
   - Mutating side-effects (`book`, `cancel`, `reschedule`) can never be called directly or hallucinated by the model.

2. **Cryptographic Approval Gate**:
   - Side effects require explicit confirmation from raw user input (via deterministic pattern matching, never unverified model output).
   - Upon confirmation, an `ApprovalGrant` cryptographically bound to `(action, provider_id, slot, price, booking_id)` via HMAC/SHA-256 is minted.
   - The `Executor` consumes and invalidates the grant upon execution. Price drift revokes the grant and triggers re-approval.

3. **Idempotency & SQLite Write-Ahead Intent Log (WAL)**:
   - Deterministic idempotency keys derived via SHA-256: `derive_idempotency_key(user_id, action, provider_id, slot, attempt)`.
   - All mutating intents are logged to an SQLite WAL table before external dispatch.
   - Indeterminate timeouts trigger status verification via `get_booking_status()`—resolving upstream state without duplicate bookings. Startup reconciliation recovers in-flight transactions.

4. **Persistent Memory with SQL-Level Privacy Filters**:
   - Explicit vs. inferred memory tracking.
   - Forgotten facts (`valid_to IS NOT NULL`) and unconfirmed inferred facts (`confirmed = 0`) are filtered out directly in SQL queries, guaranteeing zero leakage into context prompts.

5. **Token-Bounded Context & Adversarial Defense**:
   - Strict 4000-token context budgeting using `tiktoken` (`cl100k_base`).
   - All external data (provider details, reviews) are sanitized to neutralize prompt injection attacks and wrapped in `<DATA>` blocks.

6. **Observability & Budget Tracking**:
   - Structured JSONL tracing with RFC 3339 timestamps and automatic PII redaction (phone numbers, email addresses).
   - Multi-dimensional execution bounding: max 25 model turns, max 50 tool calls, 300.0s wall-clock timeout, and per-tool retry caps.

---

## 🛠️ Prerequisites & Setup

### Environment
- **Platform**: macOS (optimized for Apple Silicon, tested on MacBook Air M4, 24 GB memory)
- **Python**: Version 3.11+
- **Dependency Manager**: [`uv`](https://github.com/astral-sh/uv) (fast Python package manager)
- **Local LLM (Optional)**: [Ollama](https://ollama.ai) with `qwen2.5:7b` (for live model execution)

### Installation
Clone the repository and sync dependencies with `uv`:

```bash
# Clone repository
git clone https://github.com/SGeetansh/errand_agent
cd errand_agent

# Sync dependencies and create virtual environment via uv
uv sync
```

---

## 🚀 Running the Interactive CLI

Launch the interactive chat interface directly through the terminal:

### 1. Deterministic / Offline Mode (Recommended for testing)
Runs with the built-in `ScriptedModel`, allowing you to walk through errands deterministically without running an LLM server:
```bash
uv run python -m src.cli --model scripted
```

### 2. Local Ollama LLM Mode
Runs with local Ollama (`qwen2.5:7b`):
```bash
# Ensure ollama is running with qwen2.5:7b
ollama run qwen2.5:7b

# Launch CLI connected to Ollama
uv run python -m src.cli --model ollama
```

---

## 🖥️ Streamlit UI (Manual Testing & Demo Recording)

A side-by-side Streamlit interface designed for manual testing and video recording. It serves as a thin view layer over the Orchestrator with zero duplicate business logic.

### 1. Installation & Dependencies
The UI dependencies are isolated under an optional group so core tests and CI remain lean:
```bash
uv sync --extra ui
```

### 2. Launching the App
```bash
uv run streamlit run ui/app.py
```

### 3. Interface Overview
- **Sidebar**:
  - **Model Selector**: Switch between `scripted` and `ollama` (`qwen2.5:7b`) with live server reachability detection.
  - **Scenario Presets**: Dropdown populated from eval harness configs (Happy path, Timeout recovery, Price drift, Slot conflict, Transient 503, Prompt injection, Proactive cancellation). Automatically creates an isolated temporary SQLite database on every preset switch.
  - **Manual Overrides**: Override outcome simulation, price drift amount, review injection, and random seeds.
  - **Special Actions**:
    - **New Session**: Clean reset with a fresh temporary database.
    - **Crash + Restart**: Discards the in-memory orchestrator, recreates it from the same SQLite disk file, and triggers startup reconciliation mid-booking.
    - **Trigger Provider Cancellation**: Enqueues an upstream cancellation event for the latest booking and triggers the proactive replacement flow unprompted.
  - **Live Status**: State machine state, remaining turns, remaining tool calls, wall-clock timeout countdown.
- **Left Column (Chat)**:
  - Interactive multi-turn chat (`st.chat_input` and `st.chat_message`).
  - Read-only **Pending Action Card** when in `AWAITING_APPROVAL` with quick-fill `Insert 'yes'` and `Insert 'no'` buttons that route through normal confirmation detection.
- **Right Column (Observability Tabs)**:
  - **Trace**: Live tail of JSONL execution events with color-coded badges and full raw `.jsonl` download.
  - **Context**: Per-turn `ContextManifest` showing token allocations against the 4000-token budget and memory inclusion/exclusion reasoning.
  - **Memory**: Interactive table of stored facts with in-place correction and forgetting. Includes a dedicated greyed-out section displaying facts never sent to the model (unconfirmed inferred and forgotten facts).
  - **Bookings**: Real-time view of `list_bookings` output.
  - **WAL**: Dump of SQLite `intent_log` showing idempotency keys, actions, and status updates.

---

## 🧪 Running Tests & Evaluation Suite

### Run All Unit and Integration Tests (100 Tests)
```bash
uv run pytest -v
```

### Run Evaluation Scenarios & Generate Eval Table (27 Scenarios)
The test suite executes all 27 evaluation scenarios specified in `DESIGN.md` against deterministic mock environments and auto-generates `eval_table.md`:
```bash
uv run pytest tests/test_eval.py -v
```

---

## 📊 Evaluation Results Summary

All 27 scenarios pass with 100% success rate. The table below is auto-generated by the test harness in [`eval_table.md`](eval_table.md):

| Category | Scenarios Covered | Status |
|---|---|:---:|
| **Basic Workflows** | S01 (Happy path), S02 (Multi-turn slots), S03 (Context preservation), S04 (Approval gating) | ✅ PASS |
| **Safety & Injections** | S05 (Review injection), S06 (System prefix injection), S24 (SQL injection) | ✅ PASS |
| **Price Drift & Approvals** | S07 (Price drift detected), S08 (Price drift accepted), S09 (Repeated confirmations), S14 (Material change) | ✅ PASS |
| **Fault Tolerance & Recovery**| S10 (Timeout actually booked), S11 (Timeout not booked), S12 (Slot conflict), S13 (503 retry), S18 (Restart mid-booking), S25 (Tool failure), S26 (Hold expired) | ✅ PASS |
| **Proactive & Historical** | S15 (Proactive replacement found), S16 (No replacement found), S17 (Same electrician as last time) | ✅ PASS |
| **Memory Management** | S19 (Memory correction), S20 (Memory forget), S21 (Unconfirmed inferred fact exclusion) | ✅ PASS |
| **Resource Limits** | S22 (Model turns cap), S23 (4000-token context budget), S27 (Tool calls cap) | ✅ PASS |

See [`eval_table.md`](eval_table.md) for full scenario-by-scenario notes.

---

## 📁 Repository Deliverables Index

- [`DESIGN.md`](DESIGN.md): Master design document detailing interfaces, schemas, state machine tables, and constraints.
- [`DESIGN_NOTE.md`](DESIGN_NOTE.md): 1-page condensed design note with Mermaid state diagram and annotated S10 trace.
- [`BUGS.md`](BUGS.md): Log of 3 real engineering bugs encountered during development, root causes, fixes, and regression tests.
- [`eval_table.md`](eval_table.md): Auto-generated evaluation table from the test harness.
- [`traces/s10_timeout_after_success.jsonl`](traces/s10_timeout_after_success.jsonl): Trace artifact for Scenario 10 timeout recovery.
- [`AI_TRANSCRIPT.md`](AI_TRANSCRIPT.md): Log of AI assistant interactions and implementation trajectory.
- `src/`: Complete modular implementation adhering to SOLID/DRY/KISS.
- `tests/`: 100 unit and integration tests with 100% pass rate.
