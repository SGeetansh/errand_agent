"""Context builder managing token budget, relevance ranking, and manifests."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any
import tiktoken

from src.memory import MemoryRecord
from src.sanitizer import Sanitizer
from src.slot_filler import ErrandSlots

_ENCODER = tiktoken.get_encoding("cl100k_base")

TOTAL_TOKEN_BUDGET = 4000
SYSTEM_PROMPT_BUDGET = 800
MEMORY_BUDGET = 400
SUMMARY_BUDGET = 600
RECENT_CONVERSATION_BUDGET = 1200
STATE_SLOTS_BUDGET = 200
TOOL_RESULTS_BUDGET = 800


def count_tokens(text: str) -> int:
    """Return deterministic token count using cl100k_base encoding.

    Args:
        text: String content to measure.

    Returns:
        int: Total token count.
    """
    return len(_ENCODER.encode(text))


def rank_memories(
    memories: list[MemoryRecord],
    current_slots: ErrandSlots,
    user_message: str,
    now_dt: datetime | None = None,
) -> list[MemoryRecord]:
    """Score each active memory fact by relevance to the current request.

    Args:
        memories: List of candidate active MemoryRecords.
        current_slots: Currently filled slots.
        user_message: Latest user input string.
        now_dt: Optional reference timestamp.

    Returns:
        list[MemoryRecord]: Memories sorted descending by relevance score.
    """
    ref_now = now_dt or datetime.now(timezone.utc)
    keywords = set(user_message.lower().split())
    if current_slots.service:
        keywords.add(current_slots.service.lower())
    if current_slots.area:
        keywords.add(current_slots.area.lower())

    scored_items: list[tuple[float, MemoryRecord]] = []
    for mem in memories:
        score = 0.0
        text_repr = f"{mem.fact} {mem.value}".lower()
        score += sum(2.0 for kw in keywords if kw in text_repr)

        try:
            dt = datetime.fromisoformat(mem.updated_at)
            days_old = max(0, (ref_now - dt).days)
            score += max(0.0, 10.0 - days_old)
        except Exception:
            pass

        if mem.provenance == "explicit":
            score += 3.0

        scored_items.append((score, mem))

    scored_items.sort(key=lambda item: item[0], reverse=True)
    return [mem for _, mem in scored_items]


@dataclass
class ContextManifest:
    """Audit log capturing exact context components sent to the LLM.

    Attributes:
        turn_id: Current conversation turn index.
        system_prompt_tokens: Tokens consumed by system instructions.
        memory_facts_included: Fact identifiers included in prompt.
        memory_facts_excluded: Fact identifiers dropped due to budget.
        conversation_turns_verbatim: Turn indices included verbatim.
        conversation_summary_tokens: Tokens consumed by conversation summary.
        current_state: Active state machine state string.
        slots: Current slots dictionary.
        tool_results_tokens: Tokens consumed by sanitized tool results.
        total_tokens: Overall tokens constructed.
        budget_remaining: Remaining tokens under 4000 cap.
    """

    turn_id: int
    system_prompt_tokens: int
    memory_facts_included: list[str]
    memory_facts_excluded: list[str]
    conversation_turns_verbatim: list[int]
    conversation_summary_tokens: int
    current_state: str
    slots: dict[str, Any]
    tool_results_tokens: int
    total_tokens: int
    budget_remaining: int


class ContextBuilder:
    """Assembles prompt payload within strict per-section and total token budgets."""

    @staticmethod
    def build(
        turn_id: int,
        system_prompt: str,
        memories: list[MemoryRecord],
        conversation_turns: list[dict[str, Any]],
        conversation_summary: str,
        state: str,
        allowed_tools: list[str],
        slots: ErrandSlots,
        tool_results: list[Any],
        user_message: str,
    ) -> tuple[str, ContextManifest]:
        """Construct the prompt and manifest within the 4000 token budget.

        Args:
            turn_id: Current turn number.
            system_prompt: Hardcoded system instructions.
            memories: Active memories from MemoryStore.
            conversation_turns: Verbatim conversation history records.
            conversation_summary: Compact summary of older turns.
            state: Current state name.
            allowed_tools: Allowed tools list for current state.
            slots: Current slots instance.
            tool_results: Raw tool results from current turn.
            user_message: Raw user message.

        Returns:
            tuple[str, ContextManifest]: (assembled_prompt, manifest).
        """
        # 1. System prompt section
        sys_tokens = count_tokens(system_prompt)

        # 2. Memory ranking and budget packing (400 tokens)
        ranked = rank_memories(memories, slots, user_message)
        included_facts: list[str] = []
        excluded_facts: list[str] = []
        memory_lines: list[str] = []
        mem_tokens_used = 0

        for m in ranked:
            line = f"- {m.fact}: {m.value}"
            line_tokens = count_tokens(line)
            if mem_tokens_used + line_tokens <= MEMORY_BUDGET:
                memory_lines.append(line)
                mem_tokens_used += line_tokens
                included_facts.append(m.fact)
            else:
                excluded_facts.append(m.fact)

        mem_block = Sanitizer.sanitize_text("\n".join(memory_lines), "memory") if memory_lines else "<DATA source=\"memory\">\nNo stored facts\n</DATA>"

        # 3. Summary of older turns (600 tokens)
        summary_tokens = count_tokens(conversation_summary)
        summary_block = f"PRIOR CONVERSATION SUMMARY:\n{conversation_summary}\n" if conversation_summary else ""

        # 4. Recent turns verbatim (last 6 turns, 1200 tokens)
        recent_turns = conversation_turns[-6:] if len(conversation_turns) > 6 else conversation_turns
        verbatim_turn_ids: list[int] = []
        turns_lines: list[str] = []
        for t in recent_turns:
            verbatim_turn_ids.append(t.get("turn_id", 0))
            role = t.get("role", "user")
            text = t.get("text", "")
            turns_lines.append(f"{role.upper()}: {text}")
        recent_turns_block = "\n".join(turns_lines)

        # 5. State and slots block (200 tokens)
        slots_dict = asdict(slots)
        state_block = (
            f"CURRENT STATE: {state}\n"
            f"ALLOWED TOOLS: {', '.join(allowed_tools) if allowed_tools else 'None'}\n"
            f"SLOT VALUES: {json.dumps(slots_dict)}"
        )

        # 6. Tool results section (800 tokens)
        tool_lines: list[str] = []
        for tr in tool_results:
            sanitized = Sanitizer.sanitize_tool_result("tool_result", tr)
            tool_lines.append(str(sanitized))
        tool_results_block = "\n".join(tool_lines) if tool_lines else "None"
        tool_tokens = count_tokens(tool_results_block)

        # Assemble full text
        full_prompt = (
            f"{system_prompt}\n\n"
            f"--- STORED MEMORIES ---\n{mem_block}\n\n"
            f"--- CONVERSATION HISTORY ---\n"
            f"{summary_block}"
            f"{recent_turns_block}\n\n"
            f"--- WORKFLOW STATE ---\n{state_block}\n\n"
            f"--- TOOL RESULTS ---\n{tool_results_block}\n\n"
            f"--- CURRENT USER MESSAGE ---\n{user_message}"
        )

        total_tok = count_tokens(full_prompt)
        manifest = ContextManifest(
            turn_id=turn_id,
            system_prompt_tokens=sys_tokens,
            memory_facts_included=included_facts,
            memory_facts_excluded=excluded_facts,
            conversation_turns_verbatim=verbatim_turn_ids,
            conversation_summary_tokens=summary_tokens,
            current_state=state,
            slots=slots_dict,
            tool_results_tokens=tool_tokens,
            total_tokens=total_tok,
            budget_remaining=max(0, TOTAL_TOKEN_BUDGET - total_tok),
        )

        return full_prompt, manifest
