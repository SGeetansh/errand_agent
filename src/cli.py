"""Interactive command-line chat interface for the home-service Errand Agent."""

import argparse
import sys

from src.budget import Budget
from src.event_queue import EventQueue
from src.memory import MemoryStore
from src.models.ollama import OllamaModel
from src.models.scripted import ScriptedModel
from src.orchestrator import Orchestrator
from src.recovery import IntentLog
from src.tools.mock_env import MockEnvironment
from src.tracer import Tracer


def build_orchestrator(model_type: str = "scripted") -> Orchestrator:
    """Instantiate a fully configured Orchestrator for CLI execution.

    Args:
        model_type: 'scripted' or 'ollama'.

    Returns:
        Orchestrator: Configured agent instance.
    """
    mock_env = MockEnvironment()
    memory = MemoryStore(db_path="data/errand_agent.db")
    wal = IntentLog(db_path="data/errand_agent.db")
    tracer = Tracer(trace_file="traces/cli_session.jsonl")
    budget = Budget()
    event_queue = EventQueue()

    if model_type == "ollama":
        llm = OllamaModel(model_name="qwen2.5:7b")
    else:
        # Default scripted responses for CLI interactive walk-through
        rules = [
            {
                "input_contains": "plumber",
                "response": {
                    "intent": "search_providers",
                    "slots": {"service": "plumber", "area": "Koramangala", "date_range": "this week"},
                    "message": "I found several top plumbers in Koramangala this week.",
                },
            },
            {
                "input_contains": "ac",
                "response": {
                    "intent": "search_providers",
                    "slots": {"service": "ac_service", "area": "Koramangala", "date_range": "Saturday"},
                    "message": "Looking for AC servicing options for Saturday.",
                },
            },
            {
                "input_contains": "electrician",
                "response": {
                    "intent": "search_providers",
                    "slots": {"service": "electrician", "area": "Koramangala", "date_range": "tomorrow"},
                    "message": "Checking electricians in Koramangala.",
                },
            },
        ]
        llm = ScriptedModel(responses=rules)

    return Orchestrator(
        llm=llm,
        mock_env=mock_env,
        memory=memory,
        wal=wal,
        tracer=tracer,
        budget=budget,
        event_queue=event_queue,
    )


def main_loop(model_type: str = "scripted") -> None:
    """Run interactive REPL loop reading standard input and printing responses.

    Args:
        model_type: Selected LLM model provider.
    """
    orchestrator = build_orchestrator(model_type)
    print("=" * 60)
    print("  Welcome to the Errand Agent (Home Services Chat Assistant)")
    print(f"  Model Provider: {model_type.upper()}")
    print("  Type 'exit' or 'quit' to terminate.")
    print("=" * 60 + "\n")

    while True:
        try:
            user_input = input("User > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting session. Goodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("Session closed. Traces written to traces/cli_session.jsonl.")
            break

        reply = orchestrator.run_turn(user_input)
        print(f"\nAgent > {reply}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Errand Agent CLI")
    parser.add_argument("--model", choices=["scripted", "ollama"], default="scripted")
    args = parser.parse_args()
    main_loop(model_type=args.model)
