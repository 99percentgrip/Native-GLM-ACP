"""Bounded, inspectable epistemic state and evidence-backed completion certificates."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .security import scan_promptware

MAX_RECORDS = 100
MAX_EVIDENCE_EVENTS = 200
MAX_RECORD_CHARS = 500
MAX_SCOPES = 20
MAX_SUPPORTS = 20

RECORD_KINDS = {
    "observation",
    "assumption",
    "hypothesis",
    "contradiction",
    "unknown",
    "capability",
}
RECORD_STATUSES = {"active", "resolved", "stale", "invalidated"}
CONFIDENCE_BANDS = {"low", "medium", "high"}
EVIDENCE_SOURCES = {
    "user",
    "read",
    "search",
    "edit",
    "diagnostic",
    "verification",
    "tool",
}
_EDIT_SENSITIVE_SOURCES = {"read", "search", "edit", "diagnostic", "verification", "tool"}
_SENSITIVE = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|token|secret|password|credential)\s*[:=]\s*[^\s]{8,}",
        re.IGNORECASE,
    ),
)


class AwarenessError(ValueError):
    """Raised when model-supplied epistemic state violates the bounded contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, label: str, limit: int = MAX_RECORD_CHARS) -> str:
    text = " ".join(str(value).strip().split())
    if not text:
        raise AwarenessError(f"{label} cannot be empty")
    if len(text) > limit:
        raise AwarenessError(f"{label} exceeds the {limit}-character limit")
    if any(pattern.search(text) for pattern in _SENSITIVE):
        raise AwarenessError(f"{label} appears to contain a credential or secret")
    if scan_promptware(text):
        raise AwarenessError(f"{label} appears to contain prompt-injection instructions")
    return text


def _bounded_values(values: Any, limit: int, item_limit: int = 500) -> list[str]:
    if not isinstance(values, list):
        return []
    output: list[str] = []
    for value in values[:limit]:
        text = " ".join(str(value).strip().split())[:item_limit]
        if text and text not in output:
            output.append(text)
    return output


def _normalized_scope(value: str) -> str:
    return value.replace("\\", "/").rstrip("/")


def _overlaps(left: str, right: str) -> bool:
    left = _normalized_scope(left)
    right = _normalized_scope(right)
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


@dataclass
class EvidenceEvent:
    id: str
    source: str
    summary: str
    scopes: list[str]
    edit_generation: int
    created_at: str
    stale: bool = False
    stale_reason: str = ""


@dataclass
class EpistemicRecord:
    id: str
    kind: str
    summary: str
    confidence: str
    status: str
    evidence_ids: list[str]
    supports: list[str]
    scopes: list[str]
    created_at: str
    updated_at: str
    edit_generation: int
    stale_reason: str = ""


@dataclass
class CompletionCriterion:
    id: str
    description: str
    supported: bool
    evidence_ids: list[str]
    record_ids: list[str]


@dataclass
class CompletionCertificate:
    created_at: str
    edit_generation: int
    complete: bool
    blocked: bool
    criteria: list[CompletionCriterion]
    contradictions: list[str]
    stale_evidence: int
    verification_required: bool
    fresh_verification: bool

    @property
    def coverage(self) -> float:
        if not self.criteria:
            return 1.0
        return sum(item.supported for item in self.criteria) / len(self.criteria)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["coverage"] = round(self.coverage, 4)
        return value


class EpistemicLedger:
    """Persist bounded claims that cite only harness-issued metadata evidence."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data if isinstance(data, dict) else {}
        self.objective = str(data.get("objective", ""))[:2000]
        self._next_record = max(1, int(data.get("next_record", 1) or 1))
        self._next_evidence = max(1, int(data.get("next_evidence", 1) or 1))
        self.evidence: list[EvidenceEvent] = []
        for value in data.get("evidence", [])[-MAX_EVIDENCE_EVENTS:]:
            try:
                event = EvidenceEvent(**value)
            except (TypeError, ValueError):
                continue
            if event.source in EVIDENCE_SOURCES:
                self.evidence.append(event)
        self.records: list[EpistemicRecord] = []
        for value in data.get("records", [])[-MAX_RECORDS:]:
            try:
                record = EpistemicRecord(**value)
            except (TypeError, ValueError):
                continue
            if (
                record.kind in RECORD_KINDS
                and record.status in RECORD_STATUSES
                and record.confidence in CONFIDENCE_BANDS
            ):
                self.records.append(record)
        certificate = data.get("last_certificate")
        self.last_certificate: CompletionCertificate | None = None
        if isinstance(certificate, dict):
            try:
                criteria = [
                    CompletionCriterion(**item)
                    for item in certificate.get("criteria", [])
                    if isinstance(item, dict)
                ]
                self.last_certificate = CompletionCertificate(
                    created_at=str(certificate.get("created_at", "")),
                    edit_generation=int(certificate.get("edit_generation", 0)),
                    complete=bool(certificate.get("complete", False)),
                    blocked=bool(certificate.get("blocked", False)),
                    criteria=criteria,
                    contradictions=[str(item) for item in certificate.get("contradictions", [])],
                    stale_evidence=int(certificate.get("stale_evidence", 0)),
                    verification_required=bool(certificate.get("verification_required", False)),
                    fresh_verification=bool(certificate.get("fresh_verification", False)),
                )
            except (TypeError, ValueError):
                self.last_certificate = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "next_record": self._next_record,
            "next_evidence": self._next_evidence,
            "evidence": [asdict(item) for item in self.evidence[-MAX_EVIDENCE_EVENTS:]],
            "records": [asdict(item) for item in self.records[-MAX_RECORDS:]],
            "last_certificate": (
                self.last_certificate.to_dict() if self.last_certificate is not None else None
            ),
        }

    def set_objective(self, objective: str, *, force: bool = False) -> None:
        normalized = " ".join(str(objective).strip().split())[:2000]
        if self.objective and (force or normalized != self.objective):
            for record in self.records:
                if record.status == "active":
                    record.status = "invalidated"
                    record.updated_at = _now()
                    record.stale_reason = "objective changed"
        self.objective = normalized
        self.last_certificate = None

    def invalidate_criterion_support(self) -> None:
        """Invalidate indexed criterion claims after the criterion list changes."""
        for record in self.records:
            if record.status == "active" and any(
                item.startswith("criterion:") for item in record.supports
            ):
                record.status = "invalidated"
                record.updated_at = _now()
                record.stale_reason = "acceptance criteria changed"
        self.last_certificate = None

    def note_evidence(
        self,
        source: str,
        summary: str,
        edit_generation: int,
        scopes: list[str] | None = None,
    ) -> EvidenceEvent:
        if source not in EVIDENCE_SOURCES:
            raise AwarenessError(f"Unsupported evidence source: {source}")
        event = EvidenceEvent(
            id=f"ev{self._next_evidence}",
            source=source,
            summary=_safe_text(summary, "Evidence summary", 300),
            scopes=_bounded_values(scopes or [], MAX_SCOPES, 1000),
            edit_generation=max(0, int(edit_generation)),
            created_at=_now(),
        )
        self._next_evidence += 1
        self.evidence.append(event)
        self.evidence = self.evidence[-MAX_EVIDENCE_EVENTS:]
        self.last_certificate = None
        return event

    def upsert(
        self,
        *,
        kind: str,
        summary: str,
        confidence: str,
        edit_generation: int,
        evidence_ids: list[str] | None = None,
        supports: list[str] | None = None,
        scopes: list[str] | None = None,
        record_id: str = "",
        allowed_supports: set[str] | None = None,
    ) -> EpistemicRecord:
        if kind not in RECORD_KINDS:
            raise AwarenessError(f"Unsupported epistemic kind: {kind}")
        if confidence not in CONFIDENCE_BANDS:
            raise AwarenessError(f"Unsupported confidence band: {confidence}")
        known_evidence = {item.id for item in self.evidence}
        cited = _bounded_values(evidence_ids or [], 20, 40)
        missing = [item for item in cited if item not in known_evidence]
        if missing:
            raise AwarenessError("Unknown evidence ids: " + ", ".join(missing))
        supported = _bounded_values(supports or [], MAX_SUPPORTS, 80)
        if allowed_supports is not None:
            invalid = [item for item in supported if item not in allowed_supports]
            if invalid:
                raise AwarenessError("Unknown completion criteria: " + ", ".join(invalid))
        now = _now()
        existing = next((item for item in self.records if item.id == record_id), None)
        if existing is not None:
            existing.kind = kind
            existing.summary = _safe_text(summary, "Record summary")
            existing.confidence = confidence
            existing.status = "active"
            existing.evidence_ids = cited
            existing.supports = supported
            existing.scopes = _bounded_values(scopes or [], MAX_SCOPES, 1000)
            existing.updated_at = now
            existing.edit_generation = max(0, int(edit_generation))
            existing.stale_reason = ""
            record = existing
        else:
            record = EpistemicRecord(
                id=f"ep{self._next_record}",
                kind=kind,
                summary=_safe_text(summary, "Record summary"),
                confidence=confidence,
                status="active",
                evidence_ids=cited,
                supports=supported,
                scopes=_bounded_values(scopes or [], MAX_SCOPES, 1000),
                created_at=now,
                updated_at=now,
                edit_generation=max(0, int(edit_generation)),
            )
            self._next_record += 1
            self.records.append(record)
            self.records = self.records[-MAX_RECORDS:]
        self.last_certificate = None
        return record

    def set_status(self, record_id: str, status: str) -> EpistemicRecord:
        if status not in {"resolved", "invalidated"}:
            raise AwarenessError("Record status action must be resolved or invalidated")
        record = next((item for item in self.records if item.id == record_id), None)
        if record is None:
            raise AwarenessError(f"Unknown epistemic record: {record_id}")
        record.status = status
        record.updated_at = _now()
        record.stale_reason = ""
        self.last_certificate = None
        return record

    def mark_edit(self, path: str, edit_generation: int) -> None:
        target = _normalized_scope(str(path))
        stale_ids: set[str] = set()
        for event in self.evidence:
            if event.stale or event.source not in _EDIT_SENSITIVE_SOURCES:
                continue
            if event.edit_generation >= edit_generation:
                continue
            if event.scopes and not any(_overlaps(scope, target) for scope in event.scopes):
                continue
            event.stale = True
            event.stale_reason = f"superseded by edit generation {edit_generation}"
            stale_ids.add(event.id)
        for record in self.records:
            if record.status != "active" or not record.evidence_ids:
                continue
            referenced = [item for item in self.evidence if item.id in record.evidence_ids]
            if referenced and all(item.stale for item in referenced):
                record.status = "stale"
                record.updated_at = _now()
                record.stale_reason = f"support superseded by edit generation {edit_generation}"
        if stale_ids:
            self.last_certificate = None

    def active_records(self) -> list[EpistemicRecord]:
        return [item for item in self.records if item.status == "active"]

    def fresh_evidence_ids(self) -> set[str]:
        return {item.id for item in self.evidence if not item.stale}

    @staticmethod
    def criterion_map(goal: str, criteria: list[str], task: str = "") -> list[tuple[str, str]]:
        if goal:
            return [
                ("goal", goal),
                *[(f"criterion:{i}", value) for i, value in enumerate(criteria, 1)],
            ]
        return [("task", task)] if task.strip() else []

    def build_certificate(
        self,
        *,
        goal: str,
        criteria: list[str],
        task: str,
        edit_generation: int,
        changed_paths: list[str],
        fresh_verification: bool,
        blocked: bool = False,
    ) -> CompletionCertificate:
        requirements = self.criterion_map(goal, criteria, task)
        completion_evidence = {
            item.id for item in self.evidence if not item.stale and item.source != "user"
        }
        certificate_criteria: list[CompletionCriterion] = []
        for criterion_id, description in requirements:
            records = [
                item
                for item in self.records
                if item.status == "active"
                and item.kind == "observation"
                and criterion_id in item.supports
                and any(evidence_id in completion_evidence for evidence_id in item.evidence_ids)
            ]
            evidence_ids = list(
                dict.fromkeys(
                    evidence_id
                    for record in records
                    for evidence_id in record.evidence_ids
                    if evidence_id in completion_evidence
                )
            )[:20]
            certificate_criteria.append(
                CompletionCriterion(
                    id=criterion_id,
                    description=description[:1000],
                    supported=bool(records and evidence_ids),
                    evidence_ids=evidence_ids,
                    record_ids=[item.id for item in records[:20]],
                )
            )
        contradictions = [
            item.id
            for item in self.records
            if item.status == "active" and item.kind == "contradiction"
        ]
        verification_required = bool(changed_paths)
        complete = (
            bool(certificate_criteria)
            and all(item.supported for item in certificate_criteria)
            and not contradictions
            and (not verification_required or fresh_verification)
            and not blocked
        )
        certificate = CompletionCertificate(
            created_at=_now(),
            edit_generation=max(0, int(edit_generation)),
            complete=complete,
            blocked=blocked,
            criteria=certificate_criteria,
            contradictions=contradictions[:20],
            stale_evidence=sum(item.stale for item in self.evidence),
            verification_required=verification_required,
            fresh_verification=fresh_verification,
        )
        self.last_certificate = certificate
        return certificate

    def render(self, goal: str, criteria: list[str], task: str, edit_generation: int) -> str:
        requirements = self.criterion_map(goal, criteria, task)
        active = self.active_records()
        stale = [item for item in self.records if item.status == "stale"]
        fresh_events = [item for item in self.evidence if not item.stale][-12:]
        lines = ["🧭 **Awareness**"]
        lines.append(f"- **Objective:** {goal or self.objective or task or 'none'}")
        lines.append(f"- **Edit generation:** {edit_generation}")
        lines.append(
            f"- **State:** {len(active)} active · {len(stale)} stale · "
            f"{len(fresh_events)} recent fresh evidence events"
        )
        if requirements:
            lines.append("\n**Completion criteria**")
            lines.extend(f"- `{item_id}` — {description}" for item_id, description in requirements)
        groups = [
            ("Verified observations", [item for item in active if item.kind == "observation"]),
            ("Assumptions", [item for item in active if item.kind == "assumption"]),
            ("Hypotheses", [item for item in active if item.kind == "hypothesis"]),
            ("Contradictions", [item for item in active if item.kind == "contradiction"]),
            ("Unknowns", [item for item in active if item.kind == "unknown"]),
            ("Capability limitations", [item for item in active if item.kind == "capability"]),
        ]
        for label, records in groups:
            lines.append(f"\n**{label}**")
            if not records:
                lines.append("- none")
                continue
            for record in records[:20]:
                evidence = ", ".join(record.evidence_ids) or "no evidence"
                support = f"; supports {', '.join(record.supports)}" if record.supports else ""
                lines.append(
                    f"- `{record.id}` [{record.confidence}] {record.summary} "
                    f"(evidence: {evidence}{support})"
                )
        lines.append("\n**Recent fresh evidence IDs**")
        if fresh_events:
            lines.extend(
                f"- `{item.id}` [{item.source}] {item.summary}"
                + (f" — {', '.join(item.scopes[:3])}" if item.scopes else "")
                for item in fresh_events
            )
        else:
            lines.append("- none")
        certificate = self.last_certificate
        lines.append("\n**Completion certificate**")
        if certificate is None:
            lines.append("- not evaluated")
        else:
            lines.append(
                f"- **Complete:** {'yes' if certificate.complete else 'no'} · "
                f"coverage {certificate.coverage:.0%} · "
                f"fresh verification {'yes' if certificate.fresh_verification else 'no'}"
            )
            for item in certificate.criteria:
                marker = "✓" if item.supported else "✗"
                lines.append(
                    f"- {marker} `{item.id}` — "
                    + (", ".join(item.evidence_ids) if item.evidence_ids else "unsupported")
                )
            if certificate.contradictions:
                lines.append("- Active contradictions: " + ", ".join(certificate.contradictions))
        next_step = "No unresolved epistemic blocker is recorded."
        if any(item.kind == "contradiction" for item in active):
            next_step = "Resolve active contradictions with discriminating external evidence."
        elif stale:
            next_step = "Refresh stale evidence after the latest relevant edits."
        elif any(item.kind == "unknown" for item in active):
            next_step = "Gather evidence for the highest-impact unknown."
        elif certificate and not certificate.complete:
            next_step = "Add fresh evidence-backed observations for unsupported criteria."
        lines.append(f"\n**Next evidence:** {next_step}")
        return "\n".join(lines)[:16_000]

    def model_context(self, goal: str, criteria: list[str], task: str) -> str:
        requirements = self.criterion_map(goal, criteria, task)
        active = self.active_records()[-30:]
        fresh_events = [item for item in self.evidence if not item.stale][-20:]
        if not requirements and not active and not fresh_events:
            return ""
        payload = {
            "criteria": [{"id": item_id, "description": text} for item_id, text in requirements],
            "records": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "summary": item.summary,
                    "confidence": item.confidence,
                    "evidence_ids": item.evidence_ids,
                    "supports": item.supports,
                }
                for item in active
            ],
            "fresh_evidence": [
                {
                    "id": item.id,
                    "source": item.source,
                    "summary": item.summary,
                    "scopes": item.scopes[:5],
                }
                for item in fresh_events
            ],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:8000]
