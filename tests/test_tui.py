"""Full-screen terminal frontend behavior and shared-runtime tests."""

from __future__ import annotations

from types import SimpleNamespace

import acp
import pytest
from acp.schema import PermissionOption
from textual.widgets import Input, Select, Static

from glm_acp.cli import build_parser
from glm_acp.terminal_cli import run_chat_command
from glm_acp.tui import NativeGlmTui, PermissionScreen, SettingsScreen


class FakeAgent:
    def __init__(self) -> None:
        self._sessions = {}
        self.conn = None
        self.prompts = []
        self.permission = None
        self.closed = False

    def on_connect(self, conn) -> None:
        self.conn = conn

    async def initialize(self, **kwargs):
        return SimpleNamespace()

    async def new_session(self, cwd, additional_directories=None, **kwargs):
        session = SimpleNamespace(
            model="glm-5.2",
            permission_mode="ask",
            mode="code",
            api_endpoint="coding",
            thought_level="enabled",
            generation_profile="balanced",
            auxiliary_model="main",
            mixture_mode="off",
        )
        self._sessions["tui-session"] = session
        return SimpleNamespace(session_id="tui-session")

    async def set_config_option(self, config_id, session_id, value, **kwargs):
        session = self._sessions[session_id]
        if config_id == "permission_mode":
            session.permission_mode = value
        elif config_id == "model":
            session.model = value
        else:
            setattr(session, config_id, value)

    async def set_session_mode(self, mode_id, session_id, **kwargs):
        self._sessions[session_id].mode = mode_id

    async def prompt(self, prompt, session_id, message_id=None, **kwargs):
        self.prompts.append(prompt)
        tool_id = "tool-1"
        await self.conn.session_update(
            session_id,
            acp.start_tool_call(tool_id, "Write file", kind="edit", status="pending"),
        )
        self.permission = await self.conn.request_permission(
            options=[
                PermissionOption(option_id="allow", kind="allow_once", name="Allow write_file"),
                PermissionOption(option_id="reject", kind="reject_once", name="Deny"),
            ],
            session_id=session_id,
            tool_call=acp.update_tool_call(
                tool_id,
                status="pending",
                raw_input={"path": "demo.txt", "api_key": "must-never-render"},
            ),
        )
        await self.conn.session_update(
            session_id,
            acp.update_agent_message_text("Permission handled."),
        )

    async def cancel(self, **kwargs):
        return None

    async def aclose(self):
        self.closed = True


def _args(tmp_path, *extra):
    return build_parser().parse_args(
        ["chat", "--cwd", str(tmp_path), "--permission", "ask", *extra]
    )


@pytest.mark.asyncio
async def test_tui_mounts_full_screen_panels_and_toggles_thinking(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app._agent_ready is True
        assert app.query_one("#composer", Input).disabled is False
        assert "tui-session" in str(app.query_one("#session", Static).render())
        await pilot.press("f2")
        await pilot.pause()
        assert app.query_one("#thinking").has_class("hidden")
        app.exit(0)

    assert agent.closed is True


@pytest.mark.asyncio
async def test_tui_permission_modal_is_redacted_and_returns_allow(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        composer = app.query_one("#composer", Input)
        composer.value = "Make the requested edit"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, PermissionScreen):
                break
        assert isinstance(app.screen, PermissionScreen)
        detail = str(app.screen.query_one("#permission-detail", Static).render())
        assert "must-never-render" not in detail
        assert "[REDACTED]" in detail
        await pilot.click("#allow")
        for _ in range(20):
            await pilot.pause(0.05)
            if agent.permission is not None:
                break
        assert agent.permission.outcome.outcome == "selected"
        assert app._current_agent_text == "Permission handled."
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_settings_change_shared_session_configuration(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        await pilot.press("f3")
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, SettingsScreen):
                break
        assert isinstance(app.screen, SettingsScreen)
        app.screen.query_one("#permission_mode", Select).value = "read"
        app.screen.query_one("#thought_level", Select).value = "high"
        await pilot.click("#settings-apply")
        for _ in range(20):
            await pilot.pause(0.05)
            if agent._sessions["tui-session"].permission_mode == "read":
                break
        assert agent._sessions["tui-session"].permission_mode == "read"
        assert agent._sessions["tui-session"].thought_level == "high"
        app.exit(0)


def test_interactive_chat_routes_to_tui(monkeypatch, tmp_path):
    args = _args(tmp_path)
    called = []
    monkeypatch.setattr("glm_acp.terminal_cli.sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("glm_acp.terminal_cli.sys.stdout", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        "glm_acp.tui.run_tui_command", lambda received: called.append(received) or 17
    )

    assert run_chat_command(args) == 17
    assert called == [args]
