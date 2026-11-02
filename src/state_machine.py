"""State machine governing conversational workflow, guards, and tool allowlists."""

from enum import Enum
from typing import Any


class State(str, Enum):
    """Workflow states in the errand arrangement process."""

    IDLE = "IDLE"
    CLARIFYING = "CLARIFYING"
    SEARCHING = "SEARCHING"
    COMPARING = "COMPARING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    HOLDING = "HOLDING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    RESCHEDULING = "RESCHEDULING"
    PROACTIVE = "PROACTIVE"


class InvalidTransitionError(Exception):
    """Raised when an illegal or unguarded state transition is attempted."""


ALLOWED_TOOLS_MAP: dict[State, list[str]] = {
    State.IDLE: ["list_bookings"],
    State.CLARIFYING: ["list_bookings"],
    State.SEARCHING: ["search_providers", "get_provider", "list_bookings"],
    State.COMPARING: ["get_provider", "list_bookings"],
    State.AWAITING_APPROVAL: [],
    State.HOLDING: ["hold_slot", "get_booking_status"],
    State.EXECUTING: ["book", "cancel", "reschedule", "get_booking_status"],
    State.VERIFYING: ["get_booking_status"],
    State.DONE: [],
    State.FAILED: [],
    State.CANCELLING: ["cancel", "get_booking_status"],
    State.RESCHEDULING: ["reschedule", "get_booking_status", "search_providers"],
    State.PROACTIVE: ["search_providers", "get_provider", "list_bookings"],
}

# Explicit set of allowed (from_state, to_state) transitions
VALID_TRANSITIONS: set[tuple[State, State]] = {
    # IDLE transitions
    (State.IDLE, State.CLARIFYING),
    (State.IDLE, State.SEARCHING),
    (State.IDLE, State.CANCELLING),
    (State.IDLE, State.RESCHEDULING),
    (State.IDLE, State.PROACTIVE),
    # CLARIFYING transitions
    (State.CLARIFYING, State.CLARIFYING),
    (State.CLARIFYING, State.SEARCHING),
    (State.CLARIFYING, State.FAILED),
    # SEARCHING transitions
    (State.SEARCHING, State.COMPARING),
    (State.SEARCHING, State.CLARIFYING),
    (State.SEARCHING, State.FAILED),
    # COMPARING transitions
    (State.COMPARING, State.AWAITING_APPROVAL),
    (State.COMPARING, State.SEARCHING),
    (State.COMPARING, State.FAILED),
    # AWAITING_APPROVAL transitions
    (State.AWAITING_APPROVAL, State.HOLDING),
    (State.AWAITING_APPROVAL, State.COMPARING),
    (State.AWAITING_APPROVAL, State.FAILED),
    # HOLDING transitions
    (State.HOLDING, State.EXECUTING),
    (State.HOLDING, State.AWAITING_APPROVAL),
    (State.HOLDING, State.FAILED),
    # EXECUTING transitions
    (State.EXECUTING, State.VERIFYING),
    (State.EXECUTING, State.DONE),
    (State.EXECUTING, State.SEARCHING),
    (State.EXECUTING, State.HOLDING),
    (State.EXECUTING, State.FAILED),
    # VERIFYING transitions
    (State.VERIFYING, State.DONE),
    (State.VERIFYING, State.SEARCHING),
    (State.VERIFYING, State.FAILED),
    # CANCELLING transitions
    (State.CANCELLING, State.CANCELLING),
    (State.CANCELLING, State.DONE),
    (State.CANCELLING, State.FAILED),
    # RESCHEDULING transitions
    (State.RESCHEDULING, State.AWAITING_APPROVAL),
    (State.RESCHEDULING, State.RESCHEDULING),
    (State.RESCHEDULING, State.DONE),
    (State.RESCHEDULING, State.FAILED),
    # PROACTIVE transitions
    (State.PROACTIVE, State.AWAITING_APPROVAL),
    (State.PROACTIVE, State.FAILED),
}


class StateMachine:
    """Manages the explicit state model and transitions for an errand workflow."""

    def __init__(self, initial_state: State = State.IDLE) -> None:
        """Initialize StateMachine with an initial state.

        Args:
            initial_state: Starting state, defaults to IDLE.
        """
        self._current_state = initial_state
        self._history: list[tuple[State, State, str]] = []

    @property
    def current_state(self) -> State:
        """Return the current workflow state.

        Returns:
            State: Current state enum.
        """
        return self._current_state

    def allowed_tools(self, state: State | None = None) -> list[str]:
        """Return allowed tool names for the given or current state.

        Args:
            state: Optional state to inspect. Defaults to current state.

        Returns:
            list[str]: Whitelist of allowed tool names in this state.
        """
        target = state or self._current_state
        return list(ALLOWED_TOOLS_MAP.get(target, []))

    def can_transition(
        self, to_state: State, **context: Any
    ) -> tuple[bool, str]:
        """Evaluate if a transition to the target state is allowed by guards.

        Args:
            to_state: Target state to transition into.
            **context: Contextual parameters (e.g. has_approval, price_drift).

        Returns:
            tuple[bool, str]: (allowed, reason_message).
        """
        # Universal transitions
        if to_state in (State.FAILED, State.IDLE):
            return True, f"Universal transition to {to_state.value} allowed"

        pair = (self._current_state, to_state)
        if pair not in VALID_TRANSITIONS:
            return False, f"Illegal transition from {self._current_state.value} to {to_state.value}"

        # Guard checks for specific transitions
        if self._current_state == State.HOLDING and to_state == State.EXECUTING:
            if context.get("price_drift", False):
                return False, "Price drift detected; requires re-approval before execution"

        return True, "Transition allowed"

    def transition(
        self, to_state: State, trigger: str = "", **context: Any
    ) -> State:
        """Execute a state transition if guards permit.

        Args:
            to_state: Target state enum.
            trigger: Trigger description or reason for transition.
            **context: Guard context flags.

        Returns:
            State: Newly active state.

        Raises:
            InvalidTransitionError: If the transition is prohibited by guards.
        """
        allowed, reason = self.can_transition(to_state, **context)
        if not allowed:
            raise InvalidTransitionError(
                f"Cannot transition from {self._current_state.value} to {to_state.value}: {reason}"
            )

        from_state = self._current_state
        self._current_state = to_state
        self._history.append((from_state, to_state, trigger))
        return self._current_state

    def get_history(self) -> list[tuple[State, State, str]]:
        """Return full history of transitions recorded in this state machine.

        Returns:
            list[tuple[State, State, str]]: List of (from_state, to_state, trigger).
        """
        return list(self._history)
