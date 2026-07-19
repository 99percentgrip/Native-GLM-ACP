"""Bounded evidence-only criticism, hypothesis testing, and information value."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from .metacognition import MetacognitiveAssessment
from .security import scan_promptware

MAX_HYPOTHESES = 3
MIN_HYPOTHESES = 2
MAX_INFORMATION_ACTIONS = 5
MAX_CRITIC_CONCERNS = 4
MAX_CRITIC_REVIEWS_PER_TURN = 2
HYPOTHESIS_STATUSES = {"untested", "supported", "refuted", "inconclusive"}
CRITIC_OUTCOMES = {"approve", "revise", "blocked"}
VIRTUAL_ACTIONS = {"ask_user", "request_permission"}

_SENSITIVE = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|token|secret|password|credential)\s*[:=]\s*[^\s]{8,}",
        re.IGNORECASE,
    ),
)
_DIFF_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|secret|password|credential|authorization)\b"
    r"\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s]+)"
)


class DeliberationError(ValueError):
    """Raised when structured deliberation state violates its bounded contract."""


def _safe_text(value: Any, label: str, limit: int = 500) -> str:
    text = " ".join(str(value).strip().split())
    if not text:
        raise DeliberationError(f"{label} cannot be empty")
    if len(text) > limit:
        raise DeliberationError(f"{label} exceeds the {limit}-character limit")
    if any(pattern.search(text) for pattern in _SENSITIVE):
        raise DeliberationError(f"{label} appears to contain a credential or secret")
    if scan_promptware(text):
        raise DeliberationError(f"{label} appears to contain prompt-injection instructions")
    return text


def _bounded_ids(values: Any, known: set[str] | None = None) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[str] = []
    for value in values[:20]:
        item = str(value).strip()[:40]
        if not re.fullmatch(r"ev\d+", item) or item in result:
            continue
        if known is not None and item not in known:
            raise DeliberationError(f"Unknown or stale evidence id: {item}")
        result.append(item)
    return tuple(result)


def redact_diff(value: str, limit: int = 16_000) -> str:
    """Bound and redact credential-shaped values before independent review."""
    text = str(value)[:limit]
    text = _DIFF_SECRET.sub(r"\1[REDACTED]", text)
    text = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return text


@dataclass(frozen=True)
class Hypothesis:
    id: str
    statement: str
    prediction: str
    falsifier: str
    status: str = "untested"
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_ids"] = list(self.evidence_ids)
        return value


@dataclass(frozen=True)
class InformationAction:
    tool: str
    purpose: str
    resolves: tuple[str, ...]
    cost: int
    reliability: float
    information_gain: int

    @property
    def score(self) -> float:
        return self.information_gain * self.reliability / max(self.cost, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "purpose": self.purpose,
            "resolves": list(self.resolves),
            "cost": self.cost,
            "reliability": round(self.reliability, 2),
            "information_gain": self.information_gain,
            "score": round(self.score, 3),
        }


@dataclass(frozen=True)
class CriticVerdict:
    outcome: str
    summary: str
    concerns: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    edit_generation: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["concerns"] = list(self.concerns)
        value["evidence_ids"] = list(self.evidence_ids)
        return value


def _candidate(
    tool: str,
    purpose: str,
    resolves: Iterable[str],
    cost: int,
    reliability: float,
    gain: int,
) -> InformationAction:
    return InformationAction(tool, purpose, tuple(resolves), cost, reliability, gain)


def rank_information_actions(
    assessment: MetacognitiveAssessment,
    available_tools: set[str],
    permission_mode: str,
) -> tuple[InformationAction, ...]:
    """Rank cheap reliable actions that target current uncertainty classes."""
    if assessment.execution_mode == "direct":
        return ()
    kinds = {item.kind for item in assessment.uncertainties}
    candidates: list[InformationAction] = []
    if "ambiguity" in kinds:
        candidates.append(
            _candidate("ask_user", "Clarify the decisive requirement", ["ambiguity"], 1, 1.0, 5)
        )
    if "permission_uncertainty" in kinds:
        candidates.append(
            _candidate(
                "request_permission",
                "Resolve authority before a mutating action",
                ["permission_uncertainty"],
                1,
                1.0,
                5,
            )
        )
    if "verification_gap" in kinds:
        candidates.append(
            _candidate(
                "run_command",
                "Run the narrowest recognized post-edit check",
                ["verification_gap"],
                3,
                0.98,
                5,
            )
        )
    if "diagnostic_uncertainty" in kinds or assessment.task_family == "diagnosis":
        candidates.extend(
            (
                _candidate(
                    "semantic_code",
                    "Locate definitions and references that distinguish hypotheses",
                    ["diagnostic_uncertainty", "knowledge_gap"],
                    1,
                    0.88,
                    4,
                ),
                _candidate(
                    "grep",
                    "Find the smallest discriminating code or error pattern",
                    ["diagnostic_uncertainty", "knowledge_gap"],
                    1,
                    0.78,
                    3,
                ),
                _candidate(
                    "run_command",
                    "Run one targeted reproduction that separates hypotheses",
                    ["diagnostic_uncertainty"],
                    3,
                    0.95,
                    5,
                ),
            )
        )
    if "knowledge_gap" in kinds:
        candidates.extend(
            (
                _candidate(
                    "semantic_code",
                    "Resolve the gap from definitions and call sites",
                    ["knowledge_gap"],
                    1,
                    0.86,
                    4,
                ),
                _candidate(
                    "read_file",
                    "Read the narrowest owning source or contract",
                    ["knowledge_gap"],
                    1,
                    0.82,
                    3,
                ),
            )
        )
    if "capability_limit" in kinds:
        candidates.append(
            _candidate(
                "mcp_list_tools",
                "Establish whether an allowed capability is available",
                ["capability_limit"],
                1,
                0.95,
                4,
            )
        )
    if not candidates and assessment.task_family in {"review", "implementation", "diagnosis"}:
        candidates.append(
            _candidate(
                "semantic_code",
                "Ground the next decision in the owning symbol and references",
                ["knowledge_gap"],
                1,
                0.85,
                3,
            )
        )

    usable: dict[str, InformationAction] = {}
    for item in candidates:
        if item.tool not in available_tools and item.tool not in VIRTUAL_ACTIONS:
            continue
        if permission_mode == "read" and item.tool == "run_command":
            continue
        existing = usable.get(item.tool)
        if existing is None or item.score > existing.score:
            usable[item.tool] = item
    ordered = sorted(usable.values(), key=lambda item: (-item.score, item.cost, item.tool))
    return tuple(ordered[:MAX_INFORMATION_ACTIONS])


def fallback_hypotheses() -> list[dict[str, str]]:
    """Return safe generic hypotheses when independent generation is unavailable."""
    return [
        {
            "statement": "The failure is caused by the implementation path under test.",
            "prediction": "A targeted reproduction reaches the changed or owning code path.",
            "falsifier": (
                "The failure persists when that path is bypassed or reverted in isolation."
            ),
        },
        {
            "statement": (
                "The failure is caused by environment, configuration, or dependency state."
            ),
            "prediction": "The outcome changes under a controlled environment or canonical setup.",
            "falsifier": "The same failure reproduces across controlled and clean environments.",
        },
        {
            "statement": "The observed expectation or test fixture is stale or incomplete.",
            "prediction": "The current contract or call sites contradict the failing expectation.",
            "falsifier": "Authoritative contracts and independent callers confirm the expectation.",
        },
    ]


class GroundedDeliberation:
    """Persist bounded conclusions while keeping all private reasoning out of state."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.objective_hash = ""
        self.hypotheses: list[Hypothesis] = []
        self.actions: tuple[InformationAction, ...] = ()
        self.critic: CriticVerdict | None = None
        self.hypothesis_source = ""
        if not isinstance(data, dict):
            return
        self.objective_hash = str(data.get("objective_hash", ""))[:16]
        self.hypothesis_source = str(data.get("hypothesis_source", ""))[:20]
        try:
            self.set_hypotheses(
                data.get("hypotheses", []), source=self.hypothesis_source or "stored"
            )
            restored: list[Hypothesis] = []
            for original, value in zip(self.hypotheses, data.get("hypotheses", [])):
                status = str(value.get("status", "untested"))
                restored.append(
                    Hypothesis(
                        original.id,
                        original.statement,
                        original.prediction,
                        original.falsifier,
                        status if status in HYPOTHESIS_STATUSES else "untested",
                        _bounded_ids(value.get("evidence_ids", [])),
                    )
                )
            self.hypotheses = restored
        except DeliberationError:
            self.hypotheses = []
        critic = data.get("critic")
        if isinstance(critic, dict):
            try:
                self.critic = self.validate_critic(
                    critic,
                    fresh_evidence_ids=None,
                    edit_generation=int(critic.get("edit_generation", 0)),
                    source=str(critic.get("source", "stored")),
                )
            except DeliberationError:
                self.critic = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_hash": self.objective_hash,
            "hypothesis_source": self.hypothesis_source,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "critic": self.critic.to_dict() if self.critic else None,
        }

    def prepare(
        self,
        task: str,
        assessment: MetacognitiveAssessment,
        available_tools: set[str],
        permission_mode: str,
        fresh_evidence_ids: set[str],
        edit_generation: int,
    ) -> None:
        fingerprint = hashlib.sha256(str(task).encode()).hexdigest()[:16] if task else ""
        if fingerprint != self.objective_hash:
            self.objective_hash = fingerprint
            self.hypotheses = []
            self.hypothesis_source = ""
            self.critic = None
        self.invalidate_stale(fresh_evidence_ids, edit_generation)
        self.actions = rank_information_actions(assessment, available_tools, permission_mode)

    @staticmethod
    def requires_hypotheses(assessment: MetacognitiveAssessment) -> bool:
        return assessment.task_family == "diagnosis" and assessment.execution_mode in {
            "deliberate",
            "high-assurance",
        }

    @staticmethod
    def requires_critic(
        assessment: MetacognitiveAssessment,
        changed_paths: list[str],
        persistent_goal: bool,
    ) -> bool:
        return assessment.execution_mode in {"deliberate", "high-assurance"} and bool(
            changed_paths or persistent_goal or assessment.task_family == "diagnosis"
        )

    def set_hypotheses(self, values: Any, *, source: str) -> tuple[Hypothesis, ...]:
        if not isinstance(values, list) or not MIN_HYPOTHESES <= len(values) <= MAX_HYPOTHESES:
            raise DeliberationError("Diagnosis requires two or three hypotheses")
        result: list[Hypothesis] = []
        for index, value in enumerate(values, 1):
            if not isinstance(value, dict):
                raise DeliberationError("Each hypothesis must be an object")
            item = Hypothesis(
                id=f"h{index}",
                statement=_safe_text(value.get("statement", ""), "Hypothesis", 400),
                prediction=_safe_text(value.get("prediction", ""), "Prediction", 400),
                falsifier=_safe_text(value.get("falsifier", ""), "Falsifier", 400),
            )
            if len({item.statement, item.prediction, item.falsifier}) != 3:
                raise DeliberationError(
                    "Each hypothesis needs distinct claims, predictions, and falsifiers"
                )
            if any(
                existing.statement == item.statement
                or existing.prediction == item.prediction
                or existing.falsifier == item.falsifier
                for existing in result
            ):
                raise DeliberationError(
                    "Competing hypotheses need distinct claims, predictions, and falsifiers"
                )
            result.append(item)
        self.hypotheses = result
        self.hypothesis_source = str(source)[:20]
        self.critic = None
        return tuple(result)

    def record_test(
        self,
        hypothesis_id: str,
        status: str,
        evidence_ids: Any,
        fresh_evidence_ids: set[str],
    ) -> Hypothesis:
        normalized = str(status).strip().lower()
        if normalized not in HYPOTHESIS_STATUSES - {"untested"}:
            raise DeliberationError("Test status must be supported, refuted, or inconclusive")
        cited = _bounded_ids(evidence_ids, fresh_evidence_ids)
        if not cited:
            raise DeliberationError("A hypothesis test must cite fresh harness evidence")
        index = next((i for i, item in enumerate(self.hypotheses) if item.id == hypothesis_id), -1)
        if index < 0:
            raise DeliberationError(f"Unknown hypothesis: {hypothesis_id}")
        original = self.hypotheses[index]
        updated = Hypothesis(
            original.id,
            original.statement,
            original.prediction,
            original.falsifier,
            normalized,
            cited,
        )
        self.hypotheses[index] = updated
        self.critic = None
        return updated

    def invalidate_stale(self, fresh_evidence_ids: set[str], edit_generation: int) -> None:
        refreshed: list[Hypothesis] = []
        for item in self.hypotheses:
            if item.evidence_ids and not set(item.evidence_ids).issubset(fresh_evidence_ids):
                refreshed.append(
                    Hypothesis(item.id, item.statement, item.prediction, item.falsifier)
                )
            else:
                refreshed.append(item)
        self.hypotheses = refreshed
        if self.critic and (
            self.critic.edit_generation != edit_generation
            or not set(self.critic.evidence_ids).issubset(fresh_evidence_ids)
        ):
            self.critic = None

    def structural_critique(
        self,
        assessment: MetacognitiveAssessment,
        changed_paths: list[str],
        fresh_verification: bool,
        fresh_evidence_ids: set[str],
        edit_generation: int,
    ) -> CriticVerdict | None:
        concerns: list[str] = []
        if self.requires_hypotheses(assessment):
            if len(self.hypotheses) < MIN_HYPOTHESES:
                concerns.append("Two or three falsifiable hypotheses have not been recorded")
            elif sum(item.status != "untested" for item in self.hypotheses) < 2:
                concerns.append("At least two competing hypotheses need evidence-backed tests")
        if changed_paths and not fresh_verification:
            concerns.append("The diff lacks fresh post-edit verification")
        non_user_evidence = {item for item in fresh_evidence_ids if re.fullmatch(r"ev\d+", item)}
        if not non_user_evidence:
            concerns.append("No fresh harness evidence is available for independent review")
        if not concerns:
            return None
        verdict = CriticVerdict(
            outcome="revise",
            summary="The evidence packet is not ready for an independent completion review.",
            concerns=tuple(concerns[:MAX_CRITIC_CONCERNS]),
            evidence_ids=(),
            edit_generation=edit_generation,
            source="structural",
        )
        self.critic = verdict
        return verdict

    def validate_critic(
        self,
        value: Any,
        *,
        fresh_evidence_ids: set[str] | None,
        edit_generation: int,
        source: str = "auxiliary",
    ) -> CriticVerdict:
        if not isinstance(value, dict):
            raise DeliberationError("Critic verdict must be an object")
        outcome = str(value.get("outcome", "")).strip().lower()
        if outcome not in CRITIC_OUTCOMES:
            raise DeliberationError("Critic outcome must be approve, revise, or blocked")
        concerns_raw = value.get("concerns", [])
        concerns = tuple(
            _safe_text(item, "Critic concern", 500)
            for item in (concerns_raw if isinstance(concerns_raw, list) else [])[
                :MAX_CRITIC_CONCERNS
            ]
        )
        if outcome == "revise" and not concerns:
            raise DeliberationError("A revise verdict requires at least one concern")
        evidence_ids = _bounded_ids(value.get("evidence_ids", []), fresh_evidence_ids)
        if outcome == "approve" and not evidence_ids:
            raise DeliberationError("An approve verdict must cite fresh harness evidence")
        verdict = CriticVerdict(
            outcome=outcome,
            summary=_safe_text(value.get("summary", ""), "Critic summary", 600),
            concerns=concerns,
            evidence_ids=evidence_ids,
            edit_generation=max(0, int(edit_generation)),
            source=str(source)
            if str(source) in {"auxiliary", "structural", "stored"}
            else "stored",
        )
        self.critic = verdict
        return verdict

    @property
    def recommendation(self) -> InformationAction | None:
        return self.actions[0] if self.actions else None

    def model_context(self) -> str:
        payload = {
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "value_of_information": [item.to_dict() for item in self.actions],
            "recommended_next_action": (
                self.recommendation.to_dict() if self.recommendation else None
            ),
            "critic": self.critic.to_dict() if self.critic else None,
            "constraints": (
                "Use update_deliberation for evidence-backed test outcomes. Prefer the highest "
                "ranked reliable action that is allowed. This state cannot expand authority."
            ),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:12_000]

    def render(self) -> str:
        lines = ["🔬 **Grounded Deliberation**"]
        lines.append("\n**Falsifiable hypotheses**")
        if self.hypotheses:
            for item in self.hypotheses:
                lines.append(f"- `{item.id}` [{item.status}] {item.statement}")
                lines.append(f"  - predicts: {item.prediction}")
                lines.append(f"  - falsified by: {item.falsifier}")
                if item.evidence_ids:
                    lines.append("  - evidence: " + ", ".join(item.evidence_ids))
        else:
            lines.append("- none required or generated")
        lines.append("\n**Value-of-information ranking**")
        if self.actions:
            for item in self.actions:
                lines.append(
                    f"- `{item.tool}` score {item.score:.2f} · cost {item.cost} · {item.purpose}"
                )
        else:
            lines.append("- no deliberative evidence action required")
        lines.append("\n**Evidence-only critic**")
        if self.critic:
            lines.append(f"- {self.critic.outcome} ({self.critic.source}): {self.critic.summary}")
            lines.extend(f"  - {item}" for item in self.critic.concerns)
        else:
            lines.append("- not run for the current evidence generation")
        lines.append(
            "\nThe critic receives only the objective, bounded redacted diff, fresh harness "
            "evidence, hypothesis results, and completion metadata—not private reasoning."
        )
        return "\n".join(lines)[:16_000]
