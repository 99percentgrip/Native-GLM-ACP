"""Hermes-parity coding reliability mechanisms."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from glm_acp.agent import GlmAcpAgent, Session
from glm_acp.diagnostics import DiagnosticsManager, syntax_diagnostics
from glm_acp.glm_client import AuxiliaryResult
from glm_acp.guardrails import ToolLoopGuard
from glm_acp.project_context import (
    detect_project_facts,
    instruction_files,
    progressive_instructions,
)
from glm_acp.tools import ToolResult
from glm_acp.verification import VerificationLedger, classify_verification


def test_progressive_instruction_discovery_walks_root_to_target(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("root rules")
    (tmp_path / ".hermes.md").write_text("hermes rules")
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "python.mdc").write_text("cursor rules")
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    (tmp_path / "src" / "CLAUDE.md").write_text("src rules")
    nested_rules = tmp_path / "src" / ".cursor" / "rules"
    nested_rules.mkdir(parents=True)
    (nested_rules / "scoped.mdc").write_text("scoped cursor rules")

    found = [str(path.relative_to(tmp_path)) for path in instruction_files(nested)]

    assert found == [
        ".hermes.md",
        "AGENTS.md",
        ".cursor/rules/python.mdc",
        "src/CLAUDE.md",
        "src/.cursor/rules/scoped.mdc",
    ]
    rendered = progressive_instructions(str(tmp_path), [str(nested / "main.py")])
    assert "root rules" in rendered
    assert "src rules" in rendered
    assert "cursor rules" in rendered
    assert "scoped cursor rules" in rendered


def test_project_facts_detect_repository_verification_commands(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest", "typecheck": "tsc --noEmit"}})
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9")

    facts = detect_project_facts(tmp_path)

    assert facts.package_managers == ("pnpm",)
    assert facts.verify_commands == ("pnpm run test", "pnpm run typecheck")


def test_verification_ledger_rejects_spoof_and_invalidates_after_edit(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    facts = detect_project_facts(tmp_path)
    ledger = VerificationLedger()

    assert classify_verification("echo pytest passed", facts) is None
    assert classify_verification("pytest --version", facts) is None
    assert classify_verification("pytest | tee results.txt", facts) is None
    assert classify_verification("pytest || true", facts) is None
    assert classify_verification("uv run --frozen pytest -q", facts) == ("pytest", "full")
    event = ledger.record("pytest -q", str(tmp_path), 0, "2 passed", facts)
    assert event is not None
    assert ledger.fresh_pass is event

    ledger.mark_edit(str(tmp_path / "client.py"))
    assert ledger.fresh_pass is None


def test_verification_ledger_persists_scope_and_freshness(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    facts = detect_project_facts(tmp_path)
    ledger = VerificationLedger()
    ledger.mark_edit("client.py")
    event = ledger.record("pytest tests/test_client.py -q", str(tmp_path), 0, "ok", facts)

    restored = VerificationLedger(ledger.to_dict())

    assert event is not None and event.scope == "targeted"
    assert restored.fresh_pass is not None
    assert restored.fresh_pass.scope == "targeted"


def test_syntax_diagnostics_reports_python_json_and_toml_errors(tmp_path: Path):
    assert syntax_diagnostics(tmp_path / "bad.py", "def broken(:\n")
    assert syntax_diagnostics(tmp_path / "bad.json", "{")
    assert syntax_diagnostics(tmp_path / "bad.toml", "[broken")
    assert syntax_diagnostics(tmp_path / "good.py", "value = 1\n") == []


@pytest.mark.asyncio
async def test_diagnostics_manager_falls_back_when_lsp_is_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("glm_acp.diagnostics.shutil.which", lambda command: None)
    manager = DiagnosticsManager()

    result = await manager.check(str(tmp_path / "main.py"), "value = 1\n", str(tmp_path))

    assert result == {"syntax": [], "lsp": [], "lsp_status": "unavailable"}


@pytest.mark.asyncio
async def test_unchanged_read_is_deduplicated(tmp_path: Path):
    agent = GlmAcpAgent()
    session = Session("dedup", str(tmp_path))
    result = ToolResult(output="same content", file_path=str(tmp_path / "file.py"))

    first, _ = await agent._postprocess_tool_result(
        session, "read_file", {"path": "file.py"}, result
    )
    second, _ = await agent._postprocess_tool_result(
        session, "read_file", {"path": "file.py"}, result
    )

    assert first == "same content"
    assert "Unchanged result already provided" in second
    await agent.aclose()


def test_advanced_loop_guard_detects_same_tool_failures_and_no_progress():
    guard = ToolLoopGuard()
    assert (
        guard.observe("grep", {"pattern": "x"}, "none", failed=False, read_only=True).action
        == "allow"
    )
    assert (
        guard.observe("grep", {"pattern": "x"}, "none", failed=False, read_only=True).action
        == "warn"
    )
    for index in range(2):
        decision = guard.observe(
            "run_command", {"command": f"bad {index}"}, "failed", failed=True, read_only=False
        )
    assert decision.action == "allow"
    decision = guard.observe(
        "run_command", {"command": "bad 3"}, "failed", failed=True, read_only=False
    )
    assert decision.action == "warn"


@pytest.mark.asyncio
async def test_goal_and_subgoals_persist_and_judge_continue(tmp_path: Path):
    agent = GlmAcpAgent()
    agent._conn = MagicMock()
    agent._conn.session_update = AsyncMock()
    session = Session("goal", str(tmp_path))
    agent._sessions[session.id] = session
    await agent._handle_command(session, "/goal Fix cleanup")
    await agent._handle_command(session, "/subgoal Tests pass")

    client = MagicMock()
    client.complete_auxiliary = AsyncMock(
        return_value=AuxiliaryResult(
            content='{"done": false, "blocked": false, "reason": "tests missing"}'
        )
    )
    agent._aux_client_for_session = lambda current: client
    continuation = await agent._goal_continuation(session, "Implemented")
    restored = Session.from_dict(session.to_dict(), "restored")

    assert "tests missing" in continuation
    assert restored.goal == "Fix cleanup"
    assert restored.subgoals == ["Tests pass"]
    await agent.aclose()


def test_progressive_context_defers_first_direct_write_under_new_rules(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("Run the scoped checks.")
    agent = GlmAcpAgent()
    session = Session("scoped", str(tmp_path))

    loaded = agent._prepare_progressive_context(
        session, "write_file", {"path": "src/main.py"}
    )

    assert loaded == ["src/AGENTS.md"]
    assert "Run the scoped checks." in session.messages[0]["content"]


@pytest.mark.asyncio
async def test_mixture_of_agents_injects_private_reference_advice(tmp_path: Path, monkeypatch):
    created: list[str] = []

    class FakeClient:
        def __init__(self, model, **kwargs):
            self.model = model
            created.append(model)

        async def complete_auxiliary(self, *args, **kwargs):
            return AuxiliaryResult(content=f"review from {self.model}")

        async def aclose(self):
            return None

    monkeypatch.setattr("glm_acp.agent.GlmClient", FakeClient)
    agent = GlmAcpAgent()
    session = Session("moa", str(tmp_path))
    session.mixture_mode = "enabled"
    session.messages.append({"role": "user", "content": "review this change"})

    messages = await agent._messages_with_references(session)
    cached_messages = await agent._messages_with_references(session)

    assert created == ["glm-5-turbo", "glm-4.7"]
    assert "Private independent reference analyses" in messages[-1]["content"]
    assert "review from glm-5-turbo" in messages[-1]["content"]
    assert cached_messages[-1]["content"] == messages[-1]["content"]
    assert "Private independent reference analyses" not in session.messages[-1]["content"]
    await agent.aclose()
