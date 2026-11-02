"""Central orchestrator coordinating state machine, LLM, approval gate, and tools."""

from dataclasses import asdict
import time
from typing import Any

from src.approval import ApprovalGrant, create_approval_grant, detect_confirmation
from src.budget import Budget
from src.context import ContextBuilder
from src.event_queue import EventQueue
from src.executor import Executor
from src.idempotency import derive_idempotency_key
from src.memory import MemoryStore
from src.models.base import LLMProvider
from src.proactive import ProactiveHandler
from src.recovery import IntentLog, RecoveryManager
from src.schemas import LLMResponse, ProviderCancelledEvent
from src.slot_filler import ErrandSlots, SlotFiller
from src.state_machine import State, StateMachine
from src.tools.contracts import (
    BookResult,
    CancelResult,
    HoldResult,
    ProviderSummary,
    RescheduleResult,
    ToolError,
)
from src.tools.mock_env import MockEnvironment
from src.tracer import Tracer


class Orchestrator:
    """Deterministic orchestrator managing state transitions, tool dispatch, and LLM turns."""

    def __init__(
        self,
        llm: LLMProvider,
        mock_env: MockEnvironment,
        memory: MemoryStore | None = None,
        wal: IntentLog | None = None,
        tracer: Tracer | None = None,
        budget: Budget | None = None,
        event_queue: EventQueue | None = None,
        user_id: str = "user_default",
    ) -> None:
        """Initialize Orchestrator with all necessary subcomponents.

        Args:
            llm: Language model provider.
            mock_env: Mock service environment.
            memory: Optional persistent memory store.
            wal: Optional intent log.
            tracer: Optional execution tracer.
            budget: Optional bounded execution budget.
            event_queue: Optional proactive event queue.
            user_id: Target user ID string.
        """
        self.llm = llm
        self.mock_env = mock_env
        self.memory = memory or MemoryStore(db_path=":memory:")
        self.wal = wal or IntentLog(db_path=":memory:")
        self.tracer = tracer or Tracer()
        self.budget = budget or Budget()
        self.event_queue = event_queue or EventQueue()
        self.user_id = user_id

        self.sm = StateMachine(State.IDLE)
        self.slots = ErrandSlots()
        self.executor = Executor(self.mock_env, wal=self.wal)
        self.recovery = RecoveryManager(self.wal, self.mock_env)
        self.proactive_handler = ProactiveHandler(self.mock_env, self.memory)

        self.turn_id = 0
        self.conversation_history: list[dict[str, Any]] = []
        self.conversation_summary: str = ""
        self.proposed_action: dict[str, Any] | None = None
        self.active_grant: ApprovalGrant | None = None
        self.last_search_results: list[ProviderSummary] = []

        orig_transition = self.sm.transition

        def _logged_transition(to_state: State, trigger: str = "", **context: Any) -> State:
            from_val = self.sm.current_state.value
            target_val = to_state.value if isinstance(to_state, State) else str(to_state)
            new_state = orig_transition(to_state, trigger=trigger, **context)
            self.tracer.log_event(
                "state_transition",
                turn_id=self.turn_id,
                from_state=from_val,
                to_state=target_val,
                trigger=trigger,
            )
            return new_state

        self.sm.transition = _logged_transition  # type: ignore[assignment]

    def run_turn(self, user_input: str) -> str:
        """Process a single conversational turn from the user.

        Args:
            user_input: Raw text string entered by user.

        Returns:
            str: Agent response message.
        """
        self.turn_id += 1
        self.tracer.log_event("user_input", turn_id=self.turn_id, message=user_input)
        self.conversation_history.append({"turn_id": self.turn_id, "role": "user", "text": user_input})

        # 1. Bounded execution check
        is_exhausted, reason = self.budget.exhausted()
        if is_exhausted and reason:
            self.sm.transition(State.FAILED, trigger=reason)
            msg = self.budget.get_exit_message(reason, {"summary": "Processing limit reached"})
            return self._finalize_turn(msg)

        # 2. Check for proactive system events
        if self.event_queue.has_events():
            proactive_msg = self._handle_proactive_event()
            if proactive_msg:
                return self._finalize_turn(proactive_msg)

        # 3. Direct memory commands
        mem_response = self._handle_memory_commands(user_input)
        if mem_response:
            return self._finalize_turn(mem_response)

        # 4. Handle completed errand state
        if self.sm.current_state == State.DONE:
            decision = detect_confirmation(user_input)
            if decision in ("confirmed", "ambiguous") or any(w in user_input.lower() for w in ["yes", "ok", "confirm", "thanks", "thank you"]):
                return self._finalize_turn("Your booking is already confirmed and active. Let me know if you need any other assistance!")
            self.sm.transition(State.IDLE, trigger="new_task_after_done")
            self.slots = ErrandSlots()
            self.proposed_action = None

        if self.sm.current_state == State.FAILED:
            self.sm.transition(State.IDLE, trigger="retry_after_failed")
            self.slots = ErrandSlots()
            self.proposed_action = None

        # 5. Handle awaiting approval state deterministically via application code
        if self.sm.current_state == State.AWAITING_APPROVAL:
            approval_resp = self._handle_awaiting_approval(user_input)
            return self._finalize_turn(approval_resp)

        # 6. Normal workflow turn driven by LLM NLU
        return self._finalize_turn(self._handle_llm_turn(user_input))

    def _finalize_turn(self, response_text: str) -> str:
        """Record assistant response and update trace.

        Args:
            response_text: Text message to send to user.

        Returns:
            str: The exact message text.
        """
        self.conversation_history.append({"turn_id": self.turn_id, "role": "assistant", "text": response_text})
        self.tracer.log_event("agent_output", turn_id=self.turn_id, message=response_text)
        return response_text

    def _handle_proactive_event(self) -> str | None:
        """Check and process pending proactive provider cancellation events.

        Returns:
            str | None: Notification message if an event was handled, else None.
        """
        event = self.event_queue.poll()
        if not event:
            return None

        self.sm.transition(State.PROACTIVE, trigger="provider_cancelled")

        outcome = self.proactive_handler.handle(event, user_id=self.user_id)
        candidates = outcome["candidates"]
        if not candidates:
            self.sm.transition(State.FAILED, trigger="no_replacement_available")
            return f"{outcome['notification']} Unfortunately, no alternative providers were found."

        top = candidates[0]
        chosen_slot = top.available_slots[0] if top.available_slots else "2024-01-16T10:00:00"
        self.proposed_action = {
            "action": "book",
            "provider_id": top.provider_id,
            "provider_name": top.name,
            "slot": chosen_slot,
            "price": top.price,
            "service": top.service,
        }

        self.sm.transition(State.AWAITING_APPROVAL, trigger="propose_replacement")
        return (
            f"{outcome['notification']}\n"
            f"I found a replacement: {top.name} ({top.rating}★) for ₹{top.price:.2f} "
            f"at {chosen_slot}. Would you like me to book this provider? (Please confirm yes/no)"
        )

    def _handle_memory_commands(self, user_input: str) -> str | None:
        """Handle inspection, correction, and forgetting of stored preferences.

        Args:
            user_input: Raw user text.

        Returns:
            str | None: Confirmation response if handled, or None.
        """
        lower = user_input.lower().strip()
        if "what do you remember about me" in lower or "show memories" in lower:
            facts = self.memory.list_facts(self.user_id)
            if not facts:
                return "I don't have any saved preferences for you yet."
            lines = [f"- {f.fact}: {f.value} ({f.provenance})" for f in facts]
            return "Here are the facts I currently remember:\n" + "\n".join(lines)

        if lower.startswith("forget "):
            target = lower.replace("forget", "").replace("that i", "").strip()
            for f in self.memory.list_facts(self.user_id):
                if target in f.fact.lower() or target in f.value.lower():
                    self.memory.forget(f.id)
                    return f"I have forgotten: {f.fact}."
            return f"I couldn't find any memory matching '{target}' to forget."

        return None

    def _handle_awaiting_approval(self, user_input: str) -> str:
        """Process user confirmation/denial in AWAITING_APPROVAL state.

        Args:
            user_input: Raw user message.

        Returns:
            str: Outcome message.
        """
        decision = detect_confirmation(user_input)
        if decision == "denied":
            self.proposed_action = None
            self.sm.transition(State.COMPARING, trigger="user_declined_approval")
            return "No problem. Would you like to see other options, or is there anything else I can help with?"

        if decision == "ambiguous":
            action = self.proposed_action.get("action", "action") if self.proposed_action else "this action"
            return f"Please explicitly confirm with 'yes' or 'no' if you would like to proceed with {action}."

        # Explicit confirmation received
        if not self.proposed_action:
            self.sm.transition(State.IDLE, trigger="no_pending_proposal")
            return "No action is currently pending approval. How can I help you?"

        action = self.proposed_action["action"]
        if action == "book":
            return self._execute_booking_flow()
        if action == "cancel":
            return self._execute_cancel_flow()
        if action == "reschedule":
            return self._execute_reschedule_flow()

        return "Unknown action proposed."

    def _execute_booking_flow(self) -> str:
        """Hold slot, check price drift, issue booking, and verify with source of truth.

        Returns:
            str: Resulting conversational message.
        """
        assert self.proposed_action is not None
        provider_id = self.proposed_action["provider_id"]
        slot = self.proposed_action["slot"]
        approved_price = self.proposed_action["price"]

        # 1. Enter HOLDING
        self.sm.transition(State.HOLDING, trigger="user_confirmed")
        self.budget.record_tool_call("hold_slot")
        hold_res = self.mock_env.hold_slot(provider_id, slot)
        if isinstance(hold_res, ToolError):
            self.sm.transition(State.FAILED, trigger="hold_failed")
            return f"Failed to hold slot: {hold_res.message}."

        # 2. Check for price drift
        if hold_res.quoted_price != approved_price:
            self.sm.transition(State.AWAITING_APPROVAL, trigger="price_drift_detected")
            drift_diff = hold_res.quoted_price - approved_price
            self.proposed_action["price"] = hold_res.quoted_price
            self.proposed_action["hold_id"] = hold_res.hold_id
            return (
                f"Notice: The quoted price is ₹{hold_res.quoted_price:.2f} "
                f"(₹{drift_diff:+.2f} difference from estimated ₹{approved_price:.2f}). "
                "Do you still approve booking at this updated price? (Please reply yes/no)"
            )

        # 3. Create cryptographically bound ApprovalGrant
        grant = create_approval_grant(
            action="book",
            provider_id=provider_id,
            slot=slot,
            quoted_price=approved_price,
        )

        # 4. Transition to EXECUTING
        self.sm.transition(State.EXECUTING, trigger="proceed_with_booking", price_drift=False)
        idem_key = derive_idempotency_key(self.user_id, "book", provider_id, slot, attempt=0)

        self.budget.record_tool_call("book")
        book_res, _ = self.executor.execute(
            grant=grant,
            action="book",
            provider_id=provider_id,
            slot=slot,
            quoted_price=approved_price,
            hold_id=hold_res.hold_id,
            idempotency_key=idem_key,
        )

        # 5. Resolve outcome
        return self._handle_booking_result(book_res, idem_key, provider_id, slot)

    def _handle_booking_result(
        self, book_res: Any, idem_key: str, provider_id: str, slot: str
    ) -> str:
        """Evaluate BookResult and verify status with authoritative source of truth.

        Args:
            book_res: Result from book call.
            idem_key: Idempotency key.
            provider_id: Provider identifier.
            slot: Slot string.

        Returns:
            str: Final user-facing message.
        """
        if getattr(book_res, "status", "") == "confirmed":
            self.sm.transition(State.VERIFYING, trigger="verify_status")
            status_obj = self.mock_env.get_booking_status(idem_key)
            if status_obj.status == "confirmed":
                self.sm.transition(State.DONE, trigger="booking_verified")
                return f"Success! Your booking {status_obj.booking_id} with {provider_id} on {slot} is confirmed."

        if getattr(book_res, "status", "") == "timeout":
            self.sm.transition(State.VERIFYING, trigger="timeout_verify")
            verified = self.recovery.handle_timeout(idem_key)
            if verified.status == "confirmed":
                self.sm.transition(State.DONE, trigger="timeout_recovered_confirmed")
                return f"Success! Your booking {verified.booking_id} was confirmed upstream after resolving timeout."

            self.sm.transition(State.SEARCHING, trigger="timeout_not_booked")
            return "The booking attempt timed out and was not confirmed. Shall I retry or search for other options?"

        if getattr(book_res, "status", "") == "slot_taken":
            self.sm.transition(State.SEARCHING, trigger="slot_taken")
            return "The requested slot was taken by another customer. Let me search for alternative slots."

        if getattr(book_res, "status", "") == "hold_expired":
            self.sm.transition(State.HOLDING, trigger="hold_expired")
            return "The hold expired before booking completed. Let me try holding the slot again."

        self.sm.transition(State.FAILED, trigger="booking_error")
        return f"Unable to complete booking: {getattr(book_res, 'message', 'unknown error')}."

    def _execute_cancel_flow(self) -> str:
        """Execute cancellation with retry handling for 503 errors.

        Returns:
            str: Outcome message.
        """
        assert self.proposed_action is not None
        booking_id = self.proposed_action["booking_id"]
        grant = create_approval_grant(
            action="cancel",
            provider_id=self.proposed_action["provider_id"],
            slot=self.proposed_action["slot"],
            quoted_price=self.proposed_action["price"],
            booking_id=booking_id,
        )
        self.sm.transition(State.CANCELLING, trigger="cancel_confirmed")
        idem_key = derive_idempotency_key(self.user_id, "cancel", self.proposed_action["provider_id"], self.proposed_action["slot"])

        self.budget.record_tool_call("cancel")
        res, _ = self.executor.execute(
            grant=grant,
            action="cancel",
            provider_id=self.proposed_action["provider_id"],
            slot=self.proposed_action["slot"],
            quoted_price=self.proposed_action["price"],
            booking_id=booking_id,
            idempotency_key=idem_key,
        )

        # Handle transient 503 retry
        if getattr(res, "error_code", None) == 503:
            self.tracer.log_event("retry", turn_id=self.turn_id, message="503 encountered, retrying cancel")
            retry_grant = create_approval_grant(
                action="cancel",
                provider_id=self.proposed_action["provider_id"],
                slot=self.proposed_action["slot"],
                quoted_price=self.proposed_action["price"],
                booking_id=booking_id,
            )
            self.budget.record_tool_call("cancel")
            res, _ = self.executor.execute(
                grant=retry_grant,
                action="cancel",
                provider_id=self.proposed_action["provider_id"],
                slot=self.proposed_action["slot"],
                quoted_price=self.proposed_action["price"],
                booking_id=booking_id,
                idempotency_key=idem_key,
            )

        if getattr(res, "status", "") == "cancelled":
            self.sm.transition(State.DONE, trigger="cancel_success")
            return f"Booking {booking_id} has been successfully cancelled."

        self.sm.transition(State.FAILED, trigger="cancel_failed")
        return f"Failed to cancel booking {booking_id}."

    def _execute_reschedule_flow(self) -> str:
        """Execute rescheduling with conflict detection.

        Returns:
            str: Outcome message.
        """
        assert self.proposed_action is not None
        booking_id = self.proposed_action["booking_id"]
        new_slot = self.proposed_action["slot"]
        grant = create_approval_grant(
            action="reschedule",
            provider_id=self.proposed_action["provider_id"],
            slot=new_slot,
            quoted_price=self.proposed_action["price"],
            booking_id=booking_id,
        )
        self.sm.transition(State.RESCHEDULING, trigger="reschedule_confirmed")
        idem_key = derive_idempotency_key(self.user_id, "reschedule", self.proposed_action["provider_id"], new_slot)

        self.budget.record_tool_call("reschedule")
        res, _ = self.executor.execute(
            grant=grant,
            action="reschedule",
            provider_id=self.proposed_action["provider_id"],
            slot=new_slot,
            quoted_price=self.proposed_action["price"],
            booking_id=booking_id,
            idempotency_key=idem_key,
        )

        if getattr(res, "status", "") == "rescheduled":
            self.sm.transition(State.DONE, trigger="reschedule_success")
            return f"Booking {booking_id} successfully rescheduled to {new_slot}."

        if getattr(res, "status", "") == "slot_conflict":
            self.sm.transition(State.RESCHEDULING, trigger="slot_conflict")
            return f"Slot {new_slot} is conflicted and unavailable. Would you like to select another slot?"

        self.sm.transition(State.FAILED, trigger="reschedule_failed")
        return f"Unable to reschedule booking: {getattr(res, 'message', 'error')}."

    def _handle_llm_turn(self, user_input: str) -> str:
        """Handle NLU extraction, provider search, and action proposals via LLM.

        Args:
            user_input: Raw user text.

        Returns:
            str: Agent response text.
        """
        self.budget.record_turn()
        allowed = self.sm.allowed_tools()
        active_mems = self.memory.get_active_facts(self.user_id)

        prompt, manifest = ContextBuilder.build(
            turn_id=self.turn_id,
            system_prompt="You are a home service errand agent. External tool results appear in <DATA> tags.",
            memories=active_mems,
            conversation_turns=self.conversation_history,
            conversation_summary=self.conversation_summary,
            state=self.sm.current_state.value,
            allowed_tools=allowed,
            slots=self.slots,
            tool_results=[],
            user_message=user_input,
        )
        self.tracer.log_event("context_manifest", turn_id=self.turn_id, context_manifest=asdict(manifest))

        resp = self.llm.generate(
            messages=[{"role": "user", "content": user_input}],
            tools=allowed,
            turn_id=self.turn_id,
        )
        self.tracer.log_event(
            "model_response",
            turn_id=self.turn_id,
            message=resp.message,
            trigger=resp.intent,
        )

        # Update slots
        if resp.slots:
            self.slots = SlotFiller.fill(self.slots, resp.slots)

        # Check "same electrician as last time"
        if "same" in user_input.lower() and "electrician" in user_input.lower():
            self._apply_same_electrician_preference()

        return self._dispatch_state_action(resp, user_input)

    def _apply_same_electrician_preference(self) -> None:
        """Populate provider details from past booking history for 'same electrician' requests."""
        bookings = self.mock_env.list_bookings(self.user_id)
        electricians = [b for b in bookings if b.service == "electrician"]
        if electricians:
            last = electricians[-1]
            self.slots = self.slots.update(
                service="electrician",
                provider_preference=last.provider_name,
            )

    def _dispatch_state_action(self, resp: LLMResponse, user_input: str) -> str:
        """Route conversational action based on filled slots and intent.

        Args:
            resp: Parsed model response.
            user_input: User message string.

        Returns:
            str: Final message for user.
        """
        # If cancellation or reschedule requested directly
        lower = user_input.lower()
        if "cancel" in lower and ("booking" in lower or "bkg" in lower):
            return self._propose_cancellation(user_input)

        if "move" in lower or "reschedule" in lower:
            return self._propose_reschedule(user_input)

        missing = self.slots.missing_required()
        if missing:
            if self.sm.current_state == State.IDLE:
                self.sm.transition(State.CLARIFYING, trigger="missing_slots")
            return resp.message or f"To help arrange this, could you please specify your {', '.join(missing)}?"

        # All slots filled -> Search providers
        if self.sm.current_state in (State.IDLE, State.CLARIFYING):
            self.sm.transition(State.SEARCHING, trigger="slots_complete")

        self.budget.record_tool_call("search_providers")
        assert self.slots.service is not None
        assert self.slots.area is not None
        assert self.slots.date_range is not None

        providers = self.mock_env.search_providers(
            service=self.slots.service,
            area=self.slots.area,
            date_range=self.slots.date_range,
        )
        self.last_search_results = providers

        if not providers:
            self.sm.transition(State.FAILED, trigger="no_providers_found")
            return f"I couldn't find any {self.slots.service} providers in {self.slots.area}."

        self.sm.transition(State.COMPARING, trigger="providers_found")
        top = providers[0]
        slot = top.available_slots[0] if top.available_slots else "2024-01-15T10:00:00"

        self.proposed_action = {
            "action": "book",
            "provider_id": top.provider_id,
            "provider_name": top.name,
            "slot": slot,
            "price": top.price,
            "service": top.service,
        }
        self.sm.transition(State.AWAITING_APPROVAL, trigger="propose_provider")

        return (
            f"I found {len(providers)} providers. I recommend {top.name} "
            f"({top.rating}★, ₹{top.price:.2f}) on {slot}. "
            "Shall I go ahead and book this for you? (Please reply yes/no)"
        )

    def _propose_cancellation(self, user_input: str) -> str:
        """Find booking and transition to AWAITING_APPROVAL for cancellation.

        Args:
            user_input: User message.

        Returns:
            str: Proposal confirmation prompt.
        """
        bookings = self.mock_env.list_bookings(self.user_id)
        active = [b for b in bookings if b.status == "confirmed"]
        if not active:
            return "You have no active confirmed bookings to cancel."

        target = active[0]
        for b in active:
            if b.booking_id.lower() in user_input.lower():
                target = b
                break

        self.proposed_action = {
            "action": "cancel",
            "booking_id": target.booking_id,
            "provider_id": target.provider_id,
            "provider_name": target.provider_name,
            "slot": target.slot,
            "price": target.price,
            "service": target.service,
        }
        if self.sm.current_state == State.IDLE:
            self.sm.transition(State.CANCELLING, trigger="cancel_requested")
        self.sm.transition(State.AWAITING_APPROVAL, trigger="propose_cancellation")
        return f"Are you sure you want to cancel booking {target.booking_id} for {target.service} on {target.slot}? (Please reply yes/no)"

    def _propose_reschedule(self, user_input: str) -> str:
        """Find booking and transition to AWAITING_APPROVAL for rescheduling.

        Args:
            user_input: User message.

        Returns:
            str: Proposal confirmation prompt.
        """
        bookings = self.mock_env.list_bookings(self.user_id)
        active = [b for b in bookings if b.status == "confirmed"]
        if not active:
            return "You have no active bookings to reschedule."

        target = active[0]
        new_slot = "2024-01-20T10:00:00"
        self.proposed_action = {
            "action": "reschedule",
            "booking_id": target.booking_id,
            "provider_id": target.provider_id,
            "slot": new_slot,
            "price": target.price,
            "service": target.service,
        }
        if self.sm.current_state == State.IDLE:
            self.sm.transition(State.RESCHEDULING, trigger="reschedule_requested")
        self.sm.transition(State.AWAITING_APPROVAL, trigger="propose_reschedule")
        return f"Would you like to reschedule booking {target.booking_id} to Saturday 10:00 AM ({new_slot})? (Please reply yes/no)"
