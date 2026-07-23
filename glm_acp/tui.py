"""Full-screen Textual frontend for the shared Native GLM ACP runtime."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse
from rich.markup import escape
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    RichLog,
    Select,
    Static,
)
from textual.worker import Worker

from .agent import GlmAcpAgent
from .config import (
    API_ENDPOINTS,
    DEFAULT_AUXILIARY_MODEL,
    DEFAULT_MODEL,
    GENERATION_PROFILES,
    MODELS,
    THOUGHT_LEVELS,
    VISION_MODELS,
    thought_levels_for_model,
)


class PermissionScreen(ModalScreen[bool]):
    """Fail-closed approval dialog for a single tool invocation."""

    BINDINGS = [
        Binding("y", "allow", "Allow", priority=True),
        Binding("n", "deny", "Deny", priority=True),
        Binding("escape", "deny", "Deny", priority=True),
    ]

    CSS = """
    PermissionScreen { align: center middle; background: $background 70%; }
    #permission-dialog {
        width: 76; max-width: 95%; height: auto; max-height: 80%;
        border: thick $warning; background: $surface; padding: 1 2;
    }
    #permission-title { text-style: bold; color: $warning; margin-bottom: 1; }
    #permission-detail { max-height: 18; overflow-y: auto; margin-bottom: 1; }
    #permission-buttons { height: 3; align-horizontal: right; }
    #permission-buttons Button { margin-left: 1; }
    """

    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        self.title = title
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="permission-dialog"):
            yield Label("Tool permission required", id="permission-title")
            yield Static(f"{self.title}{self.detail}", id="permission-detail", markup=False)
            with Horizontal(id="permission-buttons"):
                yield Button("Deny [N]", id="deny", variant="error")
                yield Button("Allow once [Y]", id="allow", variant="success")

    @on(Button.Pressed, "#allow")
    def allow_button(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#deny")
    def deny_button(self) -> None:
        self.dismiss(False)

    def action_allow(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


class SettingsScreen(ModalScreen[dict[str, str] | None]):
    """Runtime settings equivalent to the ACP client configuration controls."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]
    FIELD_IDS = (
        "api_endpoint",
        "model",
        "thought_level",
        "permission_mode",
        "generation_profile",
        "auxiliary_model",
        "mixture_mode",
        "session_mode",
    )

    CSS = """
    SettingsScreen { align: center middle; background: $background 70%; }
    #settings-dialog {
        width: 78; max-width: 96%; height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #settings-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #settings-fields { height: 1fr; }
    .settings-label { margin-top: 1; color: $text-muted; }
    .settings-select { width: 100%; }
    #settings-buttons { height: 3; align-horizontal: right; margin-top: 1; }
    #settings-buttons Button { margin-left: 1; }
    """

    def __init__(self, values: dict[str, str]) -> None:
        super().__init__()
        self.values = values

    @staticmethod
    def _options(mapping: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
        return [(str(info.get("name", key)), key) for key, info in mapping.items()]

    def compose(self) -> ComposeResult:
        auxiliary = [("Main model", DEFAULT_AUXILIARY_MODEL)] + [
            (str(MODELS[key]["name"]), key) for key in MODELS if key not in VISION_MODELS
        ]
        with Vertical(id="settings-dialog"):
            yield Label("Session settings", id="settings-title")
            with VerticalScroll(id="settings-fields"):
                yield Label("API plan", classes="settings-label")
                yield Select(
                    self._options(API_ENDPOINTS),
                    value=self.values["api_endpoint"],
                    allow_blank=False,
                    id="api_endpoint",
                    classes="settings-select",
                )
                yield Label("Model", classes="settings-label")
                yield Select(
                    self._options(MODELS),
                    value=self.values["model"],
                    allow_blank=False,
                    id="model",
                    classes="settings-select",
                )
                yield Label("Reasoning", classes="settings-label")
                yield Select(
                    self._options(THOUGHT_LEVELS),
                    value=self.values["thought_level"],
                    allow_blank=False,
                    id="thought_level",
                    classes="settings-select",
                )
                yield Label("Permissions", classes="settings-label")
                yield Select(
                    [("Ask", "ask"), ("Read Only", "read"), ("Bypass", "bypass")],
                    value=self.values["permission_mode"],
                    allow_blank=False,
                    id="permission_mode",
                    classes="settings-select",
                )
                yield Label("Generation style", classes="settings-label")
                yield Select(
                    self._options(GENERATION_PROFILES),
                    value=self.values["generation_profile"],
                    allow_blank=False,
                    id="generation_profile",
                    classes="settings-select",
                )
                yield Label("Auxiliary model", classes="settings-label")
                yield Select(
                    auxiliary,
                    value=self.values["auxiliary_model"],
                    allow_blank=False,
                    id="auxiliary_model",
                    classes="settings-select",
                )
                yield Label("Mixture of Agents", classes="settings-label")
                yield Select(
                    [("Off", "off"), ("Reference review", "enabled")],
                    value=self.values["mixture_mode"],
                    allow_blank=False,
                    id="mixture_mode",
                    classes="settings-select",
                )
                yield Label("Session mode", classes="settings-label")
                yield Select(
                    [("Ask", "ask"), ("Code", "code")],
                    value=self.values["session_mode"],
                    allow_blank=False,
                    id="session_mode",
                    classes="settings-select",
                )
            with Horizontal(id="settings-buttons"):
                yield Button("Cancel", id="settings-cancel")
                yield Button("Apply", id="settings-apply", variant="primary")

    @on(Button.Pressed, "#settings-apply")
    def apply_settings(self) -> None:
        values: dict[str, str] = {}
        for field_id in self.FIELD_IDS:
            value = self.query_one(f"#{field_id}", Select).value
            if isinstance(value, str):
                values[field_id] = value
        self.dismiss(values)

    @on(Button.Pressed, "#settings-cancel")
    def cancel_button(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Select.Changed, "#api_endpoint")
    def endpoint_changed(self, event: Select.Changed) -> None:
        endpoint = str(event.value)
        model_keys = [key for key, info in MODELS.items() if endpoint in info.get("plans", [])]
        model_select = self.query_one("#model", Select)
        current_model = str(model_select.value)
        model_select.set_options([(str(MODELS[key]["name"]), key) for key in model_keys])
        model_select.value = current_model if current_model in model_keys else DEFAULT_MODEL
        auxiliary = [("Main model", DEFAULT_AUXILIARY_MODEL)] + [
            (str(MODELS[key]["name"]), key) for key in model_keys if key not in VISION_MODELS
        ]
        auxiliary_select = self.query_one("#auxiliary_model", Select)
        current_auxiliary = str(auxiliary_select.value)
        auxiliary_select.set_options(auxiliary)
        auxiliary_values = {value for _, value in auxiliary}
        auxiliary_select.value = (
            current_auxiliary if current_auxiliary in auxiliary_values else DEFAULT_AUXILIARY_MODEL
        )

    @on(Select.Changed, "#model")
    def model_changed(self, event: Select.Changed) -> None:
        model = str(event.value)
        levels = thought_levels_for_model(model)
        thought_select = self.query_one("#thought_level", Select)
        current = str(thought_select.value)
        thought_select.set_options([(str(THOUGHT_LEVELS[key]["name"]), key) for key in levels])
        thought_select.value = current if current in levels else "enabled"


class TuiClient:
    """ACP Client adapter that maps updates to Textual widgets."""

    def __init__(self, app: NativeGlmTui) -> None:
        self.app = app
        self._tool_titles: dict[str, str] = {}

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await self.app.handle_session_update(update)

    async def request_permission(
        self, options: list[Any], session_id: str, tool_call: Any, **kwargs: Any
    ) -> RequestPermissionResponse:
        from .terminal_cli import TerminalClient

        tool_call_id = str(getattr(tool_call, "tool_call_id", ""))
        title = (
            getattr(tool_call, "title", None)
            or self._tool_titles.get(tool_call_id)
            or "requested tool"
        )
        detail = TerminalClient._permission_detail(getattr(tool_call, "raw_input", None))
        allowed = await self.app.push_screen_wait(PermissionScreen(str(title), detail))
        allow = next((option for option in options if option.option_id == "allow"), None)
        if allowed and allow is not None:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=allow.option_id)
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    def remember_tool(self, update: Any) -> None:
        tool_call_id = str(getattr(update, "tool_call_id", ""))
        title = getattr(update, "title", None)
        if tool_call_id and title:
            self._tool_titles[tool_call_id] = str(title)


class NativeGlmTui(App[int]):
    """Full-screen coding-agent interface backed by one ``GlmAcpAgent``."""

    TITLE = "Native GLM ACP"
    SUB_TITLE = "Full harness terminal"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+q", "quit_agent", "Quit", priority=True),
        Binding("ctrl+c", "cancel_turn", "Cancel turn", priority=True),
        Binding("ctrl+l", "clear_transcript", "Clear view", priority=True),
        Binding("f1", "show_help", "Help", priority=True),
        Binding("f2", "toggle_thinking", "Thinking", priority=True),
        Binding("f3", "settings", "Settings", priority=True),
    ]

    CSS = """
    Screen { layout: vertical; }
    #workspace { height: 1fr; }
    #conversation { width: 1fr; }
    #transcript { height: 3fr; border: round $primary; padding: 0 1; }
    #thinking { height: 1fr; min-height: 5; border: round $secondary; padding: 0 1; }
    #thinking.hidden { display: none; }
    #sidebar { width: 38; min-width: 28; border: round $accent; padding: 0 1; }
    #session { height: auto; padding-bottom: 1; }
    #tools { height: 1fr; min-height: 8; border-top: solid $accent; border-bottom: solid $accent; }
    #plan { height: auto; max-height: 12; overflow-y: auto; padding-top: 1; }
    #composer { dock: bottom; height: 3; border: tall $primary; }
    .user-message { margin: 1 1 0 8; padding: 1; background: $primary 18%; }
    .agent-message { margin: 1 8 0 1; padding: 1; background: $surface-lighten-1; }
    .system-message { margin: 1 4; color: $text-muted; }
    """

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        agent_factory: Callable[[], GlmAcpAgent] = GlmAcpAgent,
    ) -> None:
        super().__init__()
        self.args = args
        self.agent = agent_factory()
        self.client = TuiClient(self)
        self.session_id = ""
        self._agent_ready = False
        self._closing = False
        self._prompt_worker: Worker[None] | None = None
        self._replaying = False
        self._current_agent: Markdown | None = None
        self._current_agent_text = ""
        self._thinking_text = ""
        self._pending_images = list(args.image)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="workspace"):
            with Vertical(id="conversation"):
                yield VerticalScroll(id="transcript")
                yield RichLog(id="thinking", wrap=True, markup=False, auto_scroll=True)
            with Vertical(id="sidebar"):
                yield Static("Starting…", id="session", markup=False)
                yield RichLog(id="tools", wrap=True, markup=True, auto_scroll=True)
                yield Static("Plan: none", id="plan", markup=False)
        yield Input(
            placeholder="Ask Native GLM ACP… (/help for commands)",
            id="composer",
            disabled=True,
        )
        yield Footer()

    def on_mount(self) -> None:
        self.agent.on_connect(self.client)
        self.initialize_agent()

    @work(exclusive=True, group="agent-initialize")
    async def initialize_agent(self) -> None:
        from .terminal_cli import _configure

        try:
            await self.agent.initialize(protocol_version=1, client_info={"name": "glm-acp-tui"})
            if self.args.resume:
                self._replaying = True
                await self.agent.resume_session(
                    cwd=self.args.cwd,
                    session_id=self.args.resume,
                    additional_directories=self.args.additional_dir,
                )
                self._replaying = False
                self.session_id = self.args.resume
            else:
                response = await self.agent.new_session(
                    cwd=self.args.cwd,
                    additional_directories=self.args.additional_dir,
                )
                self.session_id = response.session_id
            await _configure(self.agent, self.session_id, self.args)
            self._agent_ready = True
            self.query_one("#composer", Input).disabled = False
            self.query_one("#composer", Input).focus()
            self._refresh_session_panel("Ready")
            await self._append_system(
                "Full harness ready. F1 help · F2 thinking · F3 settings · "
                "Ctrl-C cancel · Ctrl-Q quit"
            )
        except Exception as error:
            await self._append_system(f"Startup failed: {error}")
            self._refresh_session_panel("Startup failed")

    @on(Input.Submitted, "#composer")
    async def submit_input(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or not self._agent_ready or self._prompt_worker is not None:
            return
        event.input.clear()
        if text in {"/exit", "/quit"}:
            await self.action_quit_agent()
            return
        if text.startswith("/image "):
            path = text.partition(" ")[2].strip()
            if path:
                self._pending_images.append(path)
                await self._append_system(f"Queued image for the next prompt: {path}")
            return
        await self._append_user(text)
        self._current_agent = None
        self._current_agent_text = ""
        self._thinking_text = ""
        self.query_one("#thinking", RichLog).clear()
        event.input.disabled = True
        self._refresh_session_panel("Running")
        self._prompt_worker = self.run_prompt(text, list(self._pending_images))
        self._pending_images.clear()

    @work(exclusive=True, group="agent-prompt", exit_on_error=False)
    async def run_prompt(self, text: str, images: list[str]) -> None:
        from .terminal_cli import _prompt_blocks

        try:
            await self.agent.prompt(
                prompt=_prompt_blocks(text, images),
                session_id=self.session_id,
                message_id=str(uuid4()),
            )
        except asyncio.CancelledError:
            await self._append_system("Turn cancelled.")
            raise
        except Exception as error:
            await self._append_system(f"Turn failed: {error}")
        finally:
            self._prompt_worker = None
            if not self._closing:
                composer = self.query_one("#composer", Input)
                composer.disabled = False
                composer.focus()
                self._refresh_session_panel("Ready")

    async def handle_session_update(self, update: Any) -> None:
        kind = str(getattr(update, "session_update", ""))
        if kind == "agent_message_chunk":
            await self._append_agent(self._content_text(update))
        elif kind == "user_message_chunk" and self._replaying:
            await self._append_user(self._content_text(update), history=True)
        elif kind == "agent_thought_chunk":
            text = self._content_text(update)
            self._thinking_text += text
            self.query_one("#thinking", RichLog).write(text, scroll_end=True)
        elif kind in {"tool_call", "tool_call_update"}:
            self.client.remember_tool(update)
            tool_call_id = str(getattr(update, "tool_call_id", ""))
            title = getattr(update, "title", None) or self.client._tool_titles.get(tool_call_id)
            status = getattr(update, "status", None)
            if title:
                self.query_one("#tools", RichLog).write(
                    f"[bold]{escape(str(title))}[/bold] · {escape(str(status or 'tool'))}",
                    scroll_end=True,
                )
        elif kind == "plan":
            lines = ["Plan"]
            for entry in getattr(update, "entries", []):
                marker = {"completed": "✓", "in_progress": "▶"}.get(
                    str(getattr(entry, "status", "")), "○"
                )
                lines.append(f"{marker} {getattr(entry, 'content', '')}")
            self.query_one("#plan", Static).update("\n".join(lines))
        elif kind == "usage_update":
            self._refresh_session_panel(
                "Running" if self._prompt_worker is not None else "Ready",
                used=int(getattr(update, "used", 0)),
                size=int(getattr(update, "size", 0)),
            )
        elif kind == "session_info_update":
            self.sub_title = str(getattr(update, "title", "Full harness terminal"))

    @staticmethod
    def _content_text(update: Any) -> str:
        return str(getattr(getattr(update, "content", None), "text", ""))

    async def _append_user(self, text: str, *, history: bool = False) -> None:
        self._current_agent = None
        label = "You · history" if history else "You"
        widget = Static(f"{label}\n{text}", classes="user-message", markup=False)
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(widget)
        transcript.scroll_end(animate=False)

    async def _append_agent(self, text: str) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        if self._current_agent is None:
            self._current_agent_text = ""
            self._current_agent = Markdown("", classes="agent-message")
            await transcript.mount(self._current_agent)
        self._current_agent_text += text
        await self._current_agent.update(self._current_agent_text)
        transcript.scroll_end(animate=False)

    async def _append_system(self, text: str) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(Static(text, classes="system-message", markup=False))
        transcript.scroll_end(animate=False)

    def _refresh_session_panel(self, state: str, *, used: int = 0, size: int = 0) -> None:
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        model = getattr(session, "model", self.args.model or "default")
        permission = getattr(session, "permission_mode", self.args.permission or "ask")
        mode = getattr(session, "mode", self.args.mode or "code")
        context = f"{used:,}/{size:,}" if size else "waiting"
        self.query_one("#session", Static).update(
            f"{state}\nSession: {self.session_id or 'starting'}\n"
            f"Model: {model}\nMode: {mode}\nPermissions: {permission}\nContext: {context}"
        )

    async def action_cancel_turn(self) -> None:
        if self._prompt_worker is None:
            self.notify("No active turn", severity="information")
            return
        await self.agent.cancel(session_id=self.session_id)
        self._prompt_worker.cancel()

    async def action_quit_agent(self) -> None:
        self._closing = True
        if self._prompt_worker is not None:
            await self.agent.cancel(session_id=self.session_id)
            self._prompt_worker.cancel()
        await self.agent.aclose()
        self.exit(0)

    async def action_clear_transcript(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        self._current_agent = None
        self._current_agent_text = ""

    def action_show_help(self) -> None:
        self.query_one("#composer", Input).value = "/help"
        self.query_one("#composer", Input).focus()

    def action_toggle_thinking(self) -> None:
        self.query_one("#thinking", RichLog).toggle_class("hidden")

    def action_settings(self) -> None:
        if self._agent_ready and self._prompt_worker is None:
            self.open_settings()

    @work(exclusive=True, group="settings")
    async def open_settings(self) -> None:
        session = self.agent._sessions[self.session_id]
        values = {
            "api_endpoint": session.api_endpoint,
            "model": session.model,
            "thought_level": session.thought_level,
            "permission_mode": session.permission_mode,
            "generation_profile": session.generation_profile,
            "auxiliary_model": session.auxiliary_model,
            "mixture_mode": session.mixture_mode,
            "session_mode": session.mode,
        }
        selected = await self.push_screen_wait(SettingsScreen(values))
        if not selected:
            return
        for config_id in (
            "api_endpoint",
            "model",
            "thought_level",
            "permission_mode",
            "generation_profile",
            "auxiliary_model",
            "mixture_mode",
        ):
            await self.agent.set_config_option(
                config_id=config_id,
                session_id=self.session_id,
                value=selected[config_id],
            )
        await self.agent.set_session_mode(
            mode_id=selected["session_mode"], session_id=self.session_id
        )
        self._refresh_session_panel("Ready")
        await self._append_system("Session settings updated.")

    async def on_unmount(self) -> None:
        if not self._closing:
            self._closing = True
            if self._prompt_worker is not None:
                await self.agent.cancel(session_id=self.session_id)
                self._prompt_worker.cancel()
            await self.agent.aclose()


def run_tui_command(args: argparse.Namespace) -> int:
    """Run the full-screen frontend and return its process exit status."""
    from pathlib import Path

    if not Path(args.cwd).is_dir():
        print(f"Workspace does not exist: {args.cwd}", file=__import__("sys").stderr)
        return 2
    try:
        result = NativeGlmTui(args).run()
        return int(result or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"Native GLM ACP TUI failed: {error}", file=__import__("sys").stderr)
        return 1
