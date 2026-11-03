"""Unit tests for StateMachine, transitions, guards, and ErrandSlots."""

import pytest

from src.slot_filler import ErrandSlots, SlotFiller
from src.state_machine import (
    ALLOWED_TOOLS_MAP,
    InvalidTransitionError,
    State,
    StateMachine,
)


def test_errand_slots_missing_and_complete() -> None:
    """Test required slots identification and completion checks."""
    slots = ErrandSlots()
    assert set(slots.missing_required()) == {"service", "area", "date_range"}
    assert not slots.is_complete()

    partial = slots.update(service="plumber", area="Koramangala")
    assert partial.missing_required() == ["date_range"]
    assert not partial.is_complete()

    complete = partial.update(date_range="this week")
    assert complete.missing_required() == []
    assert complete.is_complete()


def test_errand_slots_update_preserves_unrelated_context() -> None:
    """Test that correcting one slot preserves all other unrelated slot values."""
    initial = ErrandSlots(
        service="plumber",
        area="Koramangala",
        date_range="this week",
        time_preference="mornings only",
        budget_max=1500.0,
        specific_issue="kitchen tap",
    )

    # Correct area only
    corrected = initial.update(area="HSR Layout")
    assert corrected.area == "HSR Layout"
    assert corrected.service == "plumber"
    assert corrected.date_range == "this week"
    assert corrected.time_preference == "mornings only"
    assert corrected.budget_max == 1500.0
    assert corrected.specific_issue == "kitchen tap"


def test_slot_filler_fill_and_correct() -> None:
    """Test SlotFiller helper utility methods."""
    slots = ErrandSlots()
    filled = SlotFiller.fill(slots, {"service": "electrician", "budget_max": 2000.0})
    assert filled.service == "electrician"
    assert filled.budget_max == 2000.0

    corrected = SlotFiller.correct_field(filled, "budget_max", 2500.0)
    assert corrected.budget_max == 2500.0
    assert corrected.service == "electrician"


def test_allowed_tools_per_state() -> None:
    """Test that every state exposes the exact allowed tools per design spec."""
    sm = StateMachine()

    assert sm.allowed_tools(State.IDLE) == ["list_bookings"]
    assert sm.allowed_tools(State.CLARIFYING) == ["list_bookings"]
    assert set(sm.allowed_tools(State.SEARCHING)) == {"search_providers", "get_provider", "list_bookings"}
    assert set(sm.allowed_tools(State.COMPARING)) == {"get_provider", "list_bookings"}
    assert sm.allowed_tools(State.AWAITING_APPROVAL) == []
    assert set(sm.allowed_tools(State.HOLDING)) == {"hold_slot", "get_booking_status"}
    assert set(sm.allowed_tools(State.EXECUTING)) == {"book", "cancel", "reschedule", "get_booking_status"}
    assert sm.allowed_tools(State.VERIFYING) == ["get_booking_status"]
    assert sm.allowed_tools(State.DONE) == []
    assert sm.allowed_tools(State.FAILED) == []
    assert set(sm.allowed_tools(State.CANCELLING)) == {"cancel", "get_booking_status"}
    assert set(sm.allowed_tools(State.RESCHEDULING)) == {"reschedule", "get_booking_status", "search_providers"}
    assert set(sm.allowed_tools(State.PROACTIVE)) == {"search_providers", "get_provider", "list_bookings"}


def test_idle_transitions() -> None:
    """Test all legal transitions originating from IDLE."""
    for target in [State.CLARIFYING, State.SEARCHING, State.CANCELLING, State.RESCHEDULING, State.PROACTIVE]:
        sm = StateMachine(State.IDLE)
        assert sm.transition(target) == target


def test_clarifying_transitions() -> None:
    """Test transitions from CLARIFYING."""
    sm = StateMachine(State.CLARIFYING)
    assert sm.transition(State.CLARIFYING) == State.CLARIFYING
    assert sm.transition(State.SEARCHING) == State.SEARCHING

    sm = StateMachine(State.CLARIFYING)
    assert sm.transition(State.FAILED) == State.FAILED


def test_searching_transitions() -> None:
    """Test transitions from SEARCHING."""
    sm = StateMachine(State.SEARCHING)
    assert sm.transition(State.COMPARING) == State.COMPARING

    sm = StateMachine(State.SEARCHING)
    assert sm.transition(State.CLARIFYING) == State.CLARIFYING

    sm = StateMachine(State.SEARCHING)
    assert sm.transition(State.FAILED) == State.FAILED


def test_comparing_transitions() -> None:
    """Test transitions from COMPARING."""
    sm = StateMachine(State.COMPARING)
    assert sm.transition(State.AWAITING_APPROVAL) == State.AWAITING_APPROVAL

    sm = StateMachine(State.COMPARING)
    assert sm.transition(State.SEARCHING) == State.SEARCHING


def test_awaiting_approval_transitions() -> None:
    """Test transitions from AWAITING_APPROVAL."""
    sm = StateMachine(State.AWAITING_APPROVAL)
    assert sm.transition(State.HOLDING) == State.HOLDING

    sm = StateMachine(State.AWAITING_APPROVAL)
    assert sm.transition(State.COMPARING) == State.COMPARING


def test_holding_transitions_and_guards() -> None:
    """Test HOLDING transitions and price drift guard."""
    sm = StateMachine(State.HOLDING)
    # Price drift guard active -> should fail
    with pytest.raises(InvalidTransitionError):
        sm.transition(State.EXECUTING, price_drift=True)

    # Price drift guard inactive -> should succeed
    assert sm.transition(State.EXECUTING, price_drift=False) == State.EXECUTING

    # Price drift redirects back to approval
    sm2 = StateMachine(State.HOLDING)
    assert sm2.transition(State.AWAITING_APPROVAL) == State.AWAITING_APPROVAL


def test_executing_transitions() -> None:
    """Test all transitions from EXECUTING."""
    for target in [State.VERIFYING, State.DONE, State.SEARCHING, State.HOLDING, State.FAILED]:
        sm = StateMachine(State.EXECUTING)
        assert sm.transition(target) == target


def test_verifying_transitions() -> None:
    """Test transitions from VERIFYING."""
    for target in [State.DONE, State.SEARCHING, State.FAILED]:
        sm = StateMachine(State.VERIFYING)
        assert sm.transition(target) == target


def test_cancelling_and_rescheduling_transitions() -> None:
    """Test transitions for CANCELLING and RESCHEDULING."""
    # Cancelling
    sm_c = StateMachine(State.CANCELLING)
    assert sm_c.transition(State.CANCELLING) == State.CANCELLING
    assert sm_c.transition(State.DONE) == State.DONE

    # Rescheduling
    sm_r = StateMachine(State.RESCHEDULING)
    assert sm_r.transition(State.RESCHEDULING) == State.RESCHEDULING
    assert sm_r.transition(State.AWAITING_APPROVAL) == State.AWAITING_APPROVAL

    sm_r2 = StateMachine(State.RESCHEDULING)
    assert sm_r2.transition(State.DONE) == State.DONE


def test_proactive_transitions() -> None:
    """Test transitions from PROACTIVE."""
    sm = StateMachine(State.PROACTIVE)
    assert sm.transition(State.AWAITING_APPROVAL) == State.AWAITING_APPROVAL

    sm2 = StateMachine(State.PROACTIVE)
    assert sm2.transition(State.FAILED) == State.FAILED


def test_universal_transitions() -> None:
    """Test that any state can transition to FAILED or IDLE."""
    for s in State:
        sm = StateMachine(s)
        assert sm.transition(State.FAILED) == State.FAILED

        sm2 = StateMachine(s)
        assert sm2.transition(State.IDLE) == State.IDLE


def test_illegal_transitions_raise_error() -> None:
    """Test that invalid transitions raise InvalidTransitionError."""
    sm = StateMachine(State.IDLE)
    # Direct jump to EXECUTING is strictly prohibited
    with pytest.raises(InvalidTransitionError):
        sm.transition(State.EXECUTING)

    sm2 = StateMachine(State.AWAITING_APPROVAL)
    # Cannot jump directly to EXECUTING without HOLDING
    with pytest.raises(InvalidTransitionError):
        sm2.transition(State.EXECUTING)


def test_done_to_proactive_transition() -> None:
    """Test legal transition from DONE to PROACTIVE when provider cancels confirmed booking."""
    sm = StateMachine(State.DONE)
    assert sm.transition(State.PROACTIVE) == State.PROACTIVE

