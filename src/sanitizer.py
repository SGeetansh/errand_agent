"""Sanitization pipeline neutralizing untrusted free-text and injection markers."""

from dataclasses import asdict, is_dataclass
from typing import Any


class Sanitizer:
    """Sanitizes tool outputs and external texts before insertion into LLM context."""

    INJECTION_MARKERS = [
        "IGNORE ALL PREVIOUS",
        "IGNORE ABOVE",
        "SYSTEM:",
        "ASSISTANT:",
        "### INSTRUCTION",
        "<|im_start|>",
        "<|im_end|>",
        "[INST]",
        "<<SYS>>",
    ]

    @staticmethod
    def sanitize_text(text: str, source_label: str) -> str:
        """Strip known injection markers and encapsulate text in labeled DATA delimiters.

        Args:
            text: Raw input string from untrusted source.
            source_label: Attribution label (e.g. 'tool:get_provider', 'memory').

        Returns:
            str: Cleaned string enclosed within <DATA> tags.
        """
        cleaned = text
        for marker in Sanitizer.INJECTION_MARKERS:
            # Case-insensitive replacement
            idx = cleaned.upper().find(marker.upper())
            while idx != -1:
                cleaned = cleaned[:idx] + "[REDACTED]" + cleaned[idx + len(marker):]
                idx = cleaned.upper().find(marker.upper())

        return f'<DATA source="{source_label}">\n{cleaned}\n</DATA>'

    @staticmethod
    def sanitize_tool_result(tool_name: str, result: Any) -> Any:
        """Recursively sanitize any strings within arbitrary tool outputs.

        Args:
            tool_name: Name of tool producing the result.
            result: Any structured tool output (dataclass, dict, list, str, etc.).

        Returns:
            Any: Sanitized structure with all string leaf nodes wrapped in DATA tags.
        """
        if isinstance(result, str):
            return Sanitizer.sanitize_text(result, source_label=f"tool:{tool_name}")

        if is_dataclass(result):
            as_dict = asdict(result)
            return Sanitizer.sanitize_tool_result(tool_name, as_dict)

        if isinstance(result, dict):
            return {
                k: Sanitizer.sanitize_tool_result(tool_name, v)
                for k, v in result.items()
            }

        if isinstance(result, list):
            return [Sanitizer.sanitize_tool_result(tool_name, item) for item in result]

        return result
