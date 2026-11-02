"""Unit tests for Budget execution bounds, exit messages, and JSONL Tracer with PII redaction."""

import json
import os
import tempfile
import pytest

from src.budget import Budget
from src.tracer import Tracer, redact_pii


def test_budget_model_turns_exhaustion() -> None:
    """Test model turns limit triggers exhaustion."""
    budget = Budget(max_model_turns=3)
    assert not budget.exhausted()[0]

    budget.record_turn()
    budget.record_turn()
    assert not budget.exhausted()[0]

    budget.record_turn()
    is_exh, reason = budget.exhausted()
    assert is_exh
    assert reason == "model_turn_limit"

    msg = budget.get_exit_message(reason, {"summary": "collecting slots"})
    assert "reached my processing limit" in msg


def test_budget_tool_calls_exhaustion() -> None:
    """Test tool calls limit triggers exhaustion."""
    budget = Budget(max_tool_calls=2)
    budget.record_tool_call("search_providers")
    assert not budget.exhausted()[0]

    budget.record_tool_call("hold_slot")
    is_exh, reason = budget.exhausted()
    assert is_exh
    assert reason == "tool_call_limit"

    msg = budget.get_exit_message(reason, {"summary": "held slot"})
    assert "used all available tool calls" in msg


def test_budget_wall_clock_timeout() -> None:
    """Test wall clock timeout detection."""
    budget = Budget(wall_clock_deadline_seconds=10.0, start_time=1000.0)
    # Check within deadline
    assert not budget.exhausted(current_time=1005.0)[0]

    # Check after deadline
    is_exh, reason = budget.exhausted(current_time=1015.0)
    assert is_exh
    assert reason == "wall_clock_timeout"

    msg = budget.get_exit_message(reason, {"status_check_result": "no booking placed"})
    assert "taking too long" in msg


def test_per_tool_retry_caps() -> None:
    """Test individual tool retry limits."""
    budget = Budget()
    assert budget.tool_retries_remaining("book") == 3

    budget.record_tool_call("book")
    assert budget.tool_retries_remaining("book") == 2

    budget.record_tool_call("book")
    budget.record_tool_call("book")
    assert budget.tool_retries_remaining("book") == 0


def test_tracer_jsonl_output_and_pii_redaction() -> None:
    """Test Tracer outputs valid JSONL and scrubs phone numbers and API keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        trace_path = os.path.join(tmpdir, "test_trace.jsonl")
        tracer = Tracer(trace_file=trace_path)

        tracer.log_event(
            event_type="user_input",
            turn_id=1,
            message="Call me at 9876543210 using key sk-1234567890abcdef1234567890abcdef",
        )

        assert os.path.exists(trace_path)
        with open(trace_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "user_input"
        assert "[PHONE_REDACTED]" in entry["message"]
        assert "[API_KEY_REDACTED]" in entry["message"]
        assert "9876543210" not in entry["message"]
        assert "sk-1234567890" not in entry["message"]
