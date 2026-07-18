"""Fail-closed declarative tool policy."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any


class PolicyEngine:
    """Evaluate ordered allow/ask/deny rules from ``.glm-acp/policy.json``."""

    def __init__(self, cwd: str) -> None:
        self.root = Path(cwd).resolve()
        self.path = self.root / ".glm-acp" / "policy.json"

    def _rules(self) -> tuple[list[dict[str, Any]], str]:
        if not self.path.exists():
            return [], ""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return [], f"Invalid policy file: {error}"
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return [], "Policy must be an object with version 1"
        rules = payload.get("rules")
        if not isinstance(rules, list) or len(rules) > 100:
            return [], "Policy rules must be a list of at most 100 entries"
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("effect") not in {"allow", "ask", "deny"}:
                return [], "Every policy rule requires effect allow, ask, or deny"
        return rules, ""

    def evaluate(self, tool: str, arguments: dict[str, Any], paths: list[str]) -> tuple[str, str]:
        rules, error = self._rules()
        if error:
            return "deny", error
        normalized_paths: list[str] = []
        for raw in paths:
            try:
                normalized_paths.append(Path(raw).resolve().relative_to(self.root).as_posix())
            except ValueError:
                normalized_paths.append("<outside-workspace>")
        command = str(arguments.get("command", ""))[:10_000]
        for index, rule in enumerate(rules):
            effect = str(rule["effect"])
            tools = rule.get("tools", ["*"])
            path_patterns = rule.get("paths", ["*"])
            if not isinstance(tools, list) or not all(isinstance(value, str) for value in tools):
                return "deny", f"Invalid tools in policy rule {index + 1}"
            if not any(fnmatch.fnmatch(tool, value) for value in tools):
                continue
            if normalized_paths:
                if not isinstance(path_patterns, list) or not all(
                    isinstance(value, str) for value in path_patterns
                ):
                    return "deny", f"Invalid paths in policy rule {index + 1}"
                matches = [
                    any(fnmatch.fnmatch(path, pattern) for pattern in path_patterns)
                    for path in normalized_paths
                ]
                if not (any(matches) if effect == "deny" else all(matches)):
                    continue
            pattern = rule.get("command_regex")
            if pattern is not None:
                try:
                    expression = str(pattern)
                    if len(expression) > 200 or re.search(r"\([^)]*[+*][^)]*\)[+*{]", expression):
                        return "deny", f"Unsafe command_regex in policy rule {index + 1}"
                    if not re.search(expression, command):
                        continue
                except re.error:
                    return "deny", f"Invalid command_regex in policy rule {index + 1}"
            reason = str(rule.get("reason", f"Matched policy rule {index + 1}"))[:1000]
            return effect, reason
        return "default", "No policy rule matched"
