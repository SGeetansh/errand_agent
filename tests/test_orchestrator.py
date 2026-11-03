"""Unit tests for Orchestrator conversational flows, approval gating, and price drift handling."""

import pytest

from src.models.scripted import ScriptedModel
from src.orchestrator import Orchestrator
from src.state_machine import State
from src.tools.mock_env import MockEnvironment
from src.tools.scenario_config import ScenarioConfig


def test_orchestrator_happy_path_booking() -> None:
    """Test full happy-path booking workflow from request to verified confirmation."""
    env = MockEnvironment(ScenarioConfig(force_book_outcome="success"))
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                "message": "Searching for plumbers.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)

    # Turn 1: Search and propose provider
    reply1 = orch.run_turn("Find a plumber in Koramangala this week under 1500")
    assert "Shall I go ahead and book this for you?" in reply1
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Turn 2: Explicit confirmation -> hold -> book -> verified DONE
    reply2 = orch.run_turn("yes")
    assert "Success! Your booking" in reply2
    assert orch.sm.current_state == State.DONE


def test_orchestrator_price_drift_re_approval() -> None:
    """Test that price drift during hold redirects back to AWAITING_APPROVAL for new consent."""
    env = MockEnvironment(ScenarioConfig(price_drift=200.0, force_book_outcome="success"))
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                "message": "Searching plumbers.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)

    # Turn 1: Initial proposal at search price (1200)
    orch.run_turn("I need a plumber in Koramangala this week")
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Turn 2: User confirms -> hold detects price drift (+200 -> 1400)
    reply2 = orch.run_turn("yes")
    assert "Notice: The quoted price is ₹1400.00" in reply2
    assert "Do you still approve" in reply2
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Turn 3: User confirms new price -> booking succeeds
    reply3 = orch.run_turn("yes, that's fine")
    assert "Success! Your booking" in reply3
    assert orch.sm.current_state == State.DONE


def test_orchestrator_user_declined_approval() -> None:
    """Test declining proposed action transitions to COMPARING without execution."""
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

    reply_no = orch.run_turn("no")
    assert "No problem" in reply_no
    assert orch.sm.current_state == State.COMPARING
    assert orch.proposed_action is None


def test_orchestrator_followup_search_after_declined_approval() -> None:
    """Regression test: follow-up turn after declining transitions COMPARING -> SEARCHING cleanly."""
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
        },
        {
            "turn": 3,
            "input_contains": "other",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                "message": "Found alternative options.",
            },
        },
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)

    # Turn 1: Initial search -> AWAITING_APPROVAL
    orch.run_turn("Find plumber in Koramangala this week")
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Turn 2: User declines -> transitions to COMPARING
    reply_no = orch.run_turn("no")
    assert orch.sm.current_state == State.COMPARING

    # Turn 3: User asks for other options while in COMPARING
    # Must cleanly transition COMPARING -> SEARCHING -> COMPARING -> AWAITING_APPROVAL without InvalidTransitionError
    reply_opt = orch.run_turn("show me other options")
    assert orch.sm.current_state == State.AWAITING_APPROVAL
    assert orch.proposed_action is not None


def test_orchestrator_same_electrician_history() -> None:
    """Test 'same electrician as last time' inspects booking history."""
    env = MockEnvironment(ScenarioConfig())
    rules = [
        {
            "turn": 1,
            "input_contains": "electrician",
            "response": {
                "intent": "search_providers",
                "slots": {"area": "Koramangala", "date_range": "tomorrow"},
                "message": "Looking for electrician.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)

    orch.run_turn("Book the same electrician as last time in Koramangala tomorrow")
    assert orch.slots.service == "electrician"
    assert orch.slots.provider_preference == "Sparky Electrical Works"


def test_orchestrator_memory_inspection_command() -> None:
    """Test 'what do you remember about me' returns stored active facts."""
    env = MockEnvironment(ScenarioConfig())
    orch = Orchestrator(llm=ScriptedModel(), mock_env=env)
    orch.memory.store("preferred_area", "Indiranagar", provenance="explicit", confirmed=1)

    reply = orch.run_turn("what do you remember about me?")
    assert "preferred_area: Indiranagar" in reply


def test_orchestrator_guardrail_prevents_hallucinated_booking_in_clarifying() -> None:
    """Test guardrail overrides hallucinated booking confirmation when required slots are missing."""
    env = MockEnvironment(ScenarioConfig())
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "clarify",
                "slots": {"service": "plumber", "date_range": "tomorrow morning", "budget_max": 5000.0},
                "message": "Great! I have booked a plumber for your kitchen tap tomorrow morning. The booking is confirmed.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)
    reply = orch.run_turn("Find a plumber for tomorrow morning under 5000")

    assert orch.sm.current_state == State.CLARIFYING
    assert "specify your area" in reply
    assert "have booked" not in reply
    assert "booking is confirmed" not in reply
    assert orch.proposed_action is None
    assert orch.budget.tool_calls_used == 0


def test_orchestrator_shows_all_providers_before_recommendation() -> None:
    """Test search response lists all compared providers and then the top recommendation."""
    env = MockEnvironment(ScenarioConfig())
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                "message": "Searching for plumbers.",
            },
        }
    ]
    orch = Orchestrator(llm=ScriptedModel(rules), mock_env=env)
    reply = orch.run_turn("Find a plumber in Koramangala this week")

    assert orch.sm.current_state == State.AWAITING_APPROVAL
    assert "I found 5 providers:" in reply
    for prov in orch.last_search_results:
        assert prov.name in reply
    assert "I recommend QuickFix Plumbing" in reply
    assert "Shall I go ahead and book this for you?" in reply


