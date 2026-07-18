"""Deterministic offline fuzzing and transactional fault-injection checks."""

from __future__ import annotations

import json
import random
import string
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .observability import observability_snapshot
from .plugins import PluginError, PluginRegistry
from .policy import PolicyEngine
from .references import expand_references
from .tools import Sandbox, ToolError
from .worktrees import WorktreeError, WorktreeManager


def _random_text(randomizer: random.Random, limit: int = 300) -> str:
    alphabet = string.ascii_letters + string.digits + "@:/\\..[]{}\x00\n\t -_"
    return "".join(randomizer.choice(alphabet) for _ in range(randomizer.randrange(limit + 1)))


def _fuzz_parsers(root: Path, iterations: int, seed: int) -> dict[str, int]:
    randomizer = random.Random(seed)
    plugin_errors = 0
    reference_errors = 0
    policy_dir = root / ".glm-acp"
    policy_dir.mkdir()
    policy_path = policy_dir / "policy.json"
    manifest = root / "plugin.json"
    for _ in range(iterations):
        payload = _random_text(randomizer)
        manifest.write_text(payload, encoding="utf-8")
        try:
            PluginRegistry._manifest(manifest)
        except PluginError:
            plugin_errors += 1
        try:
            expand_references(payload, Sandbox(root))
        except (ToolError, OSError, ValueError):
            reference_errors += 1
        policy_path.write_text(payload, encoding="utf-8")
        effect, _ = PolicyEngine(str(root)).evaluate("run_command", {"command": "true"}, [])
        if effect != "deny":
            raise AssertionError("Malformed policy did not fail closed")
    return {"plugin_rejections": plugin_errors, "reference_rejections": reference_errors}


def _fuzz_telemetry(root: Path, iterations: int, seed: int) -> int:
    randomizer = random.Random(seed ^ 0xA5A5)
    path = root / "trajectory.jsonl"
    lines = [_random_text(randomizer, 500).encode(errors="replace") for _ in range(iterations)]
    lines.append(json.dumps({"schema": 1, "event": "tool_call", "success": True}).encode())
    path.write_bytes(b"\n".join(lines))
    snapshot = observability_snapshot(path)
    if snapshot["events"] != 1:
        raise AssertionError("Observability did not isolate malformed telemetry")
    return iterations


def _worktree_rollback_fault(root: Path) -> bool:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "fuzz@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Fuzz"], cwd=repo, check=True)
    target = repo / "value.txt"
    target.write_text("baseline\n")
    subprocess.run(["git", "add", "value.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    manager = WorktreeManager(root / "workers")
    state = manager.create(str(repo))
    (Path(state["path"]) / "value.txt").write_text("worker\n")
    inspected = manager.inspect(str(repo), state["path"], state["base_ref"])

    def inject(_repo: Path, _paths: list[str]) -> None:
        raise RuntimeError("injected post-apply fault")

    manager._after_apply = inject  # type: ignore[method-assign]
    try:
        manager.promote(str(repo), state["path"], state["base_ref"], str(inspected["diff_sha256"]))
    except WorktreeError as error:
        if "rolled back" not in str(error):
            raise
    else:
        raise AssertionError("Injected worker promotion fault was not raised")
    return target.read_text() == "baseline\n"


def run_hardening_checks(iterations: int = 250, seed: int = 5202) -> dict[str, Any]:
    """Run bounded offline fuzzing plus a real Git rollback fault injection."""
    iterations = min(max(10, int(iterations)), 5_000)
    with tempfile.TemporaryDirectory(prefix="glm-acp-harden-") as temporary:
        root = Path(temporary)
        parser = _fuzz_parsers(root, iterations, seed)
        telemetry = _fuzz_telemetry(root, iterations, seed)
        rollback = _worktree_rollback_fault(root)
    return {
        "schema": 1,
        "seed": seed,
        "iterations": iterations,
        "checks": {
            **parser,
            "malformed_telemetry": telemetry,
            "promotion_rollback_fault": rollback,
        },
        "passed": rollback,
    }
