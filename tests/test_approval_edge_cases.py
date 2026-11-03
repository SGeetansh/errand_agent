"""Offline unit tests covering approval edge cases and strict grant matching."""

import pytest

from src.approval import detect_confirmation
from src.models.scripted import ScriptedModel
from src.orchestrator import Orchestrator
from src.state_machine import State
from src.tools.mock_env import MockEnvironment
from src.tools.scenario_config import ScenarioConfig


def test_detect_confirmation_hedged_expressions() -> None:
    """Test that hedging, conditional or contradictory statements are detected as ambiguous."""
    assert detect_confirmation("yes but make it cheaper") == "ambiguous"
    assert detect_confirmation("yes, not that one") == "ambiguous"
    assert detect_confirmation("yes if it includes warranty") == "ambiguous"
    assert detect_confirmation("yes, however can we do 5pm") == "ambiguous"
    assert detect_confirmation("sure, but different provider") == "ambiguous"
    assert detect_confirmation("maybe") == "ambiguous"

    # Unconditional confirmations remain confirmed
    assert detect_confirmation("yes") == "confirmed"
    assert detect_confirmation("yes, please") == "confirmed"
    assert detect_confirmation("ok") == "confirmed"
    assert detect_confirmation("go ahead") == "confirmed"
    assert detect_confirmation("proceed") == "confirmed"


def test_orchestrator_hedged_confirmation_rejects_execution() -> None:
    """Test 'yes but make it cheaper' and 'yes, not that one' do not execute side effects."""
    env = MockEnvironment(ScenarioConfig(force_book_outcome="success"))
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                "message": "Found plumbers.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)

    # Turn 1: Propose provider
    orch.run_turn("Find plumber in Koramangala this week")
    assert orch.sm.current_state == State.AWAITING_APPROVAL
    initial_bookings_count = len(env.list_bookings("user_default"))

    # Turn 2: Hedged response "yes but make it cheaper"
    reply2 = orch.run_turn("yes but make it cheaper")
    assert orch.sm.current_state == State.AWAITING_APPROVAL
    assert "Please explicitly confirm with 'yes' or 'no'" in reply2
    assert len(env.list_bookings("user_default")) == initial_bookings_count

    # Turn 3: "maybe"
    reply3 = orch.run_turn("maybe")
    assert orch.sm.current_state == State.AWAITING_APPROVAL
    assert "Please explicitly confirm with 'yes' or 'no'" in reply3
    assert len(env.list_bookings("user_default")) == initial_bookings_count

    # Turn 4: "yes, not that one"
    reply4 = orch.run_turn("yes, not that one")
    assert orch.sm.current_state == State.AWAITING_APPROVAL
    assert "Please explicitly confirm with 'yes' or 'no'" in reply4
    assert len(env.list_bookings("user_default")) == initial_bookings_count


def test_orchestrator_ok_after_price_drift() -> None:
    """Test 'ok' confirms a proposal whose price shifted during hold."""
    env = MockEnvironment(ScenarioConfig(price_drift=150.0, force_book_outcome="success"))
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                "message": "Found plumbers.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)

    orch.run_turn("Find plumber in Koramangala this week")
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Turn 2: User says yes -> price drift detected -> returns to AWAITING_APPROVAL
    reply2 = orch.run_turn("yes")
    assert "Notice: The quoted price is" in reply2
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Turn 3: User says "ok" to approve the updated price proposal
    reply3 = orch.run_turn("ok")
    assert orch.sm.current_state == State.DONE
    assert "Success! Your booking" in reply3


def test_orchestrator_yes_after_invalidation() -> None:
    """Test sending 'yes' after a proposal was declined/invalidated executes nothing."""
    env = MockEnvironment(ScenarioConfig())
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                "message": "Found plumbers.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)

    orch.run_turn("Find plumber in Koramangala this week")
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Decline
    orch.run_turn("no")
    assert orch.sm.current_state == State.COMPARING
    assert orch.proposed_action is None

    # Force transition back or attempt approval
    orch.sm._current_state = State.AWAITING_APPROVAL
    reply = orch.run_turn("yes")
    assert "No action is currently pending approval" in reply
    assert orch.sm.current_state == State.IDLE


def test_orchestrator_yes_sent_twice_idempotent() -> None:
    """Test 'yes' sent twice does not duplicate booking."""
    env = MockEnvironment(ScenarioConfig(force_book_outcome="success"))
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                "message": "Found plumbers.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)

    orch.run_turn("Find plumber in Koramangala this week")
    reply1 = orch.run_turn("yes")
    assert orch.sm.current_state == State.DONE
    assert "Success! Your booking" in reply1
    count_after_first = len(env.list_bookings("user_default"))

    # Second "yes" in DONE state
    reply2 = orch.run_turn("yes")
    assert orch.sm.current_state == State.DONE
    assert "already confirmed and active" in reply2
    assert len(env.list_bookings("user_default")) == count_after_first


def test_orchestrator_yes_on_cancel_proposal() -> None:
    """Test 'yes' when proposal was a cancel executes cancellation, NOT a booking."""
    env = MockEnvironment(ScenarioConfig())
    orch = Orchestrator(llm=ScriptedModel(), mock_env=env)

    # Pick a confirmed active booking
    confirmed_bkgs = [b for b in env.list_bookings("user_default") if b.status == "confirmed"]
    assert len(confirmed_bkgs) > 0
    active_bkg = confirmed_bkgs[0]
    initial_count = len(env.list_bookings("user_default"))

    # Propose cancel
    reply1 = orch.run_turn(f"Cancel booking {active_bkg.booking_id}")
    assert orch.sm.current_state == State.AWAITING_APPROVAL
    assert orch.proposed_action["action"] == "cancel"

    # Confirm
    reply2 = orch.run_turn("yes")
    assert orch.sm.current_state == State.DONE
    assert "successfully cancelled" in reply2

    # Verify no new booking was created
    assert len(env.list_bookings("user_default")) == initial_count
    # Booking status should now be cancelled
    updated_bkg = [b for b in env.list_bookings("user_default") if b.booking_id == active_bkg.booking_id][0]
    assert updated_bkg.status == "cancelled"
