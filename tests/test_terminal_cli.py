"""Standalone terminal frontend parity and safety tests."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import acp
import pytest
from acp.schema import PermissionOption

from glm_acp.cli import build_parser
from glm_acp.terminal_cli import TerminalClient, _configure


def test_chat_parser_exposes_full_session_configuration(tmp_path):
    args = build_parser().parse_args(
        [
            "chat",
            "--cwd",
            str(tmp_path),
            "--model",
            "glm-5.2",
            "--thought-level",
            "max",
            "--api-endpoint",
            "coding",
            "--permission",
            "bypass",
            "--generation-profile",
            "precise",
            "--auxiliary-model",
            "glm-4.7",
            "--mixture-mode",
            "enabled",
            "--mode",
            "code",
        ]
    )
    assert args.command == "chat"
    assert args.model == "glm-5.2"
    assert args.permission == "bypass"
    assert args.mode == "code"


@pytest.mark.asyncio
async def test_terminal_client_streams_agent_text_and_hides_thinking(capsys):
    client = TerminalClient(show_thinking=False, interactive=False)
    await client.session_update("session", acp.update_agent_thought_text("private"))
    await client.session_update("session", acp.update_agent_message_text("hello"))
    assert client.finish_turn() == "hello"
    output = capsys.readouterr()
    assert "hello" in output.out
    assert "private" not in output.out + output.err


@pytest.mark.asyncio
async def test_noninteractive_permission_fails_closed():
    client = TerminalClient(interactive=False)
    response = await client.request_permission(
        options=[PermissionOption(option_id="allow", kind="allow_once", name="Allow")],
        session_id="session",
        tool_call=SimpleNamespace(title="write file"),
    )
    assert response.outcome.outcome == "cancelled"


def test_permission_details_are_bounded_and_credential_redacted():
    detail = TerminalClient._permission_detail(
        {
            "command": "curl -H 'Authorization: Bearer very-secret-token-value' /api",
            "content": "x" * 10_000,
            "api_key": "must-never-appear",
        }
    )
    assert "must-never-appear" not in detail
    assert "very-secret-token-value" not in detail
    assert "[10000 characters]" in detail


@pytest.mark.asyncio
async def test_terminal_configuration_uses_agent_session_methods():
    calls = []

    class AgentStub:
        async def set_config_option(self, **kwargs):
            calls.append(("config", kwargs["config_id"], kwargs["value"]))

        async def set_session_mode(self, **kwargs):
            calls.append(("mode", kwargs["mode_id"]))

    args = argparse.Namespace(
        model="glm-5.2",
        thought_level="high",
        api_endpoint="coding",
        permission="read",
        generation_profile="balanced",
        auxiliary_model="glm-4.7",
        mixture_mode="enabled",
        mode="ask",
    )
    await _configure(AgentStub(), "session", args)
    assert ("config", "permission_mode", "read") in calls
    assert ("config", "mixture_mode", "enabled") in calls
    assert ("mode", "ask") in calls
