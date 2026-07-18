"""Explicitly trusted, hash-pinned lifecycle hooks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .config import config_dir

HOOKS_CONFIG_ENV = "GLM_ACP_HOOKS_CONFIG"
_EVENTS = {"pre_tool_call", "post_tool_call", "pre_verify", "post_llm_call"}
_SENSITIVE_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_CREDENTIAL",
    "_PRIVATE_KEY",
    "_ACCESS_KEY",
)


def hooks_config_path() -> Path:
    override = os.environ.get(HOOKS_CONFIG_ENV)
    return Path(override).expanduser() if override else config_dir() / "hooks.json"


def _scrubbed_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().endswith(_SENSITIVE_SUFFIXES)
        and key.upper() not in {"ZAI_API_KEY", "Z_AI_API_KEY", "SSH_AUTH_SOCK"}
    }


class LifecycleHooks:
    """Run user-configured commands only when their executable content hash matches."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or hooks_config_path()

    def _configured(self, event: str, cwd: str) -> list[dict[str, Any]]:
        if event not in _EVENTS:
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return []
        hooks = payload.get("hooks", []) if isinstance(payload, dict) else []
        root = Path(cwd).resolve()
        result: list[dict[str, Any]] = []
        for hook in hooks if isinstance(hooks, list) else []:
            if not isinstance(hook, dict) or hook.get("event") != event:
                continue
            command = hook.get("command")
            if not isinstance(command, list) or not command or not all(
                isinstance(item, str) for item in command
            ):
                continue
            workspace = hook.get("workspace")
            if workspace and Path(str(workspace)).resolve() != root:
                continue
            executable = Path(command[0]).expanduser().resolve()
            expected = str(hook.get("sha256", "")).lower()
            try:
                actual = hashlib.sha256(executable.read_bytes()).hexdigest()
            except OSError:
                continue
            if actual != expected:
                continue
            result.append({**hook, "command": [str(executable), *command[1:]]})
        return result[:10]

    async def emit(self, event: str, cwd: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Emit metadata to matching hooks; failures are isolated and fail open."""
        results: list[dict[str, Any]] = []
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()[:16000]
        for hook in self._configured(event, cwd):
            try:
                timeout = min(10.0, max(0.1, float(hook.get("timeout", 3))))
            except (TypeError, ValueError):
                continue
            process: asyncio.subprocess.Process | None = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *hook["command"],
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=cwd,
                    env=_scrubbed_environment(),
                )
                stdout, _ = await asyncio.wait_for(process.communicate(body), timeout=timeout)
                if process.returncode == 0 and stdout:
                    value = json.loads(stdout[:8000])
                    if isinstance(value, dict):
                        results.append(value)
            except (OSError, ValueError, json.JSONDecodeError, asyncio.TimeoutError):
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                continue
        return results
