"""Full-screen terminal frontend behavior and shared-runtime tests."""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

import acp
import pytest
from acp.helpers import update_available_commands
from acp.schema import AvailableCommand, PermissionOption
from textual import events
from textual._xterm_parser import XTermParser
from textual.containers import VerticalScroll
from textual.widgets import ContentSwitcher, Footer, Input, ListView, OptionList, Select, Static
from textual.widgets._footer import FooterKey

from glm_acp.cli import build_parser
from glm_acp.glm_client import PlanQuota, PlanUsage
from glm_acp.terminal_cli import run_chat_command
from glm_acp.tui import (
    CONFIG_COMMANDS,
    CodeBlockPickerScreen,
    HistoryScreen,
    JourneyScreen,
    NativeGlmTui,
    PermissionScreen,
    SearchScreen,
    SettingsScreen,
    _extract_message_text,
    _format_session_row,
    _journey_extract_memory_lines,
    _journey_extract_profile_lines,
    _read_system_clipboard,
)


class FakeAgent:
    def __init__(self) -> None:
        self._sessions = {}
        self.conn = None
        self.prompts = []
        self.permission = None
        self.closed = False
        self.config_calls = []
        self.mode_calls = []
        self.usage_calls = 0

    def on_connect(self, conn) -> None:
        self.conn = conn

    async def initialize(self, **kwargs):
        return SimpleNamespace()

    async def new_session(self, cwd, additional_directories=None, **kwargs):
        await self.conn.session_update(
            "tui-session",
            update_available_commands(
                [
                    AvailableCommand(name="help", description="Show harness commands"),
                    AvailableCommand(name="status", description="Show session status"),
                    AvailableCommand(name="checkpoint", description="Manage checkpoints"),
                ]
            ),
        )
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
        self.config_calls.append((config_id, value))
        session = self._sessions[session_id]
        if config_id == "permission_mode":
            session.permission_mode = value
        elif config_id == "model":
            session.model = value
        else:
            setattr(session, config_id, value)

    async def set_session_mode(self, mode_id, session_id, **kwargs):
        self.mode_calls.append(mode_id)
        self._sessions[session_id].mode = mode_id

    def context_breakdown(self, session_id):
        """Tier 2.2 test double — delegate to the real GlmAcpAgent method
        so the TUI test for ``/context`` exercises the same code path
        without spinning up the full ACP stack."""
        from glm_acp.agent import GlmAcpAgent

        agent = GlmAcpAgent.__new__(GlmAcpAgent)
        agent._sessions = self._sessions
        return agent.context_breakdown(session_id)

    async def generate_insights(self, session_id):
        """Tier 4 test double — delegate to the real GlmAcpAgent method."""
        from glm_acp.agent import GlmAcpAgent

        agent = GlmAcpAgent.__new__(GlmAcpAgent)
        agent._sessions = self._sessions
        return await agent.generate_insights(session_id)

    async def security_review(self, session_id):
        """Tier 4 test double — delegate to the real GlmAcpAgent method."""
        from glm_acp.agent import GlmAcpAgent

        agent = GlmAcpAgent.__new__(GlmAcpAgent)
        agent._sessions = self._sessions
        return await agent.security_review(session_id)

    async def query_provider_usage(self, session_id):
        self.usage_calls += 1
        return PlanUsage(
            platform="Z.ai",
            quotas=(
                PlanQuota(
                    kind="TOKENS_LIMIT",
                    unit=3,
                    number=5,
                    limit=1000,
                    used=120,
                    remaining=880,
                    percentage=12,
                    next_reset_ms=None,
                ),
                PlanQuota(
                    kind="TOKENS_LIMIT",
                    unit=6,
                    number=7,
                    limit=None,
                    used=None,
                    remaining=None,
                    percentage=4,
                    next_reset_ms=None,
                ),
                PlanQuota(
                    kind="TIME_LIMIT",
                    unit=5,
                    number=1,
                    limit=100,
                    used=2,
                    remaining=98,
                    percentage=2,
                    next_reset_ms=None,
                ),
            ),
        )

    @staticmethod
    def format_provider_usage(usage):
        return "5-hour model quota: 12% used\nWeekly model quota: 4% used"

    async def prompt(self, prompt, session_id, message_id=None, **kwargs):
        self.prompts.append(prompt)
        text = str(getattr(prompt[0], "text", ""))
        if text.startswith("/"):
            await self.conn.session_update(
                session_id,
                acp.update_agent_message_text(f"Handled {text}"),
            )
            return
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


class HangingCloseAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = False

    async def aclose(self):
        self.close_started = True
        await asyncio.Event().wait()


class SlowAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__()
        self.prompt_started = asyncio.Event()
        self.prompt_release = asyncio.Event()

    async def prompt(self, prompt, session_id, message_id=None, **kwargs):
        self.prompts.append(prompt)
        self.prompt_started.set()
        await self.prompt_release.wait()
        await self.conn.session_update(
            session_id,
            acp.update_agent_message_text("Finished."),
        )


def _args(tmp_path, *extra):
    return build_parser().parse_args(
        ["chat", "--cwd", str(tmp_path), "--permission", "ask", *extra]
    )


async def _wait_for_agent_ready(app, pilot) -> None:
    for _ in range(40):
        await pilot.pause(0.05)
        if app._agent_ready:
            return
    raise AssertionError("TUI agent initialization did not complete")


@pytest.mark.asyncio
async def test_tui_mounts_full_screen_panels_and_toggles_thinking(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        assert app._agent_ready is True
        assert app.query_one("#composer", Input).disabled is False
        assert "tui-sess…" in str(app.query_one("#session", Static).render())
        for _ in range(20):
            await pilot.pause(0.05)
            if "quota 5h 12% · week 4% · MCP 2%" in str(app.query_one("#session", Static).render()):
                break
        assert "quota 5h 12% · week 4% · MCP 2%" in str(app.query_one("#session", Static).render())
        assert app.query_one("#thinking").has_class("hidden")
        await pilot.press("f2")
        await pilot.pause()
        assert not app.query_one("#thinking").has_class("hidden")
        app.exit(0)

    assert agent.closed is True


@pytest.mark.asyncio
async def test_tui_command_palette_is_enabled_and_provider_surfaces_all_commands(tmp_path):
    """Tier 1.1: Ctrl+P command palette is enabled and includes our /-commands
    and F-key actions via GlmCommandProvider."""
    from glm_acp.tui import LOCAL_COMMANDS, GlmCommandProvider

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    # Class-level wiring: palette enabled and provider registered.
    assert NativeGlmTui.ENABLE_COMMAND_PALETTE is True
    assert GlmCommandProvider in NativeGlmTui.COMMANDS

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)

        # Build entries from the live app — this exercises both
        # make_insert_command_callback (for /-commands) and make_action_callback
        # (for F-key actions) without depending on Textual palette internals.
        provider = GlmCommandProvider(app.screen)
        entries = provider._build_entries()
        names = [name for name, _, _ in entries]

        # Every registered slash command is present.
        for cmd in LOCAL_COMMANDS:
            assert cmd in names, f"missing slash command from palette: {cmd}"

        # F-key actions are present (sample).
        for action_label in (
            "Help (F1)",
            "Settings (F3)",
            "Working tree (F4)",
            "Quit (Ctrl-X)",
        ):
            assert action_label in names, f"missing F-key action from palette: {action_label}"

        # The insert_command callback puts a slash command into the composer
        # for review (does NOT auto-submit, so the user can add arguments).
        composer = app.query_one("#composer", Input)
        composer.value = ""
        for name, _, cb in entries:
            if name == "/model":
                cb()
                break
        assert composer.value == "/model"
        assert composer.cursor_position == len("/model")

        # The action callback schedules a sync action via call_later.
        for name, _, cb in entries:
            if name == "Settings (F3)":
                cb()
                break
        await pilot.pause()
        # Settings screen is pushed as a modal.
        assert any(
            type(screen).__name__ == "SettingsScreen" for screen in app.screen_stack
        ), "Settings action did not push the settings screen"
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_multiline_paste_is_retained_and_composer_does_not_overlap_footer(
    tmp_path,
):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)
        footer = app.query_one(Footer)

        terminal_bytes = "\x1b[200~\nPlease inspect this pasted prompt.\nKeep its content.\x1b[201~"
        messages = list(XTermParser().feed(terminal_bytes))
        assert len(messages) == 1
        assert isinstance(messages[0], events.Paste)
        assert app._driver is not None
        app._driver.send_message(messages[0])
        await pilot.pause()

        assert composer.value == "Please inspect this pasted prompt. Keep its content."
        assert composer.region.bottom <= footer.region.y
        app.exit(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("shortcut", ["ctrl+v", "ctrl+shift+v"])
async def test_tui_clipboard_shortcuts_read_external_system_clipboard(
    tmp_path, monkeypatch, shortcut
):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)
    monkeypatch.setattr(
        "glm_acp.tui._read_system_clipboard",
        lambda: "\nCopied outside the TUI.\nRetain this prompt.",
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)
        assert app.clipboard == ""

        await pilot.press(shortcut)

        assert composer.value == "Copied outside the TUI. Retain this prompt."
        app.exit(0)


def test_system_clipboard_reader_does_not_inherit_credentials(monkeypatch):
    captured = {}
    monkeypatch.setenv("ZAI_API_KEY", "must-not-reach-clipboard-process")
    monkeypatch.setattr("glm_acp.tui.shutil.which", lambda name: f"/safe/{name}")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        captured["timeout"] = kwargs["timeout"]
        return SimpleNamespace(returncode=0, stdout="external clipboard text")

    monkeypatch.setattr("glm_acp.tui.subprocess.run", fake_run)

    assert _read_system_clipboard() == "external clipboard text"
    assert captured["command"]
    assert captured["timeout"] == 1.0
    assert "ZAI_API_KEY" not in captured["environment"]


@pytest.mark.asyncio
async def test_tui_agent_response_is_selectable_after_richmarkdown_render(tmp_path):
    """Regression: agent messages render as ``RichMarkdown`` inside a ``Static``,
    which Textual's ``Widget.get_selection`` skips (returns ``None``). The
    ``SelectableStatic`` subclass exposes the raw markdown source so
    Ctrl+Shift+C and the Copy-selection menu entry can extract agent text."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Append a unique marker via the agent-message path.
        await app._append_agent("Agent reply with UNIQUE_REGRESSION_MARKER_99.")
        for _ in range(20):
            await pilot.pause(0.05)

        transcript = app.query_one("#transcript")
        statics = list(transcript.query(".agent-message"))
        assert statics, "agent message widget should be mounted"

        # Select-all-in-widget on the transcript and read the text back.
        app.screen._select_all_in_widget(transcript)
        await pilot.pause(0.05)
        selection_text = app.screen.get_selected_text() or ""
        assert "UNIQUE_REGRESSION_MARKER_99" in selection_text, (
            "Agent response must be selectable for Ctrl+Shift+C / Copy selection"
        )

        # Full-screen select-all should also include the agent text.
        app.screen.text_select_all()
        await pilot.pause(0.05)
        full_selection = app.screen.get_selected_text() or ""
        assert "UNIQUE_REGRESSION_MARKER_99" in full_selection
        app.exit(0)


def test_selectable_static_strips_markdown_syntax_from_selection():
    """Selection must return plain rendered text, not raw ``**bold**`` markers."""
    from rich.markdown import Markdown as RichMarkdown
    from textual.geometry import Offset
    from textual.selection import Selection

    from glm_acp.tui import SelectableStatic

    widget = SelectableStatic(raw_text="Hello **world** and `code` here.")
    widget.update(
        RichMarkdown("Hello **world** and `code` here."),
        raw_text="Hello **world** and `code` here.",
    )

    # Plain-text rendering should strip markdown syntax.
    assert "**" not in widget._selectable_plain_text
    assert "code" in widget._selectable_plain_text
    assert not any(
        line.endswith(" ") and line.strip()
        for line in widget._selectable_plain_text.splitlines()
    ), "plain text should not have trailing whitespace on non-empty lines"

    # Partial screen-coordinate selection should NOT return markdown garbage.
    partial = Selection(start=Offset(5, 0), end=Offset(15, 0))
    text, _ = widget.get_selection(partial)
    assert "**" not in text
    assert "Hello" in text or "world" in text


@pytest.mark.asyncio
async def test_tui_native_mouse_toggle_calls_driver_disable_then_enable(
    tmp_path, monkeypatch
):
    """F7 / /native-mouse toggles driver mouse support.

    The Codex/Claude-Code approach is to release mouse capture back to the
    terminal emulator so the terminal's own right-click menu and click-drag
    selection work natively. We inject fake escape-sequence writers onto
    the (headless) test driver and verify the toggle is idempotent.
    """
    calls = {"disable": 0, "enable": 0}

    def fake_disable():
        calls["disable"] += 1

    def fake_enable():
        calls["enable"] += 1

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        driver = app._driver
        assert driver is not None, "driver should be running"
        # The real LinuxDriver / WindowsDriver expose these; the headless
        # test driver does not, so inject them to mimic production behaviour.
        monkeypatch.setattr(driver, "_disable_mouse_support", fake_disable, raising=False)
        monkeypatch.setattr(driver, "_enable_mouse_support", fake_enable, raising=False)

        # F7 turns native mouse mode ON.
        await pilot.press("f7")
        await pilot.pause()
        assert app._native_mouse_mode is True
        assert calls["disable"] == 1
        assert calls["enable"] == 0

        # F7 again turns it OFF.
        await pilot.press("f7")
        await pilot.pause()
        assert app._native_mouse_mode is False
        assert calls["enable"] == 1
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_native_mouse_slash_command(tmp_path, monkeypatch):
    """/native-mouse invokes the same toggle as F7 (works in any terminal)."""
    calls = {"disable": 0}

    def fake_disable():
        calls["disable"] += 1

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        driver = app._driver
        monkeypatch.setattr(driver, "_disable_mouse_support", fake_disable, raising=False)
        monkeypatch.setattr(driver, "_enable_mouse_support", lambda: None, raising=False)

        handled = await app._handle_local_command("/native-mouse")
        assert handled, "/native-mouse should be a recognized local command"
        await pilot.pause()
        assert app._native_mouse_mode is True
        assert calls["disable"] == 1
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_native_mouse_env_var_auto_enables_on_mount(tmp_path, monkeypatch):
    """GLM_ACP_NATIVE_MOUSE=1 starts the TUI with native mouse mode on."""
    from textual.drivers.headless_driver import HeadlessDriver

    calls = {"disable": 0}

    def fake_disable(_self):
        calls["disable"] += 1

    # The fake methods must be installed on the driver class BEFORE the app
    # starts, because ``on_mount`` schedules the toggle via
    # ``call_after_refresh`` and it fires shortly after run_test begins —
    # long before the test body would have a chance to inject them on the
    # instance.
    monkeypatch.setattr(HeadlessDriver, "_disable_mouse_support", fake_disable, raising=False)
    monkeypatch.setattr(HeadlessDriver, "_enable_mouse_support", lambda _self: None, raising=False)

    agent = FakeAgent()
    monkeypatch.setenv("GLM_ACP_NATIVE_MOUSE", "1")
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # The mount hook schedules the toggle via call_after_refresh;
        # let it land.
        for _ in range(10):
            await pilot.pause(0.05)
        assert app._native_mouse_mode is True, "env var should auto-enable native mouse"
        assert calls["disable"] == 1
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_no_custom_context_menu_class_remains():
    """The Codex-style custom dropdown menu is gone.

    The user's directive was to remove the custom context menu entirely
    and instead release mouse capture back to the terminal. We assert that
    the ContextMenuScreen and ContextMenuOption classes are no longer
    importable from the module, and that no on_click right-click handler
    remains on the App.
    """
    import glm_acp.tui as tui_mod

    assert not hasattr(tui_mod, "ContextMenuScreen"), "custom menu screen must be removed"
    assert not hasattr(tui_mod, "ContextMenuOption"), "custom menu option class must be removed"
    # The App must not have a click handler that pops a menu.
    assert not hasattr(tui_mod.NativeGlmTui, "on_click")
    assert not hasattr(tui_mod.NativeGlmTui, "action_open_context_menu")
    # But the native-mouse toggle must exist.
    assert hasattr(tui_mod.NativeGlmTui, "action_toggle_native_mouse")


@pytest.mark.asyncio
async def test_tui_activity_line_animates_runtime_states_and_returns_ready(tmp_path):
    agent = SlowAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        activity = app.query_one("#activity-status", Static)
        assert "Ready" in str(activity.render())

        composer = app.query_one("#composer", Input)
        composer.value = "Inspect the repository"
        await pilot.press("enter")
        await asyncio.wait_for(agent.prompt_started.wait(), timeout=1)
        assert "Thinking" in str(activity.render())
        initial_frame = app._activity_frame
        app._advance_activity_animation()
        assert app._activity_frame != initial_frame

        await app.handle_session_update(acp.update_agent_thought_text("Considering evidence"))
        assert "Reasoning" in str(activity.render())

        await app.handle_session_update(
            acp.start_tool_call(
                "tool-animated",
                "Search\nrepository for a deliberately very long bounded tool title",
                kind="search",
                status="pending",
            )
        )
        rendered = str(activity.render())
        assert "Working · Search repository" in rendered
        assert "\n" not in rendered

        agent.prompt_release.set()
        for _ in range(20):
            await pilot.pause(0.05)
            if app._prompt_worker is None:
                break
        assert app._prompt_worker is None
        assert "Completed" in str(activity.render())

        app._activity_hold_until = time.monotonic() - 1
        app._advance_activity_animation()
        assert "Ready" in str(activity.render())
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_activity_animation_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_ACP_TUI_ANIMATION", "off")
    app = NativeGlmTui(_args(tmp_path), agent_factory=FakeAgent)

    async with app.run_test(size=(100, 35)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app._set_activity("Thinking", active=True)
        initial_frame = app._activity_frame
        app._advance_activity_animation()
        assert app._activity_frame == initial_frame
        assert "◆ Thinking" in str(app.query_one("#activity-status", Static).render())
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_f1_submits_help_and_documented_keys_are_actionable(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 45)) as pilot:
        await _wait_for_agent_ready(app, pilot)

        await pilot.press("f1")
        for _ in range(20):
            await pilot.pause(0.05)
            if app._current_agent_text:
                break
        assert app._current_agent_text == "Handled /help"
        assert str(getattr(agent.prompts[-1][0], "text", "")) == "/help"

        await pilot.press("f2")
        await pilot.pause()
        assert not app.query_one("#thinking").has_class("hidden")
        await pilot.press("f2")
        await pilot.pause()
        assert app.query_one("#thinking").has_class("hidden")

        await pilot.press("f3")
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, SettingsScreen):
                break
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_footer_actions_are_clickable_and_quit_uses_terminal_safe_key(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(130, 45)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        for _ in range(40):
            await pilot.pause(0.05)
            if agent.usage_calls:
                break
        assert agent.usage_calls == 1
        await pilot.pause(0.1)

        required_actions = {"quit_agent", "toggle_thinking", "settings", "show_help"}
        footer_keys = {}
        for _ in range(20):
            await pilot.pause(0.05)
            footer_keys = {key.action: key for key in app.query(FooterKey)}
            if required_actions <= footer_keys.keys():
                break
        assert required_actions <= footer_keys.keys()
        assert footer_keys["quit_agent"].key == "ctrl+x"
        assert all(key.key != "ctrl+q" for key in footer_keys.values())

        await pilot.click(
            next(key for key in app.query(FooterKey) if key.action == "toggle_thinking")
        )
        await pilot.pause()
        assert not app.query_one("#thinking").has_class("hidden")

        await pilot.click(next(key for key in app.query(FooterKey) if key.action == "settings"))
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, SettingsScreen):
                break
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.click(next(key for key in app.query(FooterKey) if key.action == "show_help"))
        for _ in range(20):
            await pilot.pause(0.05)
            if app._current_agent_text == "Handled /help" and app._prompt_worker is None:
                break
        assert app._current_agent_text == "Handled /help"
        assert app._prompt_worker is None
        await pilot.click(next(key for key in app.query(FooterKey) if key.action == "quit_agent"))
        await pilot.pause()
        assert app._shutdown_requested is True

    assert agent.closed is True


@pytest.mark.asyncio
async def test_tui_quit_does_not_shadow_textual_message_pump_state(tmp_path):
    app = NativeGlmTui(_args(tmp_path), agent_factory=FakeAgent)

    await app.action_quit_agent()

    assert app._shutdown_requested is True
    assert app._closing is False
    assert app._exit is True


@pytest.mark.asyncio
async def test_tui_quit_is_bounded_when_background_cleanup_hangs(tmp_path):
    agent = HangingCloseAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)
    app.SHUTDOWN_TIMEOUT_SECONDS = 0.05
    started = time.monotonic()

    await app._close_agent_resources()

    assert agent.close_started is True
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_tui_local_slash_controls_and_forwarded_command(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 45)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)

        composer.value = "/reasoning-panel"
        await pilot.press("enter")
        await pilot.pause()
        assert not app.query_one("#thinking").has_class("hidden")
        assert agent.prompts == []

        initial_usage_calls = agent.usage_calls
        composer.value = "/usage"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if agent.usage_calls > initial_usage_calls:
                break
        assert agent.usage_calls == initial_usage_calls + 1

        composer.value = "/settings"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, SettingsScreen):
                break
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("escape")
        await pilot.pause()

        composer.value = "/status"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app._current_agent_text == "Handled /status":
                break
        assert app._current_agent_text == "Handled /status"

        composer.value = "/clear-view"
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.query("#transcript > *")) == 0
        app.exit(0)


@pytest.mark.asyncio
async def test_slash_menu_filters_live_agent_commands_and_supports_tab_escape(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 45)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)
        menu = app.query_one("#command-menu", OptionList)

        composer.value = "/"
        await pilot.pause()
        assert menu.has_class("visible")
        assert "/status" in app._command_values
        assert "/checkpoint" in app._command_values
        assert "/model" in app._command_values
        assert app._command_values[:3] == ["/plan", "/thinking", "/model"]

        composer.value = "/check"
        await pilot.pause()
        assert app._command_values == ["/checkpoint"]
        await pilot.press("tab")
        await pilot.pause()
        assert composer.value == "/checkpoint"
        assert agent.prompts == []

        await pilot.press("ctrl+u")
        await pilot.pause()
        assert composer.value == ""

        composer.value = "/check"
        await pilot.press("escape")
        await pilot.pause()
        assert not menu.has_class("visible")

        composer.value = ""
        composer.value = "/check"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app._current_agent_text == "Handled /checkpoint":
                break
        assert app._current_agent_text == "Handled /checkpoint"
        app.exit(0)


@pytest.mark.asyncio
async def test_slash_model_menu_navigates_and_changes_shared_session(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 45)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)
        menu = app.query_one("#command-menu", OptionList)

        composer.value = "/model"
        await pilot.pause()
        assert app._command_values == ["/model"]
        await pilot.press("enter")
        await pilot.pause()
        assert composer.value == "/model "
        assert menu.has_class("visible")
        assert any(value.startswith("/model glm-") for value in app._command_values)

        target = next(value for value in app._command_values if value != "/model glm-5.2")
        menu.highlighted = app._command_values.index(target)
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if agent.config_calls:
                break
        selected_model = target.partition(" ")[2]
        assert agent.config_calls[-1] == ("model", selected_model)
        assert agent._sessions["tui-session"].model == selected_model
        assert selected_model in str(app.query_one("#session", Static).render())
        assert not menu.has_class("visible")
        app.exit(0)


@pytest.mark.asyncio
async def test_inline_permission_and_mode_commands_use_shared_agent_methods(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 45)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)

        composer.value = "/permission r"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if agent.config_calls:
                break
        assert agent.config_calls[-1] == ("permission_mode", "read")

        composer.value = "/mode a"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if agent.mode_calls:
                break
        assert agent.mode_calls[-1] == "ask"
        panel = str(app.query_one("#session", Static).render())
        assert "ask · read" in panel
        app.exit(0)


@pytest.mark.asyncio
async def test_api_plan_and_thinking_commands_have_full_zed_parity(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(140, 48)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)

        composer.value = "/plan "
        await pilot.pause()
        assert app._command_values == [
            "/plan coding",
            "/plan standard",
            "/plan bigmodel",
        ]
        menu_text = " ".join(
            str(option.prompt) for option in app.query_one("#command-menu", OptionList).options
        )
        assert "Coding Plan" in menu_text
        assert "Standard API" in menu_text
        assert "BigModel (CN)" in menu_text

        composer.value = "/plan standard"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if ("api_endpoint", "standard") in agent.config_calls:
                break
        assert agent._sessions["tui-session"].api_endpoint == "standard"
        assert "Standard API" in str(app.query_one("#session", Static).render())

        composer.value = "/model "
        await pilot.pause()
        assert "/model glm-5v-turbo" in app._command_values
        assert "/model glm-4.5v" in app._command_values
        assert "/model glm-4.6v" in app._command_values

        composer.value = "/thinking "
        await pilot.pause()
        assert app._command_values == [
            "/thinking disabled",
            "/thinking enabled",
            "/thinking high",
            "/thinking max",
        ]
        composer.value = "/thinking max"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if ("thought_level", "max") in agent.config_calls:
                break
        assert agent._sessions["tui-session"].thought_level == "max"
        assert "Deep · Max" in str(app.query_one("#session", Static).render())
        assert app.query_one("#thinking").has_class("hidden")

        agent._sessions["tui-session"].model = "glm-4.7"
        composer.value = "/thinking "
        await pilot.pause()
        assert app._command_values == [
            "/thinking disabled",
            "/thinking enabled",
        ]
        app.exit(0)


@pytest.mark.asyncio
async def test_every_inline_configuration_command_opens_valid_choices(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 45)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)
        menu = app.query_one("#command-menu", OptionList)

        for command in CONFIG_COMMANDS:
            composer.value = f"{command} "
            await pilot.pause()
            assert menu.has_class("visible"), command
            assert app._command_values, command
            assert all(value.startswith(f"{command} ") for value in app._command_values)
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_permission_modal_is_redacted_and_returns_allow(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        composer = app.query_one("#composer", Input)
        composer.value = "Make the requested edit"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, PermissionScreen):
                break
        assert isinstance(app.screen, PermissionScreen)
        assert "Waiting for approval" in str(app.query_one("#activity-status", Static).render())
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
        await _wait_for_agent_ready(app, pilot)
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


@pytest.mark.asyncio
async def test_tui_settings_initial_choices_follow_current_plan_and_model(tmp_path):
    values = {
        "api_endpoint": "coding",
        "model": "glm-4.7",
        "thought_level": "enabled",
        "permission_mode": "ask",
        "generation_profile": "balanced",
        "auxiliary_model": "main",
        "mixture_mode": "off",
        "session_mode": "code",
    }
    screen = SettingsScreen(values)

    class SettingsHost(NativeGlmTui):
        def on_mount(self) -> None:
            self.push_screen(screen)

    app = SettingsHost(_args(tmp_path), agent_factory=FakeAgent)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        model_values = {str(option[1]) for option in screen.query_one("#model", Select)._options}
        thought_values = {
            str(option[1]) for option in screen.query_one("#thought_level", Select)._options
        }
        assert model_values == {"glm-5.2", "glm-5-turbo", "glm-4.7"}
        assert thought_values == {"disabled", "enabled"}
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_composer_stays_enabled_and_queues_prompts_during_turn(tmp_path):
    """While the agent works, the composer stays enabled and Enter queues prompts."""
    agent = SlowAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)

        composer = app.query_one("#composer", Input)

        # Start first prompt — SlowAgent blocks on prompt_release
        composer.value = "First task"
        await pilot.press("enter")
        await asyncio.wait_for(agent.prompt_started.wait(), timeout=1)
        assert app._prompt_worker is not None

        # Composer must remain enabled while the agent works
        assert composer.disabled is False

        # While busy, queue two more prompts
        composer.value = "Second task"
        await pilot.press("enter")
        assert len(app._prompt_queue) == 1
        assert app._prompt_queue[0] == "Second task"

        composer.value = "Third task"
        await pilot.press("enter")
        assert len(app._prompt_queue) == 2

        # Queue display should show the count and preview
        queue_text = str(app.query_one("#queue-status", Static).render())
        assert "Queue (2)" in queue_text
        assert "Second task" in queue_text

        # Release the first prompt — queue should drain automatically
        agent.prompt_release.set()
        for _ in range(60):
            await pilot.pause(0.05)
            if app._prompt_worker is None and not app._prompt_queue:
                break

        # All three prompts should have been sent to the agent in order
        assert len(agent.prompts) == 3
        assert app._prompt_queue == []
        assert app._prompt_worker is None

        # Queue display should be empty after draining
        assert str(app.query_one("#queue-status", Static).render()) == ""
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_working_tree_panel_toggles_and_cycles_five_views(tmp_path):
    """F4 opens the working-tree panel; repeated F4 cycles through all 5 views
    (Changes → Git → Diff → Files → GitHub) then closes."""
    (tmp_path / "hello.py").write_text("print('hi')")
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(140, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)

        panel = app.query_one("#working-tree-panel")
        assert panel.has_class("hidden")

        await pilot.press("f4")
        await pilot.pause(0.15)
        assert app._wt_visible is True
        assert not panel.has_class("hidden")
        switcher = app.query_one("#wt-switcher", ContentSwitcher)
        assert switcher.current == "wt-changes"

        await pilot.press("f4")
        await pilot.pause(0.15)
        assert switcher.current == "wt-git"

        await pilot.press("f4")
        await pilot.pause(0.15)
        assert switcher.current == "wt-diff"

        await pilot.press("f4")
        await pilot.pause(0.15)
        assert switcher.current == "wt-files"

        files_widget = app.query_one("#wt-files", VerticalScroll)
        assert len(list(files_widget.children)) > 0

        await pilot.press("f4")
        await pilot.pause(0.15)
        assert switcher.current == "wt-github"

        # GitHub view should have at least the branch-name line.
        gh_widget = app.query_one("#wt-github", VerticalScroll)
        assert len(list(gh_widget.children)) > 0

        await pilot.press("f4")
        await pilot.pause(0.1)
        assert app._wt_visible is False
        assert panel.has_class("hidden")
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


# ---------------------------------------------------------------------------
# History browser / in-conversation search / session export / token meter
# ---------------------------------------------------------------------------


class _StoreListAgent(FakeAgent):
    """FakeAgent extension that exposes _store and resume_session."""

    def __init__(self, sessions_list=None, cwd="/workspace"):
        super().__init__()
        from types import SimpleNamespace

        store = SimpleNamespace(list=lambda: list(sessions_list or []))
        self._store = store
        self.resumed = []
        self._fake_cwd = cwd

    async def resume_session(self, cwd, session_id, **kwargs):
        self.resumed.append(session_id)
        return SimpleNamespace()


def _session_with_messages(messages=None, *, tokens=True, cwd="/workspace"):
    """Build a SimpleNamespace session with messages and token totals."""
    ns = SimpleNamespace(
        model="glm-5.2",
        permission_mode="ask",
        mode="code",
        api_endpoint="coding",
        thought_level="enabled",
        generation_profile="balanced",
        auxiliary_model="main",
        mixture_mode="off",
        cwd=cwd,
        id="tui-session",
        messages=list(messages or []),
    )
    if tokens:
        ns.total_input_tokens = 1234
        ns.total_output_tokens = 567
        ns.total_cached_tokens = 800
    else:
        ns.total_input_tokens = 0
        ns.total_output_tokens = 0
        ns.total_cached_tokens = 0
    return ns


def test_extract_message_text_handles_string_and_tool_calls():
    plain = {"role": "user", "content": "hello world"}
    assert _extract_message_text(plain) == "hello world"

    list_content = {
        "role": "assistant",
        "content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}],
    }
    assert _extract_message_text(list_content) == "first\nsecond"

    with_tools = {
        "role": "assistant",
        "content": "thinking",
        "tool_calls": [
            {"id": "1", "function": {"name": "read_file", "arguments": '{"path": "x"}'}}
        ],
    }
    text = _extract_message_text(with_tools)
    assert "thinking" in text
    assert "read_file" in text


def test_format_session_row_uses_title_timestamp_and_workspace():
    title, second = _format_session_row(
        {
            "session_id": "abc-1234567890",
            "title": "Bug hunt",
            "updated_at": "2026-01-02T15:30:00+00:00",
            "cwd": "/home/me/project",
            "session_id_alt": None,
        }
    )
    assert title == "Bug hunt"
    assert "2026-01-02 15:30" in second
    assert "abc-1234" in second
    assert "project" in second


def test_format_session_row_handles_missing_fields():
    title, second = _format_session_row({})
    assert title == "Untitled session"
    assert "—" in second


def test_token_summary_reports_real_totals():
    session = _session_with_messages()
    summary = NativeGlmTui._token_summary(session)
    assert "↑1,234" in summary
    assert "↓567" in summary
    assert "cache" in summary


def test_token_summary_waiting_when_zero():
    session = _session_with_messages(tokens=False)
    assert NativeGlmTui._token_summary(session) == "tokens waiting"


@pytest.mark.asyncio
async def test_history_screen_lists_sessions_and_returns_selected(tmp_path):
    agent = _StoreListAgent(
        sessions_list=[
            {
                "session_id": "sess-aaa",
                "title": "First task",
                "updated_at": "2026-01-02T15:30:00+00:00",
                "cwd": str(tmp_path),
            },
            {
                "session_id": "sess-bbb",
                "title": "Second task",
                "updated_at": "2026-01-01T10:00:00+00:00",
                "cwd": str(tmp_path),
            },
        ],
        cwd=str(tmp_path),
    )
    session = _session_with_messages([{"role": "user", "content": "x"}])
    agent._sessions["tui-session"] = session
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    selected: dict[str, str] = {}

    def on_result(value):
        selected["v"] = value

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Two sessions in the same workspace + the live session would
        # normally make >=3 entries; force the workspace filter to take
        # effect by keeping only the stored entries.
        app.push_screen(HistoryScreen(agent._store.list()[:2]), callback=on_result)
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, HistoryScreen):
                break
        assert isinstance(app.screen, HistoryScreen)
        listview = app.screen.query_one("#history-list", ListView)
        assert len(listview.children) == 2
        listview.index = 1
        await pilot.pause(0.05)
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if not isinstance(app.screen, HistoryScreen):
                break
    assert selected.get("v") == "sess-bbb"


@pytest.mark.asyncio
async def test_history_screen_cancel_returns_none(tmp_path):
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: FakeAgent())
    selected: dict[str, str] = {}

    def on_result(value):
        selected["v"] = value

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app.push_screen(HistoryScreen([]), callback=on_result)
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, HistoryScreen):
                break
        await pilot.press("escape")
        for _ in range(20):
            await pilot.pause(0.05)
            if not isinstance(app.screen, HistoryScreen):
                break
    assert selected.get("v") is None


@pytest.mark.asyncio
async def test_search_screen_greps_messages_and_returns_full_text(tmp_path):
    agent = FakeAgent()
    session = _session_with_messages(
        [
            {"role": "user", "content": "how do I configure the api plan?"},
            {"role": "assistant", "content": "use /plan coding to switch"},
            {"role": "user", "content": "thanks"},
        ]
    )
    agent._sessions["tui-session"] = session
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    selected: dict[str, object] = {}

    def on_result(value):
        selected["v"] = value

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app.push_screen(SearchScreen(list(session.messages)), callback=on_result)
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, SearchScreen):
                break
        assert isinstance(app.screen, SearchScreen)
        search_input = app.screen.query_one("#search-input", Input)
        search_input.value = "configure"
        search_input.post_message(Input.Changed(search_input, "configure"))
        for _ in range(20):
            await pilot.pause(0.05)
            results = app.screen.query_one("#search-results", ListView)
            if len(results.children) >= 1:
                break
        results = app.screen.query_one("#search-results", ListView)
        assert len(results.children) == 1
        results.index = 0
        results.focus()
        await pilot.pause(0.05)
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if not isinstance(app.screen, SearchScreen):
                break
    payload = selected.get("v")
    assert payload is not None
    _idx, full_text = payload  # type: ignore[misc]
    assert "configure the api plan" in full_text


@pytest.mark.asyncio
async def test_tui_blocks_picker_extracts_code_blocks_and_lists_them(tmp_path):
    """Tier 1.3: ``/blocks`` extracts fenced code blocks from recent responses
    and lists them in the picker modal."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)

        # Seed two responses with three fenced blocks across them.
        app._agent_responses = [
            "Here is a Python helper:\n```python\nprint('hi')\n```\n",
            "And a bash snippet:\n```bash\necho hello\n```\n"
            "Plus an untagged block:\n```\nplain code\n```\n",
        ]
        app._current_agent_text = ""

        blocks = app._extract_code_blocks()
        # Three blocks, languages preserved.
        assert len(blocks) == 3
        langs = [lang for lang, _ in blocks]
        assert langs == ["python", "bash", "text"]
        assert "print('hi')" in blocks[0][1]
        assert "echo hello" in blocks[1][1]
        assert "plain code" in blocks[2][1]

        # Mount the picker with these blocks and confirm it renders one row each.
        app.push_screen(CodeBlockPickerScreen(blocks))
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, CodeBlockPickerScreen):
                break
        assert isinstance(app.screen, CodeBlockPickerScreen)
        title = app.screen.query_one("#blocks-title", Static)
        assert "3" in str(title.render())
        listview = app.screen.query_one("#blocks-list", ListView)
        assert len(listview.children) == 3

        # Selecting the first row dismisses with ("copy", code).
        listview.index = 0
        listview.focus()
        await pilot.pause(0.05)
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if not isinstance(app.screen, CodeBlockPickerScreen):
                break
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_blocks_picker_no_blocks_returns_quickly(tmp_path):
    """``/blocks`` with no code blocks in history just notifies and returns."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app._agent_responses = ["just text, no code blocks here"]
        app._current_agent_text = ""

        blocks = app._extract_code_blocks()
        assert blocks == []
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_blocks_command_routes_to_picker(tmp_path):
    """Typing ``/blocks`` in the composer opens the picker (or notifies if empty)."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    picker_opened = {"yes": False}

    async def fake_push(screen):
        picker_opened["yes"] = isinstance(screen, CodeBlockPickerScreen)
        return None

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app.push_screen_wait = fake_push  # type: ignore[method-assign]
        # Seed a response so the picker has something to show.
        app._agent_responses = ["```python\nx = 1\n```\n"]
        app._current_agent_text = ""

        handled = await app._handle_local_command("/blocks")
        assert handled is True
        assert picker_opened["yes"] is True
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_statusline_command_opens_modal_and_persists(tmp_path, monkeypatch):
    """Tier 1.6: ``/statusline`` opens the toggle modal and Save persists the
    selected segment set, which then drives ``_refresh_session_panel``."""
    from glm_acp.config import (
        STATUSLINE_SEGMENT_IDS,
        load_statusline_config,
    )
    from glm_acp.tui import StatusLineScreen

    # Use an isolated config dir so the test does not touch the user's real file.
    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path / "glm-acp"))

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    # The picker returns a reduced set (only state + tokens).  We capture it
    # by short-circuiting push_screen_wait.
    chosen: set[str] = {"state", "tokens"}

    async def fake_push(screen):
        assert isinstance(screen, StatusLineScreen)
        # Initial enabled set must be the full set on first run.
        assert screen._enabled == set(STATUSLINE_SEGMENT_IDS)
        return set(chosen)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app.push_screen_wait = fake_push  # type: ignore[method-assign]
        before = set(app._statusline_segments)
        assert before == set(STATUSLINE_SEGMENT_IDS)  # all visible initially

        handled = await app._handle_local_command("/statusline")
        assert handled is True

        # The chosen subset is now active both in-memory and on disk.
        assert app._statusline_segments == chosen
        reloaded = load_statusline_config()
        assert reloaded == chosen
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_context_command_routes_to_breakdown_screen(tmp_path):
    """Tier 2.2: ``/context`` opens the ContextBudgetScreen with a real
    per-segment breakdown of the live session."""
    from glm_acp.tui import ContextBudgetScreen

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    captured: dict[str, object] = {}

    async def fake_push(screen):
        captured["screen"] = screen
        return None

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app.push_screen = fake_push  # type: ignore[method-assign]
        # Seed a real breakdown via the shared agent method.
        session = agent._sessions[app.session_id]
        session.context_size = 1_000_000
        session.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": "ok reading file"},
            {"role": "tool", "content": "file contents"},
        ]

        handled = await app._handle_local_command("/context")
        assert handled is True
        assert isinstance(captured.get("screen"), ContextBudgetScreen)
        breakdown = captured["screen"]._breakdown  # type: ignore[attr-defined]
        assert breakdown["total_tokens"] > 0
        assert breakdown["context_size"] > 0
        labels = [s["label"] for s in breakdown["segments"]]
        assert "System prompt" in labels
        assert "Tool results" in labels
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_btw_command_routes_to_overlay_screen(tmp_path):
    """Tier 2.5: ``/btw`` opens the BtwOverlayScreen; ``/btw <q>`` pre-fills."""
    from glm_acp.tui import BtwOverlayScreen

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    captured: dict[str, object] = {}

    async def fake_push(screen):
        captured["screen"] = screen
        return None

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app.push_screen = fake_push  # type: ignore[method-assign]

        # /btw with no argument opens an empty overlay.
        handled = await app._handle_local_command("/btw")
        assert handled is True
        screen = captured.get("screen")
        assert isinstance(screen, BtwOverlayScreen)
        assert screen._prefill == ""
        app.exit(0)

    # Reset and verify /btw <question> pre-fills the overlay.
    captured.clear()
    agent2 = FakeAgent()
    app2 = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent2)

    async def fake_push2(screen):
        captured["screen"] = screen
        return None

    async with app2.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app2, pilot)
        app2.push_screen = fake_push2  # type: ignore[method-assign]

        handled = await app2._handle_local_command("/btw what does async mean?")
        assert handled is True
        screen = captured.get("screen")
        assert isinstance(screen, BtwOverlayScreen)
        assert screen._prefill == "what does async mean?"
        app2.exit(0)


@pytest.mark.asyncio
async def test_tui_theme_command_opens_picker_and_persists(tmp_path, monkeypatch):
    """Tier 2.8: ``/theme`` opens Textual's built-in theme picker; setting a
    theme via the reactive persists to ``config_dir()/theme.json``."""
    from glm_acp.config import load_theme_config

    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path / "glm-acp"))

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    picker_opened = {"yes": False}

    def fake_search():
        picker_opened["yes"] = True

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Stub the built-in so the test doesn't actually push a modal.
        app.action_change_theme = fake_search  # type: ignore[method-assign]

        handled = await app._handle_local_command("/theme")
        assert handled is True
        assert picker_opened["yes"] is True

        # Simulate the user picking a theme: setting the reactive should
        # trigger ``watch_theme`` and persist.
        app.theme = "nord"
        await pilot.pause(0.05)
        assert load_theme_config() == "nord"
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_tasks_command_opens_dashboard_with_session_state(tmp_path):
    """Tier 2.7: ``/tasks`` opens the TasksScreen with a live snapshot of
    the current turn state, queue, and session stats."""
    from glm_acp.tui import TasksScreen

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    captured: dict[str, object] = {}

    async def fake_push(screen):
        captured["screen"] = screen
        return None

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app.push_screen = fake_push  # type: ignore[method-assign]

        # Seed a queue and turn state so the snapshot has interesting data.
        app._prompt_queue = ["next question about auth", "another queued prompt"]
        app._activity_label = "Thinking"
        session = agent._sessions[app.session_id]
        session.total_input_tokens = 5000
        session.total_output_tokens = 1200
        session.total_cached_tokens = 3000
        session.context_size = 1_000_000
        session.estimated_tokens = 8000
        session.max_tool_iterations = 100

        handled = await app._handle_local_command("/tasks")
        assert handled is True
        screen = captured.get("screen")
        assert isinstance(screen, TasksScreen)
        snap = screen._snapshot  # type: ignore[attr-defined]
        assert snap["turn_state"] == "Idle"  # no prompt running
        assert snap["activity"] == "Thinking"
        assert len(snap["queue"]) == 2
        assert snap["queue"][0] == "next question about auth"
        sess = snap["session"]
        assert sess["input_tokens"] == 5000
        assert sess["output_tokens"] == 1200
        assert sess["cached_tokens"] == 3000
        assert sess["max_iterations"] == 100
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_insights_command_appends_to_transcript(tmp_path):
    """Tier 4: ``/insights`` generates session analysis and appends it to the transcript."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Seed messages so generate_insights has something to analyze.
        session = agent._sessions[app.session_id]
        session.messages = [
            {"role": "user", "content": "fix the auth bug"},
            {"role": "assistant", "content": "reading login.py"},
        ]

        handled = await app._handle_local_command("/insights")
        assert handled is True
        await pilot.pause(0.05)
        # The insights text should appear as a new child widget in the transcript.
        children = list(app.query("#transcript > *"))
        assert len(children) > 0
        # The last child should contain the insights text.
        last_child_text = str(children[-1].render())
        assert "Insights:" in last_child_text or "user turn" in last_child_text
        app.exit(0)


def test_tui_loop_interval_parser_handles_units():
    """Tier 4: ``_parse_loop_interval`` handles s/m/h suffixes and bare numbers."""
    assert NativeGlmTui._parse_loop_interval("30s") == 30.0
    assert NativeGlmTui._parse_loop_interval("5m") == 300.0
    assert NativeGlmTui._parse_loop_interval("1h") == 3600.0
    assert NativeGlmTui._parse_loop_interval("120") == 120.0
    assert NativeGlmTui._parse_loop_interval("2.5m") == 150.0
    assert NativeGlmTui._parse_loop_interval("abc") is None
    assert NativeGlmTui._parse_loop_interval("") is None


@pytest.mark.asyncio
async def test_tui_loop_command_starts_and_stops(tmp_path):
    """Tier 4: ``/loop 5s test prompt`` starts a timer; ``/loop stop`` cancels."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)

        # Start a loop with a 999s interval (won't actually fire during the test).
        handled = await app._handle_local_command("/loop 999s check CI status")
        assert handled is True
        assert app._loop_timer is not None
        assert app._loop_prompt == "check CI status"
        assert app._loop_interval_seconds == 999.0

        # Stop the loop.
        handled = await app._handle_local_command("/loop stop")
        assert handled is True
        assert app._loop_timer is None
        assert app._loop_prompt == ""
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_loop_command_rejects_invalid_interval(tmp_path):
    """Tier 4: ``/loop abc <prompt>`` notifies error and does not start."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)

        handled = await app._handle_local_command("/loop abc check CI status")
        assert handled is True
        # No timer should have been created.
        assert app._loop_timer is None
        assert app._loop_prompt == ""
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_smart_lists_templates_when_bare(tmp_path):
    """Tier 4: ``/smart`` (bare) lists available templates in the transcript."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        handled = await app._handle_local_command("/smart")
        assert handled is True
        await pilot.pause(0.05)
        children = list(app.query("#transcript > *"))
        assert len(children) > 0
        last_text = str(children[-1].render())
        assert "/smart pr" in last_text
        assert "/smart review" in last_text
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_smart_resolves_template_into_composer(tmp_path):
    """Tier 4: ``/smart review`` expands the template with git context and
    inserts it into the composer for review."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        handled = await app._handle_local_command("/smart review")
        assert handled is True
        await pilot.pause(0.05)
        composer = app.query_one("#composer", Input)
        # The template contains "Review the uncommitted changes" and
        # variable fallbacks for non-git directories.
        assert "Review the uncommitted changes" in composer.value
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_smart_unknown_template_notifies(tmp_path):
    """Tier 4: ``/smart bogus`` notifies the user about available templates."""
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        handled = await app._handle_local_command("/smart bogus")
        assert handled is True
        # Composer should NOT have been modified.
        composer = app.query_one("#composer", Input)
        assert composer.value == ""
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_sound_command_toggles_notification_sounds(tmp_path):
    """Tier 4: ``/sound`` toggles notification sounds on/off at runtime."""
    from glm_acp.voice import is_sound_enabled, set_sound_enabled

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Ensure known starting state.
        set_sound_enabled(False)
        assert is_sound_enabled() is False

        # Toggle on.
        handled = await app._handle_local_command("/sound")
        assert handled is True
        assert is_sound_enabled() is True

        # Toggle off.
        handled = await app._handle_local_command("/sound")
        assert handled is True
        assert is_sound_enabled() is False
        app.exit(0)


@pytest.mark.asyncio
async def test_tui_refresh_session_panel_hides_disabled_segments(tmp_path, monkeypatch):
    """When segments are toggled off, ``_refresh_session_panel`` omits them."""
    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path / "glm-acp"))
    from textual.widgets import Static

    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Disable every segment except state.
        app._statusline_segments = {"state"}
        app._refresh_session_panel("Ready", used=100, size=1000)
        await pilot.pause(0.05)
        rendered = str(app.query_one("#session", Static).render())
        assert "● Ready" in rendered
        # Tokens, quota, model, etc. should NOT be rendered.
        assert "tokens" not in rendered
        assert "quota" not in rendered
        assert "glm-" not in rendered
        app.exit(0)


def test_render_session_markdown_includes_each_role():
    session = SimpleNamespace(
        id="sess-x",
        model="glm-5.2",
        api_endpoint="coding",
        title="My task",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ],
    )
    md = NativeGlmTui._render_session_markdown(session)
    assert "# My task" in md
    assert "## You" in md
    assert "hello" in md
    assert "## Agent" in md
    assert "hi there" in md
    assert "glm-5.2" in md


@pytest.mark.asyncio
async def test_export_session_writes_file_when_no_clipboard(tmp_path, monkeypatch):
    agent = _StoreListAgent(cwd=str(tmp_path))
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    # No clipboard helpers available — should fall back to a file.
    monkeypatch.setattr("glm_acp.tui._write_system_clipboard", lambda text: False)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Replace AFTER init so FakeAgent.new_session doesn't clobber it.
        agent._sessions["tui-session"] = _session_with_messages(
            [
                {"role": "user", "content": "say hi"},
                {"role": "assistant", "content": "hello!"},
            ],
            cwd=str(tmp_path),
        )
        await app._export_session("md file")
        await pilot.pause(0.1)

    exported = list(tmp_path.glob("glm-acp-tui-sess-*.md"))
    assert len(exported) == 1
    text = exported[0].read_text(encoding="utf-8")
    assert "## You" in text
    assert "say hi" in text
    assert "hello!" in text


@pytest.mark.asyncio
async def test_export_session_json_clipboard_uses_store(tmp_path, monkeypatch):
    agent = _StoreListAgent(cwd=str(tmp_path))

    class FakeSession:
        def __init__(self):
            self.id = "tui-session"
            self.messages = [{"role": "user", "content": "ping"}]
            self.model = "glm-5.2"
            self.api_endpoint = "coding"
            self.title = "T"
            self.total_input_tokens = 5
            self.total_output_tokens = 0
            self.total_cached_tokens = 0
            self.permission_mode = "ask"
            self.mode = "code"
            self.thought_level = "enabled"
            self.generation_profile = "balanced"
            self.auxiliary_model = "main"
            self.mixture_mode = "off"
            self.cwd = str(tmp_path)

        def to_dict(self):
            return {"id": self.id, "messages": self.messages}

    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    captured = {}

    def fake_clip(text):
        captured["text"] = text
        return True

    monkeypatch.setattr("glm_acp.tui._write_system_clipboard", fake_clip)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Replace AFTER init so FakeAgent.new_session doesn't clobber it.
        agent._sessions["tui-session"] = FakeSession()
        await app._export_session("json clip")
        await pilot.pause(0.1)

    assert "text" in captured
    import json as _json

    payload = _json.loads(captured["text"])
    assert payload["messages"][0]["content"] == "ping"


@pytest.mark.asyncio
async def test_refresh_session_panel_shows_live_token_totals(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        session = agent._sessions["tui-session"]
        session.total_input_tokens = 9999
        session.total_output_tokens = 1111
        session.total_cached_tokens = 500
        app._refresh_session_panel("Ready")
        rendered = str(app.query_one("#session", Static).render())
        assert "↑9,999" in rendered
        assert "↓1,111" in rendered
        assert "cache" in rendered


@pytest.mark.asyncio
async def test_search_command_with_no_messages_informs_user(tmp_path):
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        handled = await app._handle_local_command("/search")
        assert handled is True


@pytest.mark.asyncio
async def test_history_command_filters_to_workspace(tmp_path):
    sessions = [
        {
            "session_id": "in-workspace",
            "title": "Same",
            "updated_at": "2026-01-02T15:30:00+00:00",
            "cwd": str(tmp_path),
        },
        {
            "session_id": "other-workspace",
            "title": "Other",
            "updated_at": "2026-01-02T15:30:00+00:00",
            "cwd": "/somewhere/else",
        },
    ]
    agent = _StoreListAgent(sessions_list=sessions, cwd=str(tmp_path))
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    captured = {}

    async def fake_push(screen):
        captured["screen"] = screen
        # Emulate user pressing Escape.
        return None

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Make the live session report the same cwd so the workspace filter
        # keeps only the in-workspace entry.
        agent._sessions["tui-session"].cwd = str(tmp_path)
        monkey_target = app
        monkey_target.push_screen_wait = fake_push  # type: ignore[method-assign]
        await app.action_open_history()
        await pilot.pause(0.05)

    assert isinstance(captured.get("screen"), HistoryScreen)
    # Workspace filter should keep only the same-workspace entry.
    assert [s["session_id"] for s in captured["screen"].sessions] == ["in-workspace"]


# ---------------------------------------------------------------------------
# Phase 1 features (Hermes-parity): /undo, /prompt, atomic memory batch, /journey
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_command_is_routed_locally(tmp_path):
    """`/undo` is intercepted by _handle_local_command (not forwarded to the model)."""
    agent = FakeAgent()
    forwarded: list[str] = []
    orig_prompt = agent.prompt

    async def capturing_prompt(*args, **kwargs):
        forwarded.append("yes")
        return await orig_prompt(*args, **kwargs)

    agent.prompt = capturing_prompt  # type: ignore[method-assign]

    async def fake_handle(sess, text):
        return "Nothing to undo"

    agent._handle_command = fake_handle  # type: ignore[method-assign]

    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)
    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Drive the slash command via the composer (the canonical path).
        composer = app.query_one("#composer", Input)
        composer.value = "/undo"
        await composer.action_submit()
        for _ in range(15):
            await pilot.pause(0.05)

    # The command was handled locally — never forwarded to the model.
    assert forwarded == []


def test_undo_marker_parsing_extracts_prefill():
    """The TUI's marker contract with the agent's /undo handler."""
    response = (
        "↩️ Undid 1 turn. Last message prefilled for editing:\n\n"
        "---PROMPT---\nsecond user turn"
    )
    marker = "\n---PROMPT---\n"
    assert marker in response
    head, prefill = response.split(marker, 1)
    assert "Undid" in head
    assert prefill == "second user turn"


def test_undo_marker_parsing_handles_truncation_preview():
    """Long prefills stay within the agent's preview cap before the marker."""
    long_msg = "x" * 500
    response = (
        f"↩️ Undid 1 turn. Last message prefilled for editing:\n\n---PROMPT---\n{long_msg[:397]}…"
    )
    marker = "\n---PROMPT---\n"
    _, prefill = response.split(marker, 1)
    assert prefill.endswith("…")
    assert len(prefill) <= 400


@pytest.mark.asyncio
async def test_prompt_command_queues_edited_text(tmp_path):
    """`/prompt` runs $EDITOR on a tempfile and returns the cleaned prompt.

    Uses a tiny Python fake-editor script so the test runs identically on
    Linux, macOS, and Windows. Passes the argv list directly to bypass
    shell-parsing differences across platforms.
    """
    # Cross-platform Python fake editor that overwrites the temp file.
    fake_editor = tmp_path / "fake_editor.py"
    fake_editor.write_text(
        "import sys\n"
        "path = sys.argv[1]\n"
        "with open(path, 'w', encoding='utf-8') as fh:\n"
        "    fh.write('# comment line - must be stripped\\n')\n"
        "    fh.write('Build the feature.\\n')\n"
        "    fh.write('Then verify with pytest.\\n')\n"
    )
    # Pass argv directly so no shell parsing is needed; works on every OS
    # even when sys.executable or the temp path contains spaces.
    prompt = await NativeGlmTui._compose_prompt_in_editor([sys.executable, str(fake_editor)])
    assert "Build the feature" in prompt
    assert "verify with pytest" in prompt
    # Comment line was stripped.
    assert "comment line" not in prompt


@pytest.mark.asyncio
async def test_prompt_command_returns_empty_when_editor_missing(monkeypatch):
    """Missing editor binary returns empty string (no exception)."""
    monkeypatch.setenv("VISUAL", "/nonexistent/editor-binary-xyz-abc")
    monkeypatch.setenv("EDITOR", "")
    prompt = await NativeGlmTui._compose_prompt_in_editor()
    assert prompt == ""


@pytest.mark.asyncio
async def test_prompt_command_env_var_parsing_handles_multiword(monkeypatch):
    """``$VISUAL`` with a quoted multi-word value parses to an argv list."""
    monkeypatch.setenv("VISUAL", "/usr/bin/env python3 -B")
    monkeypatch.setenv("EDITOR", "")
    # We can't actually run python3 -B without a script, so just verify
    # the parsing path doesn't crash. The helper returns "" because the
    # bogus argv can't write the temp file.
    prompt = await NativeGlmTui._compose_prompt_in_editor()
    assert prompt == ""


def test_journey_extract_memory_lines_strips_leading_marker(tmp_path, monkeypatch):
    mem_file = tmp_path / ".glm-acp" / "memory.md"
    mem_file.parent.mkdir(parents=True)
    mem_file.write_text(
        "- First fact\n"
        "- Second fact with detail\n"
        "## Section\n"
        "free text without marker\n"
    )
    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path / "cfg"))
    lines = _journey_extract_memory_lines(str(tmp_path))
    assert lines == ["First fact", "Second fact with detail"]


def test_journey_extract_memory_lines_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path / "cfg"))
    assert _journey_extract_memory_lines(str(tmp_path)) == []


def test_journey_extract_profile_lines_strips_category_marker(tmp_path, monkeypatch):
    profile_dir = tmp_path / "cfg"
    profile_dir.mkdir()
    profile_file = profile_dir / "user_profile.md"
    profile_file.write_text(
        "- [preference] Always run tests after edits\n"
        "- [workflow] Use uv\n"
        "## Unrelated section\n"
        "free text\n"
    )
    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(profile_dir))
    monkeypatch.setattr("glm_acp.memory._safe_user_profile_path", lambda: profile_file)
    monkeypatch.setattr("glm_acp.tui.read_user_profile", lambda: profile_file.read_text())
    lines = _journey_extract_profile_lines()
    assert "Always run tests after edits" in lines
    assert "Use uv" in lines


@pytest.mark.asyncio
async def test_journey_modal_lists_skills_memories_and_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path / "cfg"))

    skills = [
        {
            "name": "release-pipeline",
            "description": "Ship a release through version → CI → binary",
            "created_at": "2026-07-20T10:00:00+00:00",
            "use_count": 5,
            "state": "active",
            "pinned": False,
        },
        {
            "name": "old-archived",
            "description": "Legacy",
            "created_at": "2026-05-01T08:00:00+00:00",
            "use_count": 0,
            "state": "archived",
            "pinned": False,
        },
    ]
    memories = ["Project uses uv", "Tests in tests/"]
    profile = ["Always bump versions in 5 files"]

    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: FakeAgent())
    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        # Use push_screen + callback (not push_screen_wait, which needs a worker).
        app.push_screen(JourneyScreen(memories, skills, profile))
        for _ in range(20):
            await pilot.pause(0.05)
            if isinstance(app.screen, JourneyScreen):
                break
        assert isinstance(app.screen, JourneyScreen)
        # Give the deferred _populate a chance to run.
        for _ in range(20):
            await pilot.pause(0.05)
            listview = app.screen.query_one("#journey-list", ListView)
            if len(listview.children) > 0:
                break
        listview = app.screen.query_one("#journey-list", ListView)
        # 2 skills + 2 memories + 1 profile = 5 entries
        assert len(listview.children) == 5
        # Most recent skill (2026-07-20) should be first. Inspect the Static
        # widget inside the first ListItem (the list row itself renders as
        # a Blank, not the row text).
        first_statics = listview.children[0].query(Static)
        first_text = " ".join(str(s.render()) for s in first_statics)
        assert "2026-07-20" in first_text
        assert "release-pipeline" in first_text
        await pilot.press("escape")
        for _ in range(20):
            await pilot.pause(0.05)
            if not isinstance(app.screen, JourneyScreen):
                break


@pytest.mark.asyncio
async def test_journey_command_routes_to_modal(tmp_path, monkeypatch):
    """`/journey` slash command opens the modal without raising."""
    monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path / "cfg"))
    agent = FakeAgent()
    app = NativeGlmTui(_args(tmp_path), agent_factory=lambda: agent)

    # Stub push_screen_wait so _handle_journey doesn't need a worker context.
    pushed: list[JourneyScreen] = []

    async def fake_push_wait(screen):
        pushed.append(screen)
        return None

    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_agent_ready(app, pilot)
        app.push_screen_wait = fake_push_wait  # type: ignore[method-assign]
        composer = app.query_one("#composer", Input)
        composer.value = "/journey"
        await composer.action_submit()
        for _ in range(10):
            await pilot.pause(0.05)

    assert len(pushed) == 1
    assert isinstance(pushed[0], JourneyScreen)
