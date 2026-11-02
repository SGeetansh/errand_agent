"""Unit tests for LLMProvider implementations: ScriptedModel, RecordedModel, and OllamaModel."""

import json
import tempfile
import pytest

from src.models.ollama import OllamaModel
from src.models.recorded import RecordedModel
from src.models.scripted import ScriptedModel
from src.schemas import LLMResponse


def test_scripted_model_matching_and_fallback() -> None:
    """Test ScriptedModel matches rules by text or turn and falls back when unmatched."""
    rules = [
        {
            "turn": 1,
            "input_contains": "plumber",
            "response": {
                "intent": "search_providers",
                "slots": {"service": "plumber", "area": "Koramangala"},
                "message": "Searching for plumbers in Koramangala.",
            },
        },
        {
            "turn": 2,
            "input_contains": "yes",
            "response": {
                "intent": "confirm_booking",
                "message": "Proceeding with booking.",
            },
        },
    ]
    model = ScriptedModel(responses=rules)

    # Turn 1 matching
    resp1 = model.generate(
        messages=[{"role": "user", "content": "I need a plumber please"}],
        turn_id=1,
    )
    assert isinstance(resp1, LLMResponse)
    assert resp1.intent == "search_providers"
    assert resp1.slots["service"] == "plumber"

    # Turn 2 matching
    resp2 = model.generate(
        messages=[{"role": "user", "content": "yes, go ahead"}],
        turn_id=2,
    )
    assert resp2.intent == "confirm_booking"

    # Unmatched fallback
    resp_unmatched = model.generate(
        messages=[{"role": "user", "content": "random noise"}],
        turn_id=99,
    )
    assert resp_unmatched.intent == "unknown"


def test_scripted_model_from_file() -> None:
    """Test loading scripted rules from a JSON scenario file."""
    data = {
        "model_id": "test_scenario",
        "responses": [
            {
                "turn": 1,
                "input_contains": "ac",
                "response": {"intent": "search_ac", "message": "Finding AC techs."},
            }
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        temp_name = f.name

    model = ScriptedModel.from_file(temp_name)
    assert model.model_id == "test_scenario"
    resp = model.generate(
        messages=[{"role": "user", "content": "Need ac service"}],
        turn_id=1,
    )
    assert resp.intent == "search_ac"


def test_recorded_model_replay() -> None:
    """Test RecordedModel replaying captured trace responses."""
    recordings = [
        {"turn_id": 1, "intent": "greet", "message": "Hello! How can I help?"},
        {"turn_id": 2, "intent": "search", "message": "Found 3 providers."},
    ]
    model = RecordedModel(recordings=recordings)

    # Match by turn_id
    resp1 = model.generate(messages=[], turn_id=1)
    assert resp1.intent == "greet"
    assert resp1.message == "Hello! How can I help?"

    resp2 = model.generate(messages=[], turn_id=2)
    assert resp2.intent == "search"


def test_ollama_model_malformed_and_connection_handling() -> None:
    """Test OllamaModel gracefully handles malformed JSON and server connection errors."""
    ollama = OllamaModel(base_url="http://127.0.0.1:59999", timeout_seconds=1.0)

    # 1. Connection error handling (server offline)
    resp = ollama.generate(messages=[{"role": "user", "content": "hi"}])
    assert resp.intent == "connection_error"
    assert "inference failed" in resp.message

    # 2. Malformed JSON parsing
    malformed = ollama._parse_json_response("NOT VALID JSON", latency_ms=10.0)
    assert malformed.intent == "malformed_output"

    # 3. Valid JSON parsing
    valid = ollama._parse_json_response('{"intent": "book", "message": "ok"}', latency_ms=10.0)
    assert valid.intent == "book"
    assert valid.message == "ok"
