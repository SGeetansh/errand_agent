"""Compute and format live LLM execution metrics from recorded JSONL turns."""

import json
import os
import sys
from typing import Any


def compute_metrics(jsonl_path: str = "tests/live/results/live_metrics.jsonl") -> dict[str, Any]:
    """Parse JSONL turns log and calculate aggregate performance metrics.

    Args:
        jsonl_path: Path to live_metrics.jsonl log file.

    Returns:
        dict[str, Any]: Aggregated metrics summary.
    """
    if not os.path.exists(jsonl_path):
        print(f"Error: {jsonl_path} does not exist.")
        return {}

    records: list[dict[str, Any]] = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        print("No turn records found in metrics log.")
        return {}

    total_turns = len(records)
    malformed = sum(1 for r in records if r.get("malformed", False))
    retries = sum(r.get("retries", 0) for r in records)
    latencies = [float(r.get("latency_ms", 0.0)) for r in records if float(r.get("latency_ms", 0.0)) > 0]

    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    min_latency = min(latencies) if latencies else 0.0
    max_latency = max(latencies) if latencies else 0.0
    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95)
    p95_latency = sorted_lat[p95_idx] if sorted_lat else 0.0

    tot_prompt = sum(int(r.get("prompt_tokens", 0)) for r in records)
    tot_completion = sum(int(r.get("completion_tokens", 0)) for r in records)
    tot_tokens = tot_prompt + tot_completion
    total_model_seconds = sum(latencies) / 1000.0

    print("\n" + "=" * 70)
    print("          VERIFIED LIVE MODEL EXECUTION METRICS (qwen2.5:7b)")
    print("=" * 70)
    print(f"Source file:               {jsonl_path}")
    print(f"Total live model turns:    {total_turns}")
    print(f"Malformed responses:       {malformed} ({(malformed/total_turns)*100:.1f}%)")
    print(f"Retries needed:            {retries}")
    print(f"Total model time:          {total_model_seconds:.2f} s")
    print(f"Average latency per turn:  {avg_latency:.1f} ms")
    print(f"Min latency:               {min_latency:.1f} ms")
    print(f"Max latency:               {max_latency:.1f} ms")
    print(f"P95 latency:               {p95_latency:.1f} ms")
    print(f"Total prompt tokens:       {tot_prompt}")
    print(f"Total completion tokens:   {tot_completion}")
    print(f"Total tokens processed:    {tot_tokens}")
    print("-" * 70)
    print("Per-Test Breakdown:")
    print(f"{'Test Name':<42} {'Turns':<8} {'Avg Lat (ms)':<14} {'Tokens':<8}")

    test_groups: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        tname = r.get("test", "unknown")
        test_groups.setdefault(tname, []).append(r)

    for tname, items in test_groups.items():
        t_lats = [float(i.get("latency_ms", 0.0)) for i in items]
        t_avg = sum(t_lats) / len(t_lats) if t_lats else 0.0
        t_toks = sum(int(i.get("prompt_tokens", 0)) + int(i.get("completion_tokens", 0)) for i in items)
        print(f"{tname:<42} {len(items):<8} {t_avg:<14.1f} {t_toks:<8}")

    print("=" * 70 + "\n")

    return {
        "total_turns": total_turns,
        "malformed": malformed,
        "retries": retries,
        "avg_latency_ms": round(avg_latency, 2),
        "min_latency_ms": round(min_latency, 2),
        "max_latency_ms": round(max_latency, 2),
        "p95_latency_ms": round(p95_latency, 2),
        "prompt_tokens": tot_prompt,
        "completion_tokens": tot_completion,
        "total_tokens": tot_tokens,
        "total_model_seconds": round(total_model_seconds, 2),
    }


if __name__ == "__main__":
    compute_metrics()
