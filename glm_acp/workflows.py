"""Bounded declarative tool workflow validation."""

from __future__ import annotations

from typing import Any

MAX_WORKFLOW_STEPS = 12
ALLOWED_WORKFLOW_TOOLS = {
    "read_file",
    "list_directory",
    "search_files",
    "grep",
    "write_file",
    "edit_file",
    "apply_patch",
    "apply_patch_set",
    "run_command",
}


def ordered_steps(raw_steps: Any) -> list[dict[str, Any]]:
    """Validate a small acyclic dependency graph and return topological order."""
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_WORKFLOW_STEPS:
        raise ValueError(f"steps must contain 1-{MAX_WORKFLOW_STEPS} entries")
    steps: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ValueError("Each workflow step must be an object")
        step_id = str(raw.get("id", index + 1))[:64]
        tool = str(raw.get("tool", ""))
        arguments = raw.get("arguments")
        needs = raw.get("needs", [])
        if (
            step_id in steps
            or tool not in ALLOWED_WORKFLOW_TOOLS
            or not isinstance(arguments, dict)
        ):
            raise ValueError(f"Invalid workflow step: {step_id}")
        if not isinstance(needs, list) or not all(isinstance(item, str) for item in needs):
            raise ValueError(f"Invalid dependencies for workflow step: {step_id}")
        steps[step_id] = {"id": step_id, "tool": tool, "arguments": arguments, "needs": needs}
    ordered: list[dict[str, Any]] = []
    remaining = dict(steps)
    while remaining:
        ready = [
            step
            for step in remaining.values()
            if all(need in {item["id"] for item in ordered} for need in step["needs"])
        ]
        if not ready:
            missing = sorted(
                {need for step in remaining.values() for need in step["needs"] if need not in steps}
            )
            raise ValueError(f"Workflow has a cycle or missing dependencies: {missing}")
        for step in ready:
            ordered.append(step)
            remaining.pop(step["id"])
    return ordered
