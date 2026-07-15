"""Result-aware tool-loop detection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GuardDecision:
    action: str = "allow"
    message: str = ""


class ToolLoopGuard:
    """Detect exact failures, same-tool failure streaks, and read-only no-progress."""

    def __init__(self) -> None:
        self.exact_failures: dict[str, int] = {}
        self.tool_failures: dict[str, int] = {}
        self.no_progress: dict[str, tuple[str, int]] = {}

    @staticmethod
    def _signature(name: str, arguments: dict[str, Any]) -> str:
        raw = json.dumps([name, arguments], sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def observe(
        self,
        name: str,
        arguments: dict[str, Any],
        output: str,
        *,
        failed: bool,
        read_only: bool,
    ) -> GuardDecision:
        signature = self._signature(name, arguments)
        if failed:
            exact = self.exact_failures.get(signature, 0) + 1
            same = self.tool_failures.get(name, 0) + 1
            self.exact_failures[signature] = exact
            self.tool_failures[name] = same
            if exact >= 5 or same >= 8:
                return GuardDecision(
                    "halt",
                    f"Stopped {name}: repeated failures show no progress. Inspect the latest "
                    "error and change the underlying assumption.",
                )
            if exact >= 2 or same >= 3:
                return GuardDecision(
                    "warn",
                    f"Tool-loop warning: {name} has failed repeatedly ({same} failures; "
                    f"{exact} with identical arguments). Do not retry unchanged.",
                )
            return GuardDecision()
        self.tool_failures.pop(name, None)
        self.exact_failures.pop(signature, None)
        if not read_only:
            return GuardDecision()
        result_hash = hashlib.sha256(output.encode(errors="replace")).hexdigest()
        previous_hash, count = self.no_progress.get(signature, ("", 0))
        count = count + 1 if previous_hash == result_hash else 1
        self.no_progress[signature] = (result_hash, count)
        if count >= 5:
            return GuardDecision(
                "halt",
                f"Stopped {name}: the identical read-only result was returned {count} times.",
            )
        if count >= 2:
            return GuardDecision(
                "warn",
                f"Tool-loop warning: {name} returned the same result {count} times; use the "
                "existing evidence or change the query.",
            )
        return GuardDecision()
