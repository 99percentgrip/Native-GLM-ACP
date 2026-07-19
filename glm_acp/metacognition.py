"""Bounded uncertainty classification, adaptive modes, and empirical profiles."""

from __future__ import annotations

import json
import math
import platform
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .awareness import EpistemicLedger
from .project_context import ProjectFacts
from .telemetry import telemetry_enabled, trajectory_path

UNCERTAINTY_KINDS = (
    "ambiguity",
    "knowledge_gap",
    "diagnostic_uncertainty",
    "capability_limit",
    "verification_gap",
    "permission_uncertainty",
)
EXECUTION_MODES = ("direct", "grounded", "deliberate", "high-assurance")
TASK_FAMILIES = {"general", "information", "review", "implementation", "diagnosis", "operations"}
MAX_PROFILE_EVENTS = 5_000
MAX_PROFILE_BYTES = 4 * 1024 * 1024
MAX_PROFILE_BUCKETS = 64

_TASK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "operations",
        re.compile(
            r"\b(?:release|publish|deploy|registry|install|upgrade|migrate|rollback|rename)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "diagnosis",
        re.compile(
            r"\b(?:diagnos\w*|debug\w*|bug|error|fail\w*|broken|regression|"
            r"root cause|why does)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "review",
        re.compile(r"\b(?:review|audit|inspect|assess|analy[sz]e|research)\b", re.IGNORECASE),
    ),
    (
        "implementation",
        re.compile(
            r"\b(?:implement|build|create|add|change|edit|refactor|remove|update|write)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "information",
        re.compile(r"\b(?:explain|summarize|what is|how does|question|compare)\b", re.IGNORECASE),
    ),
)
_AMBIGUITY = re.compile(
    r"\b(?:maybe|perhaps|ambiguous|unclear|underspecified|not sure|either|whichever|"
    r"somehow|something|as usual)\b|"
    r"\bor\b.{0,40}\bor\b",
    re.IGNORECASE,
)
_HIGH_RISK = re.compile(
    r"\b(?:production|release|publish|deploy|registry|credential|secret|auth|security|"
    r"permission|delete|purge|migrate|payment|billing|rollback)\b",
    re.IGNORECASE,
)
_ENVIRONMENT = re.compile(r"[a-z0-9_-]{1,24}(?::[a-z0-9_-]{1,24}){3}")


def _metric(value: Any) -> int:
    try:
        return max(0, min(10**12, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _rate(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, result)) if math.isfinite(result) else 0.0


def classify_task_family(task: str) -> str:
    """Return one stable task family without retaining the task text."""
    normalized = str(task)[:4000]
    for family, pattern in _TASK_PATTERNS:
        if pattern.search(normalized):
            return family
    return "general"


def classify_environment(facts: ProjectFacts, session_mode: str) -> str:
    """Return a coarse non-identifying runtime environment label."""
    ecosystems: list[str] = []
    if any(item in facts.manifests for item in ("pyproject.toml", "setup.py", "setup.cfg")):
        ecosystems.append("python")
    if "package.json" in facts.manifests:
        ecosystems.append("node")
    if "Cargo.toml" in facts.manifests:
        ecosystems.append("rust")
    if "go.mod" in facts.manifests:
        ecosystems.append("go")
    ecosystem = ecosystems[0] if len(ecosystems) == 1 else "mixed" if ecosystems else "unknown"
    vcs = "git" if facts.branch else "plain"
    os_family = platform.system().lower() or "unknown"
    return f"{ecosystem}:{vcs}:{session_mode}:{os_family}"[:100]


@dataclass(frozen=True)
class UncertaintyItem:
    kind: str
    severity: str
    basis: str


@dataclass(frozen=True)
class CapabilityProfile:
    task_family: str
    environment: str
    attempts: int
    successes: int
    failures: int
    verified: int
    input_tokens: int
    output_tokens: int
    duration_ms: int

    @property
    def success_rate(self) -> float:
        return self.successes / max(self.attempts, 1)

    @property
    def verification_rate(self) -> float:
        return self.verified / max(self.attempts, 1)

    @property
    def mean_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens) // max(self.attempts, 1)

    @property
    def mean_duration_ms(self) -> int:
        return self.duration_ms // max(self.attempts, 1)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update(
            success_rate=round(self.success_rate, 4),
            verification_rate=round(self.verification_rate, 4),
            mean_tokens=self.mean_tokens,
            mean_duration_ms=self.mean_duration_ms,
        )
        return value


class CapabilityProfiles:
    """Aggregate metadata-only outcome events into bounded empirical buckets."""

    def __init__(self, profiles: list[CapabilityProfile] | None = None) -> None:
        self.profiles = (profiles or [])[:MAX_PROFILE_BUCKETS]

    @classmethod
    def load(cls, path: Path | None = None) -> CapabilityProfiles:
        if not telemetry_enabled():
            return cls()
        source = path or trajectory_path()
        try:
            size = source.stat().st_size
            with source.open("rb") as stream:
                if size > MAX_PROFILE_BYTES:
                    stream.seek(-MAX_PROFILE_BYTES, 2)
                    stream.readline()
                lines = stream.readlines()[-MAX_PROFILE_EVENTS:]
        except OSError:
            return cls()
        buckets: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "verified": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "duration_ms": 0,
            }
        )
        for raw in lines:
            if len(raw) > 4096:
                continue
            try:
                event = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict) or event.get("event") != "capability_outcome":
                continue
            family = str(event.get("task_family", ""))[:40]
            environment = str(event.get("environment", ""))[:100]
            if family not in TASK_FAMILIES or not _ENVIRONMENT.fullmatch(environment):
                continue
            success_value = event.get("success")
            if not isinstance(success_value, bool):
                continue
            bucket = buckets[(family, environment)]
            bucket["attempts"] += 1
            success = success_value
            bucket["successes" if success else "failures"] += 1
            if str(event.get("verification_strength", "none")) in {"targeted", "full"}:
                bucket["verified"] += 1
            for key in ("input_tokens", "output_tokens", "duration_ms"):
                bucket[key] += _metric(event.get(key, 0))
        ordered = sorted(buckets.items(), key=lambda item: (-item[1]["attempts"], item[0]))
        return cls(
            [
                CapabilityProfile(family, environment, **values)
                for (family, environment), values in ordered[:MAX_PROFILE_BUCKETS]
            ]
        )

    def matching(self, task_family: str, environment: str) -> CapabilityProfile | None:
        return next(
            (
                item
                for item in self.profiles
                if item.task_family == task_family and item.environment == environment
            ),
            None,
        )

    def to_dict(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.profiles]


@dataclass(frozen=True)
class MetacognitiveAssessment:
    task_family: str
    environment: str
    execution_mode: str
    risk_score: int
    uncertainties: tuple[UncertaintyItem, ...]
    profile_attempts: int = 0
    profile_success_rate: float = 0.0
    profile_verification_rate: float = 0.0
    empirical_escalation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_family": self.task_family,
            "environment": self.environment,
            "execution_mode": self.execution_mode,
            "risk_score": self.risk_score,
            "uncertainties": [asdict(item) for item in self.uncertainties],
            "profile_attempts": self.profile_attempts,
            "profile_success_rate": round(self.profile_success_rate, 4),
            "profile_verification_rate": round(self.profile_verification_rate, 4),
            "empirical_escalation": self.empirical_escalation,
        }

    def model_context(self) -> str:
        guidance = {
            "direct": (
                "Act directly. Avoid workers, hypothesis branching, and unnecessary questions."
            ),
            "grounded": (
                "Gather the minimum external evidence needed, then act without broad exploration."
            ),
            "deliberate": (
                "Test two or three falsifiable explanations before committing to a diagnosis "
                "or edit."
            ),
            "high-assurance": (
                "Use an explicit plan, pre-mortem, independent verification where available, "
                "and fresh post-change checks."
            ),
        }[self.execution_mode]
        payload = self.to_dict()
        payload["guidance"] = guidance
        payload["constraints"] = (
            "This mode never expands permissions, bypasses policy, stores reasoning, or "
            "authorizes delegation."
        )
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:5000]


class MetacognitiveController:
    """Select a bounded execution posture from runtime facts and empirical evidence."""

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        profiles: CapabilityProfiles | None = None,
    ) -> None:
        self.profiles = profiles or CapabilityProfiles.load()
        self.assessment: MetacognitiveAssessment | None = None
        if isinstance(data, dict):
            try:
                uncertainties = tuple(
                    UncertaintyItem(**item)
                    for item in data.get("uncertainties", [])
                    if isinstance(item, dict) and item.get("kind") in UNCERTAINTY_KINDS
                )
                mode = str(data.get("execution_mode", "direct"))
                family = str(data.get("task_family", "general"))[:40]
                environment = str(data.get("environment", "unknown"))[:100]
                if (
                    mode in EXECUTION_MODES
                    and family in TASK_FAMILIES
                    and _ENVIRONMENT.fullmatch(environment)
                ):
                    self.assessment = MetacognitiveAssessment(
                        task_family=family,
                        environment=environment,
                        execution_mode=mode,
                        risk_score=max(0, min(10, int(data.get("risk_score", 0)))),
                        uncertainties=uncertainties,
                        profile_attempts=max(0, int(data.get("profile_attempts", 0))),
                        profile_success_rate=_rate(data.get("profile_success_rate", 0.0)),
                        profile_verification_rate=_rate(data.get("profile_verification_rate", 0.0)),
                        empirical_escalation=bool(data.get("empirical_escalation", False)),
                    )
            except (TypeError, ValueError):
                self.assessment = None

    def to_dict(self) -> dict[str, Any]:
        return self.assessment.to_dict() if self.assessment is not None else {}

    def assess(
        self,
        *,
        task: str,
        facts: ProjectFacts,
        ledger: EpistemicLedger,
        permission_mode: str,
        session_mode: str,
        changed_paths: list[str],
        fresh_verification: bool,
        persistent_goal: bool,
    ) -> MetacognitiveAssessment:
        family = classify_task_family(task)
        environment = classify_environment(facts, session_mode)
        active = ledger.active_records()
        items: list[UncertaintyItem] = []

        def add(kind: str, severity: str, basis: str) -> None:
            if not any(item.kind == kind for item in items):
                items.append(UncertaintyItem(kind, severity, basis))

        if _AMBIGUITY.search(task) or any(item.kind == "assumption" for item in active):
            add("ambiguity", "medium", "request wording or active assumptions")
        if any(item.kind == "unknown" for item in active) or any(
            item.status == "stale" for item in ledger.records
        ):
            add("knowledge_gap", "medium", "active unknowns or stale evidence")
        if any(item.kind in {"hypothesis", "contradiction"} for item in active):
            severity = "high" if any(item.kind == "contradiction" for item in active) else "medium"
            add("diagnostic_uncertainty", severity, "untested hypotheses or contradictions")
        if any(item.kind == "capability" for item in active) or (
            permission_mode == "read" and family in {"implementation", "operations"}
        ):
            add("capability_limit", "high", "recorded or session capability boundary")
        if changed_paths and not fresh_verification:
            add("verification_gap", "high", "workspace changed after the latest passing check")
        if permission_mode == "ask" and family in {"implementation", "operations"}:
            add("permission_uncertainty", "low", "mutating actions require user approval")

        risk = {
            "information": 0,
            "general": 1,
            "review": 1,
            "implementation": 3,
            "diagnosis": 3,
            "operations": 5,
        }.get(family, 1)
        risk += sum(2 if item.severity == "high" else 1 for item in items)
        risk += int(bool(persistent_goal))
        risk += int(facts.dirty and family in {"implementation", "operations"})
        if _HIGH_RISK.search(task):
            risk = max(risk, 6)
        risk = min(risk, 10)

        if (
            risk >= 6
            or family == "operations"
            or any(item.kind == "verification_gap" for item in items)
        ):
            mode = "high-assurance"
        elif risk >= 4 or any(item.kind == "diagnostic_uncertainty" for item in items):
            mode = "deliberate"
        elif items or family in {"review", "diagnosis", "implementation"}:
            mode = "grounded"
        else:
            mode = "direct"

        profile = self.profiles.matching(family, environment)
        escalated = False
        if (
            profile is not None
            and profile.attempts >= 3
            and (
                profile.success_rate < 0.6
                or (family in {"implementation", "operations"} and profile.verification_rate < 0.5)
            )
        ):
            index = EXECUTION_MODES.index(mode)
            if index < len(EXECUTION_MODES) - 1:
                mode = EXECUTION_MODES[index + 1]
                risk = min(10, risk + 1)
                escalated = True

        self.assessment = MetacognitiveAssessment(
            task_family=family,
            environment=environment,
            execution_mode=mode,
            risk_score=risk,
            uncertainties=tuple(items),
            profile_attempts=profile.attempts if profile else 0,
            profile_success_rate=profile.success_rate if profile else 0.0,
            profile_verification_rate=profile.verification_rate if profile else 0.0,
            empirical_escalation=escalated,
        )
        return self.assessment

    def render(self) -> str:
        assessment = self.assessment
        if assessment is None:
            return "🧠 **Metacognitive Controller**\n- Not assessed yet."
        lines = [
            "🧠 **Metacognitive Controller**",
            f"- **Task family:** {assessment.task_family}",
            f"- **Environment:** {assessment.environment}",
            f"- **Execution mode:** {assessment.execution_mode}",
            f"- **Risk score:** {assessment.risk_score}/10",
        ]
        lines.append("\n**Uncertainty classification**")
        if assessment.uncertainties:
            lines.extend(
                f"- `{item.kind}` [{item.severity}] — {item.basis}"
                for item in assessment.uncertainties
            )
        else:
            lines.append("- none")
        lines.append("\n**Matching empirical profile**")
        if assessment.profile_attempts:
            lines.append(
                f"- {assessment.profile_attempts} attempts · "
                f"{assessment.profile_success_rate:.0%} success · "
                f"{assessment.profile_verification_rate:.0%} verified"
            )
            lines.append(
                f"- Empirical escalation: {'yes' if assessment.empirical_escalation else 'no'}"
            )
        else:
            lines.append("- no prior outcomes for this task family and environment")
        lines.append(
            "\nMode selection is advisory and cannot expand permissions, bypass policy, "
            "authorize workers, or modify trusted rules."
        )
        return "\n".join(lines)[:12_000]
