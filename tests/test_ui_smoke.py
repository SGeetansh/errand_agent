"""Smoke tests for the Streamlit UI using AppTest."""

import os
import pytest

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from src.state_machine import State


def test_ui_app_boots_without_exception() -> None:
    """1. App boots with no exception and initializes session state."""
    app_file = os.path.abspath("ui/app.py")
    at = AppTest.from_file(app_file, default_timeout=15)
    at.run()

    assert not at.exception
    assert "orchestrator" in at.session_state
    assert at.session_state["orchestrator"].sm.current_state == State.IDLE


def test_ui_happy_path_booking_reaches_done() -> None:
    """2. A happy-path conversation with scripted model reaches DONE and adds a booking."""
    app_file = os.path.abspath("ui/app.py")
    at = AppTest.from_file(app_file, default_timeout=15)
    at.run()
    assert not at.exception

    # Turn 1: User requests plumber in Koramangala
    at.chat_input[0].set_value("Find plumber in Koramangala this week").run()
    assert not at.exception
    orch = at.session_state["orchestrator"]
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Turn 2: User confirms booking
    at.chat_input[0].set_value("yes").run()
    assert not at.exception
    orch = at.session_state["orchestrator"]
    assert orch.sm.current_state == State.DONE

    # Verify booking appears in bookings data (2 seeded past + 1 newly added = 3)
    bookings = orch.mock_env.list_bookings(orch.user_id)
    assert len(bookings) == 3
    confirmed = [b for b in bookings if b.status == "confirmed"]
    assert len(confirmed) >= 1
    assert any(b.service == "plumber" for b in confirmed)


def test_ui_trace_view_contains_state_transition() -> None:
    """3. After first user message, the trace view contains at least one state_transition event."""
    app_file = os.path.abspath("ui/app.py")
    at = AppTest.from_file(app_file, default_timeout=15)
    at.run()
    assert not at.exception

    at.chat_input[0].set_value("Find plumber in Koramangala this week").run()
    assert not at.exception

    orch = at.session_state["orchestrator"]
    entries = orch.tracer.entries
    state_transitions = [e for e in entries if e.get("event_type") == "state_transition"]
    assert len(state_transitions) >= 1

    targets = [st.get("to_state") for st in state_transitions]
    assert any(t in ("SEARCHING", "COMPARING", "AWAITING_APPROVAL") for t in targets)


def test_ui_simulate_crash_restart_no_duplicate_booking() -> None:
    """4. 'Simulate crash + restart' mid-booking reconciles without producing duplicate bookings."""
    app_file = os.path.abspath("ui/app.py")
    at = AppTest.from_file(app_file, default_timeout=15)
    at.run()
    assert not at.exception

    # User initiates booking request
    at.chat_input[0].set_value("Find plumber in Koramangala this week").run()
    orch = at.session_state["orchestrator"]
    initial_bookings_count = len(orch.mock_env.list_bookings(orch.user_id))

    # Log in-flight pending intent before crash simulation
    key = "idem_test_crash_recovery_001"
    orch.wal.log_intent(
        idempotency_key=key,
        action="book",
        provider_id=orch.proposed_action["provider_id"],
        slot=orch.proposed_action["slot"],
        quoted_price=orch.proposed_action["price"],
        grant_id="grant_crash_001",
    )

    # Trigger Crash + Restart button in sidebar
    crash_btn = [b for b in at.button if "Crash + Restart" in b.label][0]
    crash_btn.click().run()
    assert not at.exception

    # Reconciled banner should be displayed in chat column
    info_messages = [i.value for i in at.info]
    assert any("Reconciled 1 pending intent(s)" in m for m in info_messages)

    # Verify no duplicate booking was created
    new_orch = at.session_state["orchestrator"]
    final_bookings_count = len(new_orch.mock_env.list_bookings(new_orch.user_id))
    assert final_bookings_count == initial_bookings_count
