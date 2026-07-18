"""Semantic navigation, cache layout, telemetry, and lifecycle extension tests."""

from __future__ import annotations

import hashlib
import json

import pytest

from glm_acp.agent import Session
from glm_acp.diagnostics import _LspProcess
from glm_acp.hooks import LifecycleHooks
from glm_acp.mcp import McpManager
from glm_acp.telemetry import TrajectoryRecorder


@pytest.mark.asyncio
async def test_lsp_semantic_request_uses_one_based_tool_positions(tmp_path, monkeypatch):
    path = tmp_path / "main.ts"
    path.write_text("const value = 1;\n")
    process = _LspProcess(["fake-lsp"], tmp_path)
    captured = {}

    async def start():
        return None

    async def open_document(file_path, text, language_id):
        assert file_path == path
        assert language_id == "typescript"
        return path.as_uri()

    async def request(method, params):
        captured.update({"method": method, "params": params})
        return [{"uri": path.as_uri()}]

    monkeypatch.setattr(process, "start", start)
    monkeypatch.setattr(process, "open_document", open_document)
    monkeypatch.setattr(process, "request", request)

    result = await process.semantic(
        "references", path, path.read_text(), "typescript", line=10, column=4
    )

    assert result
    assert captured["method"] == "textDocument/references"
    assert captured["params"]["position"] == {"line": 9, "character": 3}
    assert captured["params"]["context"] == {"includeDeclaration": True}


def test_system_prompt_keeps_stable_prefix_across_dynamic_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path / "config"))
    session = Session("session", str(tmp_path))
    initial = session.messages[0]["content"]
    prefix = initial.partition("<dynamic_context>")[0]

    (tmp_path / "pyproject.toml").write_text("[project]\nname='changed'\n")
    session.refresh_system_prompt("new task")

    assert session.messages[0]["content"].startswith(prefix + "<dynamic_context>")
    assert session.system_prefix_hash == hashlib.sha256(prefix.encode()).hexdigest()[:16]


def test_trajectory_telemetry_is_metadata_only_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_ACP_TELEMETRY", "1")
    path = tmp_path / "trajectory.jsonl"
    recorder = TrajectoryRecorder(path)
    recorder.record(
        "tool_call",
        "real-session-id",
        tool="run_command",
        command="ZAI_API_KEY=top-secret",
        content="private output",
        note="token=top-secret",
    )

    raw = path.read_text()
    payload = json.loads(raw)
    assert "real-session-id" not in raw
    assert "top-secret" not in raw
    assert "private output" not in raw
    assert payload["note"] == "[REDACTED]"


def test_trajectory_telemetry_fails_open_on_unwritable_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_ACP_TELEMETRY", "1")

    def denied(*args, **kwargs):
        raise OSError("denied")

    monkeypatch.setattr("glm_acp.telemetry.os.open", denied)
    TrajectoryRecorder(tmp_path / "trajectory.jsonl").record("turn", "session", model="glm")


@pytest.mark.asyncio
async def test_lifecycle_hook_requires_matching_hash_and_returns_directive(tmp_path):
    script = tmp_path / "hook.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'action': 'continue', 'message': 'run formatter'}))\n"
    )
    script.chmod(0o700)
    config = tmp_path / "hooks.json"
    config.write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "event": "pre_verify",
                        "command": [str(script)],
                        "sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                        "workspace": str(tmp_path),
                    }
                ]
            }
        )
    )

    results = await LifecycleHooks(config).emit("pre_verify", str(tmp_path), {"attempt": 0})
    assert results == [{"action": "continue", "message": "run formatter"}]

    script.write_text(script.read_text() + "# changed\n")
    assert await LifecycleHooks(config).emit("pre_verify", str(tmp_path), {}) == []


@pytest.mark.asyncio
async def test_browser_ui_maps_only_to_allowlisted_playwright_tools(monkeypatch):
    manager = McpManager({"playwright": {"url": "https://example.invalid"}})
    captured = {}

    async def call(server, tool, arguments):
        captured.update({"server": server, "tool": tool, "arguments": arguments})
        return {"content": "snapshot"}

    monkeypatch.setattr(manager, "call", call)
    result = await manager.invoke_preset("browser_ui", {"action": "snapshot", "arguments": {}})

    assert result == {"content": "snapshot"}
    assert captured == {"server": "playwright", "tool": "browser_snapshot", "arguments": {}}
