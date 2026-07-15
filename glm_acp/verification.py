"""Fresh, project-scoped verification evidence for coding sessions."""

from __future__ import annotations

import re
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project_context import ProjectFacts


@dataclass
class VerificationEvent:
    command: str
    canonical_command: str
    cwd: str
    status: str
    exit_code: int
    scope: str
    created_at: str
    edit_generation: int
    output_summary: str = ""


def _segments(command: str) -> list[list[str]]:
    result: list[list[str]] = []
    for segment in re.split(r"\s*(?:&&|\|\||;)\s*", command.strip()):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        while tokens and (
            tokens[0] in {"env", "command", "time"}
            or ("=" in tokens[0] and not tokens[0].startswith("-"))
        ):
            tokens = tokens[1:]
        if tokens:
            executable = Path(tokens[0]).name
            if re.fullmatch(r"python3(?:\.\d+)*", executable):
                executable = "python3"
            elif re.fullmatch(r"python(?:\.\d+)+", executable):
                executable = "python"
            tokens[0] = executable
            result.append([token.removeprefix("./") for token in tokens])
    return result


def _canonical_tokens(command: str) -> list[str]:
    try:
        return [token.removeprefix("./") for token in shlex.split(command)]
    except ValueError:
        return []


def _invocation_variants(tokens: list[str]) -> list[list[str]]:
    """Expose the verifier behind supported command runners and their flags."""
    variants = [tokens]
    if tokens[:2] == ["uv", "run"]:
        executables = {"pytest", "ruff", "mypy", "python", "python3"}
        for index, token in enumerate(tokens[2:], 2):
            if Path(token).name in executables:
                variants.append([Path(token).name, *tokens[index + 1 :]])
                break
    return variants


def classify_verification(command: str, facts: ProjectFacts) -> tuple[str, str] | None:
    """Return canonical command and targeted/full scope only for detected checks."""
    # A shell pipeline, fallback, or trailing command can hide the verifier's real
    # exit status (for example, ``pytest | tee`` or ``pytest || true``).
    if re.search(r"(?:\|\||(?<!\|)\|(?!\|)|;)", command):
        return None
    non_execution_flags = {
        "--help",
        "-h",
        "--version",
        "--collect-only",
        "--list",
        "--list-tests",
        "--dry-run",
    }
    for canonical in facts.verify_commands:
        expected = _canonical_tokens(canonical)
        if not expected:
            continue
        equivalents = [expected]
        if expected[:2] == ["uv", "run"]:
            equivalents.append(expected[2:])
        if expected == ["pytest"]:
            equivalents.extend(
                (["python", "-m", "pytest"], ["python3", "-m", "pytest"], ["uv", "run", "pytest"])
            )
        for raw_tokens in _segments(command):
            for tokens in _invocation_variants(raw_tokens):
                for candidate in equivalents:
                    if tokens[: len(candidate)] == candidate:
                        trailing = tokens[len(candidate) :]
                        if non_execution_flags.intersection(trailing):
                            continue
                        targeted = any(
                            not value.startswith("-")
                            and (
                                "/" in value
                                or "::" in value
                                or value.endswith((".py", ".ts", ".js", ".rs", ".go"))
                            )
                            for value in trailing
                        )
                        return canonical, "targeted" if targeted else "full"
    return None


class VerificationLedger:
    """Bounded in-session ledger invalidated by every successful edit."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.edit_generation = int(data.get("edit_generation", 0))
        self.changed_paths = [str(value) for value in data.get("changed_paths", [])][-200:]
        self.events = [
            VerificationEvent(**value)
            for value in data.get("events", [])
            if isinstance(value, dict)
        ][-100:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_generation": self.edit_generation,
            "changed_paths": self.changed_paths,
            "events": [asdict(event) for event in self.events[-100:]],
        }

    def mark_edit(self, path: str) -> None:
        self.edit_generation += 1
        self.changed_paths = list(dict.fromkeys([*self.changed_paths, str(path)]))[-200:]

    def record(
        self, command: str, cwd: str, exit_code: int, output: str, facts: ProjectFacts
    ) -> VerificationEvent | None:
        match = classify_verification(command, facts)
        if match is None:
            return None
        canonical, scope = match
        summary = output.strip()
        if len(summary) > 2000:
            summary = (
                summary[:650]
                + f"\n... [{len(summary) - 2000} chars omitted] ...\n"
                + summary[-1350:]
            )
        event = VerificationEvent(
            command=command,
            canonical_command=canonical,
            cwd=str(Path(cwd).resolve()),
            status="passed" if exit_code == 0 else "failed",
            exit_code=exit_code,
            scope=scope,
            created_at=datetime.now(timezone.utc).isoformat(),
            edit_generation=self.edit_generation,
            output_summary=summary,
        )
        self.events.append(event)
        self.events = self.events[-100:]
        return event

    @property
    def fresh_pass(self) -> VerificationEvent | None:
        for event in reversed(self.events):
            if event.edit_generation != self.edit_generation:
                continue
            if event.status == "passed":
                return event
            if event.status == "failed":
                return None
        return None

    @property
    def latest(self) -> VerificationEvent | None:
        return self.events[-1] if self.events else None
