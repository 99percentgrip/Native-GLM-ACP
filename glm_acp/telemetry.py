"""Local, redacted trajectory telemetry for evidence-led agent improvement."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import config_dir

TELEMETRY_ENV = "GLM_ACP_TELEMETRY"
_CREDENTIAL = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|credential|authorization)\s*[:=]\s*\S+"
)
_BODY_FIELDS = {"prompt", "content", "reasoning", "arguments", "command", "output", "response"}


def telemetry_enabled() -> bool:
    return os.environ.get(TELEMETRY_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


def trajectory_path() -> Path:
    return config_dir() / "trajectory.jsonl"


def _safe_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _CREDENTIAL.sub("[REDACTED]", value)[:500]
    if isinstance(value, list):
        return [_safe_value(item) for item in value[:30]]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _safe_value(item)
            for key, item in list(value.items())[:30]
            if str(key).lower() not in {"prompt", "content", "reasoning", "arguments", "command"}
        }
    return type(value).__name__


def _safe_field(key: str, value: Any) -> Any:
    if key.lower() in {"path", "paths", "workspace", "cwd", "file", "files"}:
        values = value if isinstance(value, list) else [value]
        return [
            "sha256:" + hashlib.sha256(str(item).encode()).hexdigest()[:16] for item in values[:30]
        ]
    return _safe_value(value)


class TrajectoryRecorder:
    """Append bounded metadata-only events; never persist prompts or tool bodies."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or trajectory_path()

    def record(self, event: str, session_id: str, **fields: Any) -> None:
        if not telemetry_enabled():
            return
        payload = {
            "schema": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": str(event)[:100],
            "session": hashlib.sha256(session_id.encode()).hexdigest()[:16],
            **{
                str(key)[:100]: _safe_field(str(key), value)
                for key, value in fields.items()
                if str(key).lower() not in _BODY_FIELDS
            },
        }
        raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(raw) > 4096:
            payload = {
                "schema": 1,
                "timestamp": payload["timestamp"],
                "event": payload["event"],
                "session": payload["session"],
                "truncated": True,
            }
            raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, raw)
                if os.name != "nt":
                    os.chmod(self.path, 0o600)
            finally:
                os.close(descriptor)
        except OSError:
            # Observability is always fail-open and must never break an agent turn.
            return
