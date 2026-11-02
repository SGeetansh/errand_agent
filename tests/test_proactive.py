"""Unit tests for proactive follow-up on provider cancellation."""

import pytest

from src.event_queue import EventQueue
from src.memory import MemoryStore
from src.models.scripted import ScriptedModel
from src.orchestrator import Orchestrator
from src.schemas import ProviderCancelledEvent
from src.state_machine import State
from src.tools.mock_env import MockEnvironment
from src.tools.scenario_config import ScenarioConfig


def test_proactive_cancellation_detection_and_fresh_approval() -> None:
    """Test proactive cancellation event detection, replacement recommendation, and fresh approval."""
    env = MockEnvironment(ScenarioConfig())
    mem = MemoryStore(db_path=":memory:")
    mem.store("preferred_area", "Koramangala", provenance="explicit", confirmed=1)
    queue = EventQueue()
    llm = ScriptedModel()

    orch = Orchestrator(
        llm=llm,
        mock_env=env,
        memory=mem,
        event_queue=queue,
    )

    # Inject cancellation event
    event = ProviderCancelledEvent(
        booking_id="BKG-AC-002",
        provider_id="P-AC-01",
        provider_name="CoolAir Services",
        service="ac_service",
        original_slot="2024-01-14T15:00:00",
        cancelled_at="2024-01-14T08:00:00",  # 7 hours notice -> High urgency
        reason="Technician illness",
    )
    queue.push(event)

    # First turn: agent notices event immediately, searches replacement, proposes it
    reply1 = orch.run_turn("hello")
    assert "High Urgency" in reply1
    assert "has been cancelled by the provider" in reply1
    assert "Would you like me to book this provider?" in reply1
    assert orch.sm.current_state == State.AWAITING_APPROVAL

    # Second turn: user confirms with 'yes' -> Fresh approval grant generated and booking placed
    reply2 = orch.run_turn("yes, please book it")
    assert "Success! Your booking" in reply2
    assert orch.sm.current_state == State.DONE
