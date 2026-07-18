"""Secret-safe local observability summaries over metadata-only trajectories."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from .telemetry import trajectory_path

MAX_OBSERVABILITY_EVENTS = 50_000
MAX_OBSERVABILITY_BYTES = 20 * 1024 * 1024


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return int(ordered[index])


def _events(path: Path, max_events: int) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size > MAX_OBSERVABILITY_BYTES:
        with path.open("rb") as stream:
            stream.seek(-MAX_OBSERVABILITY_BYTES, 2)
            stream.readline()
            raw_lines = stream.readlines()
    else:
        raw_lines = path.read_bytes().splitlines()
    output: list[dict[str, Any]] = []
    for raw in raw_lines[-max_events:]:
        if len(raw) > 4096:
            continue
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("schema") == 1:
            output.append(value)
    return output


def observability_snapshot(
    path: Path | None = None, max_events: int = MAX_OBSERVABILITY_EVENTS
) -> dict[str, Any]:
    """Aggregate bounded local metrics without exposing event bodies or session ids."""
    events = _events(path or trajectory_path(), min(max(1, max_events), MAX_OBSERVABILITY_EVENTS))
    tools = [event for event in events if event.get("event") == "tool_call"]
    llm = [event for event in events if event.get("event") == "llm_call"]
    turns = [event for event in events if event.get("event") == "turn_complete"]
    certificates = [event for event in events if event.get("event") == "completion_certificate"]
    durations = [int(event.get("duration_ms", 0) or 0) for event in tools]
    llm_durations = [int(event.get("duration_ms", 0) or 0) for event in llm]
    tool_counts = Counter(str(event.get("tool", "unknown")) for event in tools)
    tool_failures = Counter(
        str(event.get("tool", "unknown")) for event in tools if not event.get("success", False)
    )
    input_tokens = sum(int(event.get("input_tokens", 0) or 0) for event in llm)
    output_tokens = sum(int(event.get("output_tokens", 0) or 0) for event in llm)
    cached_tokens = sum(int(event.get("cached_tokens", 0) or 0) for event in llm)
    sessions = {str(event.get("session", "")) for event in events if event.get("session")}
    return {
        "schema": 1,
        "events": len(events),
        "sessions": len(sessions),
        "window": {
            "first": str(events[0].get("timestamp", "")) if events else "",
            "last": str(events[-1].get("timestamp", "")) if events else "",
        },
        "turns": {
            "completed": len(turns),
            "freshly_verified": sum(bool(event.get("fresh_verification")) for event in turns),
            "changed_files": sum(int(event.get("changed_files", 0) or 0) for event in turns),
        },
        "awareness": {
            "certificates": len(certificates),
            "complete": sum(bool(event.get("complete")) for event in certificates),
            "prevented_false_completion": sum(
                bool(event.get("prevented")) for event in certificates
            ),
            "mean_evidence_coverage": round(
                sum(float(event.get("coverage", 0.0) or 0.0) for event in certificates)
                / max(len(certificates), 1),
                4,
            ),
            "active_contradictions": sum(
                int(event.get("contradictions", 0) or 0) for event in certificates
            ),
            "stale_evidence": sum(
                int(event.get("stale_evidence", 0) or 0) for event in certificates
            ),
        },
        "llm": {
            "calls": len(llm),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cache_hit_ratio": round(cached_tokens / max(input_tokens, 1), 4),
            "latency_ms_p50": int(statistics.median(llm_durations)) if llm_durations else 0,
            "latency_ms_p95": _percentile(llm_durations, 0.95),
        },
        "tools": {
            "calls": len(tools),
            "failures": sum(tool_failures.values()),
            "success_rate": round(
                sum(bool(event.get("success")) for event in tools) / max(len(tools), 1), 4
            ),
            "latency_ms_p50": int(statistics.median(durations)) if durations else 0,
            "latency_ms_p95": _percentile(durations, 0.95),
            "by_tool": [
                {"tool": tool, "calls": count, "failures": tool_failures[tool]}
                for tool, count in tool_counts.most_common(20)
            ],
        },
        "safety": {
            "rollbacks": sum(event.get("event") == "rollback" for event in events),
            "rollback_conflicts": sum(
                event.get("event") == "rollback" and not event.get("success", False)
                for event in events
            ),
            "worker_promotions": sum(
                event.get("event") == "worker_promotion" and event.get("success", False)
                for event in events
            ),
        },
    }


def render_observability(snapshot: dict[str, Any]) -> str:
    tools = snapshot["tools"]
    llm = snapshot["llm"]
    turns = snapshot["turns"]
    safety = snapshot["safety"]
    awareness = snapshot["awareness"]
    by_tool = (
        "\n".join(
            f"- `{item['tool']}`: {item['calls']} calls, {item['failures']} failures"
            for item in tools["by_tool"]
        )
        or "- No tool activity recorded."
    )
    return (
        "📈 **Local Observability**\n"
        f"- Window: {snapshot['window']['first'] or 'empty'} → "
        f"{snapshot['window']['last'] or 'empty'}\n"
        f"- Sessions: {snapshot['sessions']} · completed turns: {turns['completed']} · "
        f"freshly verified: {turns['freshly_verified']}\n"
        f"- LLM: {llm['calls']} calls · {llm['input_tokens']:,} input · "
        f"{llm['output_tokens']:,} output · {llm['cache_hit_ratio']:.1%} cache hit · "
        f"p95 {llm['latency_ms_p95']} ms\n"
        f"- Tools: {tools['calls']} calls · {tools['success_rate']:.1%} success · "
        f"p95 {tools['latency_ms_p95']} ms\n"
        f"- Safety: {safety['rollbacks']} rollbacks ({safety['rollback_conflicts']} conflicts) · "
        f"{safety['worker_promotions']} worker promotions\n\n"
        f"- Awareness: {awareness['complete']}/{awareness['certificates']} certificates complete · "
        f"{awareness['mean_evidence_coverage']:.1%} mean evidence coverage · "
        f"{awareness['prevented_false_completion']} unsupported completions prevented\n\n"
        "**Most-used tools**\n" + by_tool
    )
