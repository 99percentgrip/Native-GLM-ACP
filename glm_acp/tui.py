"""Full-screen Textual frontend for the shared Native GLM ACP runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse
from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape
from rich.style import Style
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.selection import Selection
from textual.timer import Timer
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    RichLog,
    Select,
    Static,
)
from textual.widgets.option_list import Option
from textual.worker import Worker

from .agent import GlmAcpAgent
from .config import (
    API_ENDPOINTS,
    DEFAULT_AUXILIARY_MODEL,
    DEFAULT_MODEL,
    GENERATION_PROFILES,
    MOA_PICKER_VALUE,
    MODELS,
    STATUSLINE_SEGMENTS,
    THOUGHT_LEVELS,
    VISION_MODELS,
    keybinds_path,
    load_keybinds_config,
    load_screen_reader_config,
    load_statusline_config,
    load_theme_config,
    load_vim_config,
    save_keybinds_config,
    save_screen_reader_config,
    save_statusline_config,
    save_theme_config,
    save_vim_config,
    thought_levels_for_model,
)
from .glm_client import PlanUsage
from .memory import list_learned_skills, read_memory, read_user_profile

LOCAL_COMMANDS = {
    "/plan": "Switch between Coding Plan, Standard API, and BigModel (CN)",
    "/thinking": "Change provider thinking: Off, Standard, Deep High, or Deep Max",
    "/model": "Change the active GLM model",
    "/usage": "Refresh live 5-hour, weekly, and MCP Coding Plan quota",
    "/permission": "Change Ask, Read Only, or Bypass permissions",
    "/mode": "Change Ask or Code session mode",
    "/generation": "Change the generation style",
    "/auxiliary": "Change the auxiliary model",
    "/mixture": "Enable or disable Mixture of Agents",
    "/settings": "Open all live session settings",
    "/reasoning": "Alias for /thinking",
    "/api-plan": "Alias for /plan",
    "/endpoint": "Alias for /plan",
    "/reasoning-panel": "Show or hide the live reasoning panel",
    "/toggle-thinking": "Alias for /reasoning-panel",
    "/clear-view": "Clear only the visible transcript",
    "/max-iterations": "Show or set the per-turn tool-call iteration cap (default 50, max 1000)",
    "/recap": "Show a one-line summary of the session so far",
    "/blocks": "Pick a code block from recent responses to copy or save (Enter copy, w write)",
    "/statusline": "Choose which sidebar segments are visible (state, model, tokens, quota, …)",
    "/context": "Visualize context-window usage by segment (system, user, assistant, tool)",
    "/btw": "Ask a side question without polluting the conversation (/btw <question>)",
    "/theme": "Switch the visual theme (textual-dark, textual-light, ansi, dracula, nord, …)",
    "/tasks": "Show the session dashboard (turn state, queue, tokens, model, context)",
    "/release": "Cut a release from the workspace (/release [patch|minor|major])",
    "/insights": "Analyze the session for friction points and improvement opportunities",
    "/loop": "Run a prompt repeatedly at an interval (/loop 5m check CI status, /loop stop)",
    "/security-review": "Scan the working-tree diff for security vulnerabilities",
    "/rewind": "Alias for /rollback — rewind conversation to a prior checkpoint",
    "/smart": "Expand a smart-prompt template with git context (/smart pr, review, commit, fix-ci)",
    "/sound": "Toggle notification sounds on/off for this session",
    "/screen-reader": "Toggle screen-reader mode (plain text, no animations, F8)",
    "/keybinds": "Customize TUI F-key and Ctrl-key bindings",
    "/vim": "Toggle vim-mode composer (Normal/Insert/Visual, F9)",
    "/annotate": "Annotate working-tree diff hunks for the next prompt",
    "/rename": "Rename the current session (/rename <name>)",
    "/branch": "Fork the current session to try a different direction (/branch [name])",
    # Agent-side commands (implemented in the shared runtime; listed here so
    # they appear in the /-menu and the Ctrl+P command palette for discovery).
    "/status": "Show session, model, permissions, context, and live evidence",
    "/compact": "Compact older context (optionally: /compact focus on the bug fix)",
    "/diff": "Show the working-tree diff in the transcript",
    "/clear-plan": "Clear the active plan",
    "/clear-history": "Clear saved session history for this workspace",
    "/checkpoint": "Manage conversation checkpoints (/checkpoint list, save, restore)",
    "/rollback": "Roll back to a prior checkpoint (/rollback [id])",
    "/plugins": "List trusted plugin publishers and installed plugins",
    "/goal": "Set or inspect a persistent goal (/goal <objective>, clear, pause, resume)",
    "/subgoal": "Add an acceptance criterion to the current persistent goal",
    "/awareness": "Show the live epistemic state (observations, hypotheses, contradictions)",
    "/metacognition": "Show the metacognitive assessment (mode, risk, profile)",
    "/deliberation": "Show the active grounded-deliberation hypotheses and tests",
    "/repository": "Show repository-intelligence metadata and impact predictions",
    "/meta-learning": "Show metacognitive-learning candidates and gates",
    "/observability": "Show secret-safe local reliability metrics (/observability json)",
    "/memory": "Show project-local memory entries",
    "/skills": "List learned project skills (with use counts and pin/archive state)",
    "/profile": "Show approved cross-project user-profile preferences",
    "/curator": "Run deterministic skill maintenance (mark stale, archive idle)",
    "/sessions": "Search past sessions (/sessions <query>)",
    "/lineage": "Show the session-lineage chain (parents and forks)",
    "/mcp": "Manage MCP server connections (list, enable, disable, reconnect)",
    "/ci": "Show CI status for the current branch",
    "/version": "Show package, Python, and platform version info",
    "/help": "Show the full harness command reference",
    "/copy": "Copy the last response to clipboard (or /copy <N> for response N, /copy all)",
    "/history": "Browse and resume past sessions (or press F6)",
    "/search": "Grep the current conversation (or press Ctrl-F)",
    "/export": "Export current session: /export [md|json] [file|clip] (default md clip)",
    "/undo": "Take back the last N user turns (default 1); prefills the composer",
    "/prompt": "Compose your next prompt in $EDITOR (multi-line markdown)",
    "/journey": "Show the timeline of memories + skills + profile learned over time",
    "/native-mouse": "Toggle native terminal mouse mode (release TUI mouse capture)",
    "/planmode": "Activate Plan Mode with a PRD: /planmode <your requirements>",
    "/export last": "Export the last response to a Markdown file",
    "/image": "Queue an image for the next prompt",
    "/exit": "Close the terminal agent",
}


# These are the user-customizable application bindings. Stable action IDs on
# ``NativeGlmTui.BINDINGS`` let Textual replace a default binding instead of
# layering a second copy of the same action over it.
KEYBINDABLE_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("quit_agent", "Quit terminal agent", "ctrl+x"),
    ("cancel_turn", "Cancel active turn", "ctrl+c"),
    ("clear_transcript", "Clear transcript", "ctrl+l"),
    ("show_help", "Show help", "f1"),
    ("toggle_thinking", "Toggle reasoning panel", "f2"),
    ("settings", "Open settings", "f3"),
    ("toggle_working_tree", "Cycle working-tree panel", "f4"),
    ("toggle_voice", "Toggle push-to-talk", "f5"),
    ("open_history", "Open session history", "f6"),
    ("toggle_native_mouse", "Toggle native mouse", "f7"),
    ("toggle_screen_reader", "Toggle screen-reader mode", "f8"),
    ("open_search", "Search conversation", "ctrl+f"),
    ("copy_last_response", "Copy last response", "ctrl+y"),
    ("copy_selection", "Copy current selection", "ctrl+shift+c"),
)
KEYBINDABLE_ACTION_IDS = frozenset(action for action, _label, _key in KEYBINDABLE_ACTIONS)
DEFAULT_KEYBINDS = {action: key for action, _label, key in KEYBINDABLE_ACTIONS}

# Smart prompt templates: one-click actions with auto-resolved git context.
# Variables: {branch}, {diff}, {commit_log}, {cwd}.
# /smart (bare) lists these; /smart <name> resolves and inserts into the
# composer for review before sending.
SMART_PROMPTS: dict[str, tuple[str, str]] = {
    "pr": (
        "Create a PR",
        "Create a GitHub pull request for the current branch {branch}. "
        "Generate a descriptive title and body based on the commits:\n"
        "{commit_log}\nUse `gh pr create`.",
    ),
    "review": (
        "Review changes",
        "Review the uncommitted changes in the working tree for correctness, "
        "style, and potential bugs. Here is the diff:\n\n{diff}",
    ),
    "commit": (
        "Write commit message",
        "Write a clear conventional commit message for these changes:\n\n{diff}",
    ),
    "fix-ci": (
        "Fix CI failures",
        "The CI may be failing on branch {branch}. "
        "Run the CI checks locally, identify the failures, and fix them.",
    ),
}

CONFIG_COMMANDS = {
    "/plan": ("api_endpoint", "API plan"),
    "/thinking": ("thought_level", "Thinking"),
    "/model": ("model", "Model"),
    "/permission": ("permission_mode", "Permissions"),
    "/generation": ("generation_profile", "Generation style"),
    "/auxiliary": ("auxiliary_model", "Auxiliary model"),
    "/mixture": ("mixture_mode", "Mixture of Agents"),
    "/mode": ("session_mode", "Session mode"),
    "/reasoning": ("thought_level", "Thinking"),
    "/api-plan": ("api_endpoint", "API plan"),
    "/endpoint": ("api_endpoint", "API plan"),
}

MAX_CLIPBOARD_CHARS = 1_000_000


def _read_system_clipboard() -> str:
    """Read the OS clipboard for an explicit Ctrl-V without invoking a shell."""
    if sys.platform == "darwin":
        commands = [("pbpaste",)]
    elif os.name == "nt":
        commands = [
            ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"),
            ("pwsh", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"),
        ]
    else:
        commands = []
        if os.environ.get("WAYLAND_DISPLAY"):
            commands.append(("wl-paste",))
        commands.extend(
            [
                ("xclip", "-selection", "clipboard", "-out"),
                ("xsel", "--clipboard", "--output"),
            ]
        )
    allowed_environment = {
        name: value
        for name in (
            "DISPLAY",
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "SystemRoot",
            "USERPROFILE",
            "WAYLAND_DISPLAY",
            "WINDIR",
            "XAUTHORITY",
            "XDG_RUNTIME_DIR",
        )
        if (value := os.environ.get(name))
    }
    for command in commands:
        executable = shutil.which(command[0])
        if executable is None:
            continue
        try:
            result = subprocess.run(
                (executable, *command[1:]),
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                env=allowed_environment,
                timeout=1.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout:
            return result.stdout[:MAX_CLIPBOARD_CHARS].rstrip("\r\n")
    return ""


def _write_system_clipboard(text: str) -> bool:
    """Write text to the OS clipboard without invoking a shell."""
    if sys.platform == "darwin":
        commands = [("pbcopy",)]
    elif os.name == "nt":
        commands = [
            (
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$input = [Console]::In::ReadToEnd(); Set-Clipboard -Value $input",
            ),
            ("clip",),
        ]
    else:
        commands = []
        if os.environ.get("WAYLAND_DISPLAY"):
            commands.append(("wl-copy",))
        commands.extend(
            [
                ("xclip", "-selection", "clipboard"),
                ("xsel", "--clipboard", "--input"),
            ]
        )
    allowed_environment = {
        name: value
        for name in (
            "DISPLAY",
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "SystemRoot",
            "USERPROFILE",
            "WAYLAND_DISPLAY",
            "WINDIR",
            "XAUTHORITY",
            "XDG_RUNTIME_DIR",
        )
        if (value := os.environ.get(name))
    }
    for command in commands:
        executable = shutil.which(command[0])
        if executable is None:
            continue
        try:
            result = subprocess.run(
                (executable, *command[1:]),
                input=text,
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                env=allowed_environment,
                timeout=1.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


class CommandInput(Input):
    """Composer input with command-menu navigation while focus stays in place."""

    BINDINGS = [
        Binding("ctrl+shift+v", "paste_system", show=False, priority=True),
        Binding("tab", "command_complete", show=False, priority=True),
        Binding("up", "command_up", show=False, priority=True),
        Binding("down", "command_down", show=False, priority=True),
        Binding("escape", "command_escape", show=False, priority=True),
    ]

    def action_command_complete(self) -> None:
        app = self.app
        if isinstance(app, NativeGlmTui):
            app.accept_command_completion(submit=False)

    def action_command_up(self) -> None:
        app = self.app
        if isinstance(app, NativeGlmTui):
            app.move_command_highlight(-1)

    def action_command_down(self) -> None:
        app = self.app
        if isinstance(app, NativeGlmTui):
            app.move_command_highlight(1)

    def action_command_escape(self) -> None:
        app = self.app
        if isinstance(app, NativeGlmTui):
            app.hide_command_menu()

    def _on_paste(self, event: events.Paste) -> None:
        """Keep a multiline terminal paste usable in the single-line composer."""
        self._insert_pasted_text(event.text)
        event.stop()

    def _insert_pasted_text(self, text: str) -> None:
        if "\n" in text or "\r" in text:
            text = " ".join(text.splitlines()).strip()
        if text:
            selection = self.selection
            if selection.is_empty:
                self.insert_text_at_cursor(text)
            else:
                self.replace(text, *selection)

    def action_paste(self) -> None:
        """Paste the internal clipboard or explicitly read the OS clipboard."""
        text = self.app.clipboard or _read_system_clipboard()
        self._apply_clipboard_text(text)

    def action_paste_system(self) -> None:
        """Read the OS clipboard for terminals that deliver Ctrl-Shift-V as a key."""
        text = _read_system_clipboard() or self.app.clipboard
        self._apply_clipboard_text(text)

    def _apply_clipboard_text(self, text: str) -> None:
        if text:
            self._insert_pasted_text(text)
        else:
            self.app.notify(
                "Clipboard is empty or unavailable; try the terminal paste shortcut",
                severity="warning",
            )


class ModalComposer(CommandInput):
    """Single-line Vim modal layer that preserves the normal composer APIs."""

    mode = "insert"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_operator = ""
        self._pending_g = False
        self._clipboard = ""
        self._undo_value = ""
        self._visual_anchor = 0

    def _word_end(self) -> int:
        match = re.search(r"\w+", self.value[self.cursor_position :])
        end = self.cursor_position + (match.end() if match else 1)
        while end < len(self.value) and self.value[end].isspace():
            end += 1
        return end

    def _delete_range(self, start: int, end: int, *, yank: bool = False) -> None:
        self._undo_value = self.value
        self._clipboard = self.value[start:end]
        if not yank:
            self.value = self.value[:start] + self.value[end:]
            self.cursor_position = min(start, len(self.value))

    def _move_cursor(self, position: int) -> None:
        """Move the cursor while retaining the visual-mode anchor."""
        self.cursor_position = max(0, min(len(self.value), position))
        if self.mode == "visual":
            self.selection = self.selection.__class__(self._visual_anchor, self.cursor_position)

    def handle_vim_key(self, key: str) -> bool:
        """Apply one modal editing key; return whether Textual should consume it."""
        if self.mode == "insert":
            if key == "escape":
                self.set_mode("normal")
                return True
            return False
        if self._pending_operator:
            operator, self._pending_operator = self._pending_operator, ""
            end = len(self.value) if key == "$" else self._word_end()
            if key in {"d", "y"}:
                end = self.value.find("\n", self.cursor_position)
                end = len(self.value) if end < 0 else end + 1
            if key in {"w", "d", "y"}:
                self._delete_range(self.cursor_position, end, yank=operator == "y")
                return True
        if key == "escape":
            self.set_mode("normal")
        elif self.mode == "normal" and key in {"i", "a", "I", "A", "o", "O"}:
            if key == "a":
                self.cursor_position = min(len(self.value), self.cursor_position + 1)
            elif key == "I":
                self.cursor_position = 0
            elif key == "A":
                self.cursor_position = len(self.value)
            elif key in {"o", "O"}:
                self.insert_text_at_cursor(" ")
            self.set_mode("insert")
        elif self.mode == "visual" and key in {"y", "d"}:
            start, end = sorted((self.selection.start, self.selection.end))
            self._delete_range(start, end, yank=key == "y")
            self.set_mode("normal")
        elif key in {"d", "y"}:
            self._pending_operator = key
        elif key == "p":
            self._undo_value = self.value
            self.insert_text_at_cursor(self._clipboard)
        elif key == "u":
            self.value, self._undo_value = self._undo_value, self.value
        elif key == "v":
            self._visual_anchor = self.cursor_position
            self.set_mode("visual")
        elif key == "h":
            self._move_cursor(self.cursor_position - 1)
        elif key == "l":
            self._move_cursor(self.cursor_position + 1)
        elif key in {"j", "k"}:
            # The composer is intentionally single-line; vertical movement is
            # a safe no-op that still keeps Vim key sequences modal.
            pass
        elif key == "w":
            self._move_cursor(self._word_end())
        elif key == "b":
            self._move_cursor(self.value.rfind(" ", 0, self.cursor_position - 1) + 1)
        elif key == "0":
            self._move_cursor(0)
        elif key == "g":
            if self._pending_g:
                self._move_cursor(0)
            self._pending_g = not self._pending_g
        elif key in {"$", "G"}:
            self._move_cursor(len(self.value))
        elif key == "x":
            self._delete_range(self.cursor_position, self.cursor_position + 1)
        return True

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        labels = {"normal": "[N] Normal", "insert": "[I] Insert", "visual": "[V] Visual"}
        self.border_subtitle = labels[mode]

    async def _on_key(self, event: events.Key) -> None:
        app = self.app
        if not isinstance(app, NativeGlmTui) or not app._vim_enabled:
            await super()._on_key(event)
            return
        key = event.key
        # Slash-command completion remains available in Normal mode: entering
        # '/' switches to Insert and lets CommandInput open the live menu.
        if self.mode == "normal" and key in {"/", "slash"}:
            self.set_mode("insert")
            await super()._on_key(event)
            return
        if self.mode == "insert" and key != "escape":
            await super()._on_key(event)
            return
        if self.handle_vim_key(key):
            event.stop()
            event.prevent_default()


def _diff_annotation_anchors(diff_text: str) -> list[tuple[str, int, str]]:
    """Return displayable diff lines with their best-effort new-file anchors."""
    anchors: list[tuple[str, int, str]] = []
    current_file = "(unknown)"
    next_line = 1
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git a/"):
            parts = raw_line.split()
            if len(parts) >= 4 and parts[-1].startswith("b/"):
                current_file = parts[-1][2:]
        elif raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
        match = hunk.match(raw_line)
        if match:
            next_line = int(match.group(1))
            anchors.append((current_file, next_line, raw_line))
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            anchors.append((current_file, next_line, raw_line))
            next_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            anchors.append((current_file, max(1, next_line - 1), raw_line))
        elif raw_line.startswith(" "):
            anchors.append((current_file, next_line, raw_line))
            next_line += 1
        else:
            anchors.append((current_file, next_line, raw_line))
    return anchors


class AnnotationCommentScreen(ModalScreen[str | None]):
    """Small focused editor for one line-anchored diff comment."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    CSS = """
    AnnotationCommentScreen { align: center middle; background: $background 70%; }
    #annotation-comment-dialog {
        width: 76; max-width: 94%; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    #annotation-comment-input { width: 1fr; }
    """

    def __init__(self, file_path: str, line: int) -> None:
        super().__init__()
        self._file_path = file_path
        self._line = line

    def compose(self) -> ComposeResult:
        with Vertical(id="annotation-comment-dialog"):
            yield Label(f"Comment on {self._file_path}:{self._line}")
            yield Input(
                placeholder="Describe the requested revision…",
                id="annotation-comment-input",
            )
            yield Static("Enter save  ·  Esc cancel", markup=False)

    def on_mount(self) -> None:
        self.query_one("#annotation-comment-input", Input).focus()

    @on(Input.Submitted, "#annotation-comment-input")
    def save_comment(self, event: Input.Submitted) -> None:
        comment = event.value.strip()
        self.dismiss(comment or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DiffAnnotationScreen(ModalScreen[list[tuple[str, int, str]] | None]):
    """Navigate a working-tree diff and attach revision comments to its lines."""

    BINDINGS = [
        Binding("c", "comment", "Comment", priority=True),
        Binding("escape", "done", "Done", priority=True),
    ]

    CSS = """
    DiffAnnotationScreen { align: center middle; background: $background 70%; }
    #annotation-dialog {
        width: 112; max-width: 98%; height: 88%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    #annotation-lines { height: 1fr; border: solid $panel; }
    #annotation-comments { height: auto; max-height: 6; color: $text-muted; }
    #annotation-hint { height: 1; color: $text-muted; }
    """

    def __init__(self, diff_text: str) -> None:
        super().__init__()
        self._anchors = _diff_annotation_anchors(diff_text)
        self.comments: list[tuple[str, int, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="annotation-dialog"):
            yield Label("Diff annotations")
            yield OptionList(
                *[
                    Option(f"{file_path}:{line:>5}  {text}", id=str(index))
                    for index, (file_path, line, text) in enumerate(self._anchors)
                ],
                id="annotation-lines",
                markup=False,
            )
            yield Static("No comments yet.", id="annotation-comments", markup=False)
            yield Static(
                "↑↓ select a line  ·  c comment  ·  Esc insert follow-up prompt",
                id="annotation-hint",
                markup=False,
            )

    def on_mount(self) -> None:
        self.query_one("#annotation-lines", OptionList).focus()

    def action_comment(self) -> None:
        lines = self.query_one("#annotation-lines", OptionList)
        index = lines.highlighted
        if index is None or not 0 <= index < len(self._anchors):
            return
        file_path, line, _text = self._anchors[index]
        self.app.push_screen(
            AnnotationCommentScreen(file_path, line),
            lambda comment: self._save_comment(file_path, line, comment),
        )

    def _save_comment(self, file_path: str, line: int, comment: str | None) -> None:
        if not comment:
            return
        self.comments.append((file_path, line, comment))
        rendered = "\n".join(f"• {path}:{number} — {note}" for path, number, note in self.comments)
        self.query_one("#annotation-comments", Static).update(rendered)

    def action_done(self) -> None:
        self.dismiss(self.comments or None)


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
        endpoint = self.values["api_endpoint"]
        model_keys = [key for key, info in MODELS.items() if endpoint in info.get("plans", [])]
        thought_levels = thought_levels_for_model(self.values["model"])
        auxiliary = [("Use main model", DEFAULT_AUXILIARY_MODEL)] + [
            (str(MODELS[key]["name"]), key) for key in model_keys if key not in VISION_MODELS
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
                    [(str(MODELS[key]["name"]), key) for key in model_keys],
                    value=self.values["model"],
                    allow_blank=False,
                    id="model",
                    classes="settings-select",
                )
                yield Label("Reasoning", classes="settings-label")
                yield Select(
                    self._options(thought_levels),
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


def _journey_extract_memory_lines(cwd: str) -> list[str]:
    """Pull plain-text memory entries (strip the leading '- ' marker)."""
    try:
        text = read_memory(cwd)
    except Exception:
        return []
    if not text or text.startswith("No durable project memory"):
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:].strip())
    return lines


def _journey_extract_profile_lines() -> list[str]:
    """Pull plain-text user-profile entries (strip the '- [category] ' marker)."""
    try:
        text = read_user_profile()
    except Exception:
        return []
    if not text or text.startswith("No private"):
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        # Profile entries look like '- [preference] the fact'
        match = re.match(r"^- \[[a-z-]+\] (.+)$", stripped)
        if match:
            lines.append(match.group(1).strip())
    return lines


def _format_session_row(meta: dict[str, Any]) -> tuple[str, str]:
    """Format a SessionStore.list() row into (first_line, second_line)."""
    title = meta.get("title") or "Untitled session"
    sid = str(meta.get("session_id", ""))[:8]
    raw_when = meta.get("updated_at") or meta.get("saved_at") or ""
    when: str
    if raw_when:
        try:
            normalized = raw_when.replace("Z", "+00:00")
            when = datetime.fromisoformat(normalized).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            when = raw_when[:16]
    else:
        when = "—"
    cwd = str(meta.get("cwd") or "")
    if cwd:
        cwd_short = Path(cwd).name or cwd
    else:
        cwd_short = ""
    branch = meta.get("branch_root_id") or meta.get("session_id") or sid
    branch_marker = ""
    if branch and str(branch) != str(meta.get("session_id")):
        branch_marker = " · branched"
    second = f"{when} · {sid} · {cwd_short}{branch_marker}"
    return title, second


class HistoryScreen(ModalScreen[str | None]):
    """Browse persisted sessions and pick one to resume.

    Returns the selected session_id (or None on cancel). The main app
    resolves the resume via the shared agent.resume_session() method.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("q", "cancel", "Cancel", priority=True),
    ]

    CSS = """
    HistoryScreen { align: center middle; background: $background 70%; }
    #history-dialog {
        width: 86; max-width: 96%; height: 80%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #history-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #history-list { height: 1fr; border: solid $panel; margin-bottom: 1; }
    #history-hint { color: $text-muted; height: 1; }
    """

    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        super().__init__()
        self.sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="history-dialog"):
            yield Label(
                f"Session history ({len(self.sessions)} persisted)",
                id="history-title",
            )
            if self.sessions:
                listview = ListView(id="history-list")
                yield listview
            else:
                yield Static(
                    "No persisted sessions yet.\nSessions are saved automatically after each turn.",
                    id="history-list",
                    markup=False,
                )
            yield Static(
                "↑↓ navigate  ·  Enter resume  ·  Esc cancel",
                id="history-hint",
                markup=False,
            )

    def on_mount(self) -> None:
        if not self.sessions:
            return
        listview = self.query_one("#history-list", ListView)
        for meta in self.sessions:
            title, second = _format_session_row(meta)
            listview.append(
                ListItem(
                    Static(title, markup=False),
                    Static(f"[dim]{escape(second)}[/dim]", markup=True),
                )
            )

    @on(ListView.Selected)
    def session_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or idx >= len(self.sessions):
            return
        meta = self.sessions[idx]
        self.dismiss(str(meta.get("session_id") or ""))

    def action_cancel(self) -> None:
        self.dismiss(None)


class SearchScreen(ModalScreen[tuple[int, str] | None]):
    """Grep the current conversation and show matching messages.

    Searches the live in-memory session.messages (not the FTS5 store,
    which only indexes on save). Returns (ordinal, full_text) on select,
    or None on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    CSS = """
    SearchScreen { align: center middle; background: $background 70%; }
    #search-dialog {
        width: 90; max-width: 96%; height: 84%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #search-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #search-input { margin-bottom: 1; }
    #search-results { height: 1fr; border: solid $panel; }
    #search-hint { color: $text-muted; height: 1; margin-top: 1; }
    """

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        super().__init__()
        self.messages = messages
        self._matches: list[tuple[int, str, str, str]] = []  # (ord, role, snippet, full_text)

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Label("Search conversation", id="search-title")
            yield Input(placeholder="Type to search messages…", id="search-input")
            yield ListView(id="search-results")
            yield Static(
                "↑↓ navigate  ·  Enter view full message  ·  Esc cancel",
                id="search-hint",
                markup=False,
            )

    @on(Input.Changed, "#search-input")
    def query_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        listview = self.query_one("#search-results", ListView)
        listview.clear()
        self._matches = []
        if not query:
            return
        try:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        except re.error:
            return
        for ordinal, msg in enumerate(self.messages):
            role = str(msg.get("role", "?"))
            text = _extract_message_text(msg)
            if not text:
                continue
            match = pattern.search(text)
            if not match:
                continue
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 120)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(text) else ""
            snippet = (prefix + text[start:end].replace("\n", " ") + suffix).strip()
            label_role = {"user": "You", "assistant": "Agent", "tool": "Tool", "system": "Sys"}.get(
                role, role.capitalize()
            )
            listview.append(
                ListItem(
                    Static(
                        f"[bold]{escape(label_role)} #{ordinal}[/bold]  {escape(snippet)}",
                        markup=True,
                    )
                )
            )
            self._matches.append((ordinal, role, snippet, text))

    @on(ListView.Selected, "#search-results")
    def match_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or idx >= len(self._matches):
            return
        _ordinal, _role, _snippet, full_text = self._matches[idx]
        self.dismiss((idx, full_text))

    def action_cancel(self) -> None:
        self.dismiss(None)


class JourneyScreen(ModalScreen[None]):
    """`/journey` — chronological timeline of memories + skills + profile.

    Pure presentation: pulls from the existing durable storage helpers
    (read_memory, list_learned_skills, read_user_profile). No new
    persistence. Cancel-only modal (Esc or q).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("q", "cancel", "Cancel", priority=True),
    ]

    CSS = """
    JourneyScreen { align: center middle; background: $background 70%; }
    #journey-dialog {
        width: 92; max-width: 96%; height: 86%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #journey-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #journey-list { height: 1fr; border: solid $panel; }
    #journey-hint { color: $text-muted; height: 1; margin-top: 1; }
    """

    def __init__(
        self,
        memories: list[str],
        skills: list[dict[str, Any]],
        profile: list[str],
    ) -> None:
        super().__init__()
        self.memories = memories
        self.skills = skills
        self.profile = profile

    def compose(self) -> ComposeResult:
        with Vertical(id="journey-dialog"):
            yield Label("Learning journey", id="journey-title")
            yield ListView(id="journey-list")
            yield Static(
                "↑↓ scroll  ·  Esc / q close",
                id="journey-hint",
                markup=False,
            )

    def on_mount(self) -> None:
        # Defer population until the ListView is fully mounted.
        self.call_after_refresh(self._populate)

    def _populate(self) -> None:
        from datetime import datetime as _dt

        entries: list[tuple[str, str | None, str, str]] = []
        # Skills — most rich data, have timestamps.
        for skill in self.skills:
            created = skill.get("created_at")
            when: str | None
            if isinstance(created, str) and created:
                try:
                    when = _dt.fromisoformat(created.replace("Z", "+00:00")).strftime("%Y-%m-%d")
                except ValueError:
                    when = created[:10]
            else:
                when = None
            uses = int(skill.get("use_count", 0) or 0)
            state_marker = (
                "📌 "
                if skill.get("pinned")
                else ("📦 " if skill.get("state") == "archived" else "✓ ")
            )
            summary = f"{skill.get('description', '') or '(no description)'}"
            if uses:
                summary += f"  · {uses} use{'s' if uses != 1 else ''}"
            entries.append(
                (
                    when or "—",
                    when,
                    f"{state_marker}skill · {escape(str(skill.get('name', '')))}",
                    escape(summary),
                )
            )
        # Project memory — no timestamps; show file order as "memory".
        for entry in self.memories:
            entries.append(
                (
                    "—",
                    None,
                    "✓ memory",
                    escape(entry),
                )
            )
        # User profile — no timestamps either.
        for entry in self.profile:
            entries.append(
                (
                    "—",
                    None,
                    "✓ profile",
                    escape(entry),
                )
            )
        # Sort by date desc (skills with timestamps first), then alphabetic.
        entries.sort(key=lambda e: (e[1] or "", e[0]), reverse=True)
        listview = self.query_one("#journey-list", ListView)
        if not entries:
            listview.append(
                ListItem(
                    Static(
                        "[dim]Nothing learned yet. The agent stores memories and "
                        "skills after verified tasks.[/dim]",
                        markup=True,
                    )
                )
            )
            return
        for when, _sortable, kind, summary in entries:
            listview.append(
                ListItem(
                    Static(
                        f"[bold]{escape(when)}[/bold]  [dim]{kind}[/dim]",
                        markup=True,
                    ),
                    Static(f"  {summary}", markup=True),
                )
            )

    def action_cancel(self) -> None:
        self.dismiss(None)


class CodeBlockPickerScreen(ModalScreen[tuple[str, str] | None]):
    """Pick a code block from recent agent responses to copy or save.

    Returns ``(action, code)`` where ``action`` is ``"copy"`` or
    ``"write"``; returns ``None`` on cancel. Used by ``/blocks``.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("w", "write_selected", "Write to file", priority=True),
    ]

    CSS = """
    CodeBlockPickerScreen { align: center middle; background: $background 70%; }
    #blocks-dialog {
        width: 96; max-width: 96%; height: 84%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #blocks-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #blocks-list { height: 1fr; border: solid $panel; }
    #blocks-hint { color: $text-muted; height: 1; margin-top: 1; }
    """

    def __init__(self, blocks: list[tuple[str, str]]) -> None:
        super().__init__()
        self._blocks = blocks

    def compose(self) -> ComposeResult:
        with Vertical(id="blocks-dialog"):
            yield Label(
                f"Code blocks in recent responses ({len(self._blocks)})",
                id="blocks-title",
            )
            yield ListView(id="blocks-list")
            yield Static(
                "↑↓ navigate  ·  Enter copy to clipboard  ·  w write to file  ·  Esc cancel",
                id="blocks-hint",
                markup=False,
            )

    def on_mount(self) -> None:
        listview = self.query_one("#blocks-list", ListView)
        for index, (lang, code) in enumerate(self._blocks):
            first_line = code.splitlines()[0][:64] if code else ""
            line_count = code.count("\n") + 1
            listview.append(
                ListItem(
                    Static(
                        f"[bold]#{index + 1} [{escape(lang) or 'text'}] "
                        f"({line_count} line{'s' if line_count != 1 else ''})[/bold]  "
                        f"{escape(first_line)}",
                        markup=True,
                    )
                )
            )

    @on(ListView.Selected, "#blocks-list")
    def block_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._blocks):
            _, code = self._blocks[idx]
            self.dismiss(("copy", code))

    def action_write_selected(self) -> None:
        listview = self.query_one("#blocks-list", ListView)
        idx = listview.index
        if idx is not None and 0 <= idx < len(self._blocks):
            _, code = self._blocks[idx]
            self.dismiss(("write", code))

    def action_cancel(self) -> None:
        self.dismiss(None)


class StatusLineScreen(ModalScreen[set[str] | None]):
    """Toggle which segments of the sidebar session panel are visible.

    Returns the new enabled-set on Save, or ``None`` on cancel. Used by
    ``/statusline``. Persistence is handled by the caller via
    :func:`glm_acp.config.save_statusline_config`.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    CSS = """
    StatusLineScreen { align: center middle; background: $background 70%; }
    #statusline-dialog {
        width: 78; max-width: 96%; height: auto; max-height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #statusline-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #statusline-toggles { height: auto; }
    .statusline-toggle {
        width: 100%; height: 1; margin-bottom: 0;
        background: $surface; color: $text; text-align: left;
    }
    .statusline-toggle.on { background: $accent 30%; color: $text; }
    #statusline-buttons { height: 3; align-horizontal: right; margin-top: 1; }
    #statusline-buttons Button { margin-left: 1; }
    #statusline-hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, enabled: set[str]) -> None:
        super().__init__()
        # Per-session working copy; the caller's set is not mutated.
        self._enabled: set[str] = set(enabled)

    def compose(self) -> ComposeResult:
        with Vertical(id="statusline-dialog"):
            yield Label("Sidebar segments", id="statusline-title")
            with Vertical(id="statusline-toggles"):
                for sid, label in STATUSLINE_SEGMENTS:
                    btn = Button(
                        f"{'[✓]' if sid in self._enabled else '[ ]'} {label}",
                        id=f"toggle-{sid}",
                        classes="statusline-toggle" + (" on" if sid in self._enabled else ""),
                    )
                    btn.variant = "success" if sid in self._enabled else "default"
                    yield btn
            yield Label(
                "Click to toggle  ·  Save persists to ~/.config/glm-acp/statusline.json",
                id="statusline-hint",
                markup=False,
            )
            with Horizontal(id="statusline-buttons"):
                yield Button("Save", id="statusline-save", variant="primary")
                yield Button("Cancel", id="statusline-cancel")

    @on(Button.Pressed)
    def toggle_pressed(self, event: Button.Pressed) -> None:
        # Toggle buttons share the ``toggle-<sid>`` id prefix.
        if event.button.id and event.button.id.startswith("toggle-"):
            sid = event.button.id.removeprefix("toggle-")
            if sid in self._enabled:
                self._enabled.discard(sid)
            else:
                self._enabled.add(sid)
            event.button.label = (
                f"{'[✓]' if sid in self._enabled else '[ ]'} {dict(STATUSLINE_SEGMENTS)[sid]}"
            )
            event.button.set_classes("statusline-toggle" + (" on" if sid in self._enabled else ""))
            event.button.variant = "success" if sid in self._enabled else "default"
        elif event.button.id == "statusline-save":
            self.dismiss(self._enabled)
        elif event.button.id == "statusline-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class KeybindsScreen(ModalScreen[dict[str, str] | None]):
    """Edit the persisted application keybinding overrides.

    ``{}`` is the explicit Reset-to-defaults result. A non-empty result maps
    stable Textual binding IDs to the requested key sequence.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    CSS = """
    KeybindsScreen { align: center middle; background: $background 70%; }
    #keybinds-dialog {
        width: 88; max-width: 96%; height: 34; max-height: 92%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #keybinds-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #keybinds-list { height: 1fr; }
    .keybind-row { height: 3; }
    .keybind-label { width: 1fr; padding-top: 1; }
    .keybind-input { width: 24; }
    #keybinds-buttons { height: 3; align-horizontal: right; margin-top: 1; }
    #keybinds-buttons Button { margin-left: 1; }
    #keybinds-hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, overrides: dict[str, str]) -> None:
        super().__init__()
        self._overrides = {
            action: keys for action, keys in overrides.items() if action in KEYBINDABLE_ACTION_IDS
        }

    def compose(self) -> ComposeResult:
        with Vertical(id="keybinds-dialog"):
            yield Label("Custom keybindings", id="keybinds-title")
            with VerticalScroll(id="keybinds-list"):
                for action, label, default_key in KEYBINDABLE_ACTIONS:
                    with Horizontal(classes="keybind-row"):
                        yield Label(label, classes="keybind-label")
                        yield Input(
                            self._overrides.get(action, default_key),
                            id=f"keybind-{action}",
                            classes="keybind-input",
                        )
            yield Label(
                "Use Textual key names (for example f2, ctrl+shift+k, or ctrl+j,space).",
                id="keybinds-hint",
                markup=False,
            )
            with Horizontal(id="keybinds-buttons"):
                yield Button("Reset defaults", id="keybinds-reset", variant="warning")
                yield Button("Save", id="keybinds-save", variant="primary")
                yield Button("Cancel", id="keybinds-cancel")

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "keybinds-reset":
            self.dismiss({})
            return
        if event.button.id == "keybinds-save":
            mapping = {
                action: self.query_one(f"#keybind-{action}", Input).value.strip()
                for action, _label, _default_key in KEYBINDABLE_ACTIONS
            }
            self.dismiss(mapping)
            return
        if event.button.id == "keybinds-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ContextBudgetScreen(ModalScreen[None]):
    """Visualize the per-segment context-window breakdown.

    Renders a horizontal bar chart of token usage by message-role segment
    (system prompt, user turns, assistant turns, tool results), with the
    total used / context-window size and a percentage. Used by ``/context``.
    Press ``c`` to dismiss and run ``/compact`` (which preserves the most
    recent turns and summarizes the rest), or Esc to close.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
        Binding("c", "compact", "Compact", priority=True),
    ]

    CSS = """
    ContextBudgetScreen { align: center middle; background: $background 70%; }
    #context-dialog {
        width: 86; max-width: 96%; height: auto; max-height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #context-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #context-summary { color: $text-muted; margin-bottom: 1; }
    .context-segment { height: auto; margin-bottom: 0; }
    .context-segment-label { width: 22; color: $text; }
    .context-segment-bar { color: $accent; }
    .context-segment-tokens { color: $text-muted; }
    #context-hint { color: $text-muted; margin-top: 1; }
    """

    # Each bar is rendered with this many cells at 100% context-window use.
    BAR_WIDTH = 40

    def __init__(self, breakdown: dict[str, Any]) -> None:
        super().__init__()
        self._breakdown = breakdown

    def compose(self) -> ComposeResult:
        with Vertical(id="context-dialog"):
            yield Label("Context window budget", id="context-title")
            yield Label(self._summary_line(), id="context-summary", markup=False)
            with VerticalScroll():
                for segment in self._breakdown.get("segments", []):
                    yield Static(self._segment_line(segment), markup=False)
            yield Label(
                "c compact older context  ·  Esc close",
                id="context-hint",
                markup=False,
            )

    def _summary_line(self) -> str:
        total = self._breakdown.get("total_tokens", 0)
        size = self._breakdown.get("context_size", 0)
        pct = self._breakdown.get("usage_percent", 0.0)
        if not size:
            return "Session not ready."
        return (
            f"{total:,} / {size:,} tokens  ·  {pct:g}% of context window  "
            f"·  {size - total:,} remaining"
        )

    def _segment_line(self, segment: dict[str, Any]) -> str:
        label = str(segment.get("label", "?"))
        count = int(segment.get("count", 0))
        tokens = int(segment.get("tokens", 0))
        pct = float(segment.get("percent_of_window", 0.0))
        # Bar: scale by share-of-window so the chart sums to total usage.
        cells = max(1, int(round(pct / 100.0 * self.BAR_WIDTH))) if pct > 0 else 0
        bar = "█" * cells + "·" * (self.BAR_WIDTH - cells)
        return f"{label:<22} {count:>3} msgs  {tokens:>7,} tok  {pct:>5.2f}%  {bar}"

    def action_compact(self) -> None:
        # Signal the caller to run /compact by dismissing with "compact".
        self.dismiss(None)
        app = self.app
        if isinstance(app, NativeGlmTui):
            app.call_later(app.run_compact_from_context_view)

    def action_cancel(self) -> None:
        self.dismiss(None)


class BtwOverlayScreen(ModalScreen[None]):
    """Ask a quick side question without polluting the main conversation.

    A small overlay with an Input for the question and a Static for the
    answer. The question is sent to the auxiliary GLM via
    ``GlmAcpAgent.ask_btw``; the answer is shown in the overlay but is
    NOT added to ``session.messages``. Used by ``/btw``.
    """

    BINDINGS = [Binding("escape", "cancel", "Close", priority=True)]

    CSS = """
    BtwOverlayScreen { align: center middle; background: $background 70%; }
    #btw-dialog {
        width: 80; max-width: 96%; height: auto; min-height: 12;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #btw-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #btw-input { margin-bottom: 1; }
    #btw-answer {
        height: auto; min-height: 3; max-height: 16;
        color: $text; border-top: solid $panel; padding-top: 1;
    }
    #btw-hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, prefill_question: str = "") -> None:
        super().__init__()
        self._prefill = prefill_question
        self._querying = False

    def compose(self) -> ComposeResult:
        with Vertical(id="btw-dialog"):
            yield Label("Side question (not added to the conversation)", id="btw-title")
            yield Input(
                placeholder="Ask a quick question…",
                id="btw-input",
                value=self._prefill,
            )
            yield Static(
                "(type a question and press Enter — the answer stays in this overlay)",
                id="btw-answer",
                markup=False,
            )
            yield Label("Enter ask  ·  Esc close", id="btw-hint", markup=False)

    def on_mount(self) -> None:
        input_widget = self.query_one("#btw-input", Input)
        input_widget.focus()
        # If the caller pre-filled a question (via `/btw <question>`), fire
        # the query immediately on mount.
        if self._prefill.strip():
            self.call_later(self._run_query, self._prefill.strip())

    @on(Input.Submitted, "#btw-input")
    async def question_submitted(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if not question or self._querying:
            return
        await self._run_query(question)

    async def _run_query(self, question: str) -> None:
        app = self.app
        if not isinstance(app, NativeGlmTui):
            return
        self._querying = True
        answer_widget = self.query_one("#btw-answer", Static)
        answer_widget.update("⏳ Asking the auxiliary model…")
        try:
            answer = await app.agent.ask_btw(app.session_id, question)
        except Exception as error:  # noqa: BLE001 — surface any failure in-overlay
            answer = f"Side question failed: {error}"
        finally:
            self._querying = False
        answer_widget.update(answer)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TasksScreen(ModalScreen[None]):
    """Consolidated session dashboard: turn state, queue, and session stats.

    A read-only modal that surfaces what the TUI is currently doing — the
    active turn state + elapsed time, the FIFO prompt queue with previews,
    and the session's model/mode/token stats — in one view. Used by
    ``/tasks``. Press Esc to close.
    """

    BINDINGS = [Binding("escape", "cancel", "Close", priority=True)]

    CSS = """
    TasksScreen { align: center middle; background: $background 70%; }
    #tasks-dialog {
        width: 82; max-width: 96%; height: auto; max-height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    #tasks-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .tasks-section-label {
        text-style: bold; color: $text; margin-top: 1; margin-bottom: 0;
    }
    .tasks-section-body { color: $text-muted; height: auto; margin-bottom: 0; }
    #tasks-hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, snapshot: dict[str, Any]) -> None:
        super().__init__()
        self._snapshot = snapshot

    def compose(self) -> ComposeResult:
        with Vertical(id="tasks-dialog"):
            yield Label("Session dashboard", id="tasks-title")
            yield Label("Current turn", classes="tasks-section-label")
            yield Label(self._turn_line(), classes="tasks-section-body", markup=False)
            yield Label("Queue", classes="tasks-section-label")
            yield Label(self._queue_line(), classes="tasks-section-body", markup=False)
            yield Label("Session", classes="tasks-section-label")
            yield Label(self._session_line(), classes="tasks-section-body", markup=False)
            yield Label("Esc close", id="tasks-hint", markup=False)

    def _turn_line(self) -> str:
        state = str(self._snapshot.get("turn_state", "Idle"))
        elapsed = float(self._snapshot.get("turn_elapsed", 0.0))
        activity = str(self._snapshot.get("activity", ""))
        if state == "Running":
            mins, secs = divmod(int(elapsed), 60)
            line = f"● Running ({mins}:{secs:02d})"
            if activity:
                line += f"  ·  {activity}"
            return line
        return f"● {state}"

    def _queue_line(self) -> str:
        queue = self._snapshot.get("queue", [])
        if not queue:
            return "(empty)"
        lines = [f"{len(queue)} queued prompt{'s' if len(queue) != 1 else ''}:"]
        for index, item in enumerate(queue[:5]):
            preview = str(item)[:60].replace("\n", " ")
            lines.append(f"  [{index + 1}] {preview}")
        if len(queue) > 5:
            lines.append(f"  (+{len(queue) - 5} more)")
        return "\n".join(lines)

    def _session_line(self) -> str:
        s = self._snapshot.get("session", {})
        model = str(s.get("model", "?"))
        mode = str(s.get("mode", "?"))
        perm = str(s.get("permission", "?"))
        inp = int(s.get("input_tokens", 0))
        out = int(s.get("output_tokens", 0))
        cached = int(s.get("cached_tokens", 0))
        context_pct = float(s.get("context_percent", 0.0))
        cap = int(s.get("max_iterations", 50))
        cache_str = f" · cache {cached:,}" if cached else ""
        return (
            f"{model} · {mode} · {perm}\n"
            f"tokens ↑{inp:,} ↓{out:,}{cache_str}\n"
            f"context {context_pct:g}% · iteration cap {cap}"
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


def _extract_message_text(msg: dict[str, Any]) -> str:
    """Best-effort plain-text extraction from a session message dict."""
    parts: list[str] = []
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif block.get("type") in {"tool_use", "function"}:
                    fn = block.get("function") or block.get("name") or ""
                    args = block.get("arguments") or block.get("input")
                    if isinstance(args, dict):
                        args = str(args)
                    parts.append(f"{fn}({args})")
            else:
                text_attr = getattr(block, "text", None)
                if isinstance(text_attr, str):
                    parts.append(text_attr)
    if msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            fn = (tc.get("function") or {}).get("name", "") if isinstance(tc, dict) else ""
            args = (tc.get("function") or {}).get("arguments", "") if isinstance(tc, dict) else ""
            parts.append(f"{fn}({args})")
    return "\n".join(p for p in parts if p)


class SelectableStatic(Static):
    """A ``Static`` whose text is exposed to Textual selection.

    Textual's default ``Widget.get_selection`` only returns text when the
    rendered content is a ``Text`` or ``Content`` object. When the content is
    a Rich renderable such as ``Markdown``, ``get_selection`` returns ``None``,
    which makes agent responses invisible to ``Ctrl+Shift+C`` and the
    Copy-selection menu entry.

    This subclass captures both:
    * ``_selectable_plain_text`` — the *rendered* plain text (markdown syntax
      stripped, lists/wrapping applied). Used for selection so users get what
      they see, not raw ``**bold**`` markers.
    * ``_selectable_raw_text`` — the raw markdown source. Returned only when
      the plain rendering is unavailable.
    """

    def __init__(self, *args: Any, raw_text: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._selectable_raw_text = raw_text
        self._selectable_plain_text = self._markdown_to_plain(raw_text)

    @staticmethod
    def _markdown_to_plain(text: str) -> str:
        """Render a markdown string to plain display text (no ANSI codes)."""
        if not text:
            return ""
        try:
            import io

            from rich.console import Console
            from rich.markdown import Markdown

            buf = io.StringIO()
            console = Console(
                file=buf,
                width=200,
                no_color=True,
                color_system=None,
                highlight=False,
                markup=False,
                soft_wrap=False,
            )
            console.print(Markdown(text))
            # Rich pads each line to the console width; strip per-line
            # trailing whitespace and collapse to single newlines.
            rendered = buf.getvalue()
            return "\n".join(line.rstrip() for line in rendered.splitlines()).rstrip("\n")
        except Exception:
            return text

    def update(self, renderable: Any = None, *, raw_text: str | None = None) -> None:  # type: ignore[override]
        """Render ``renderable`` while remembering both source and plain text."""
        if raw_text is not None:
            self._selectable_raw_text = raw_text
            self._selectable_plain_text = self._markdown_to_plain(raw_text)
        elif isinstance(renderable, str):
            self._selectable_raw_text = renderable
            self._selectable_plain_text = self._markdown_to_plain(renderable)
        super().update(renderable)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return text for selection, preferring the rendered plain text."""
        # Defer to the default behaviour for plain Text/Content renderables
        # (covers user messages, system messages, file-browser entries, etc.).
        # Only trust the default if it actually returns content; for a Static
        # whose current renderable is a RichMarkdown, ``_render()`` returns
        # something that is not Text/Content and the default yields ``""``.
        default = super().get_selection(selection)
        if default is not None and default[0]:
            return default
        text = self._selectable_plain_text or self._selectable_raw_text
        if not text:
            return None
        # SELECT_ALL (start/end None) returns the whole message. For partial
        # selections on a non-Text renderable, the offsets are screen-cell
        # coordinates that do not map cleanly to source characters — so we
        # also return the whole message. This is predictable and avoids
        # returning garbage slices like `` **world**``.
        return text, "\n"


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
        self.app._set_activity("Waiting for approval", tone="warning")
        try:
            allowed = await self.app.push_screen_wait(PermissionScreen(str(title), detail))
        finally:
            if self.app._prompt_worker is not None:
                self.app._set_activity(
                    f"Working · {self.app._bounded_activity_label(str(title))}",
                    active=True,
                )
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


class GlmCommandProvider(Provider):
    """Command-palette provider that surfaces every ``/``-command and F-key action.

    Registered on :class:`NativeGlmTui.COMMANDS` so the built-in ``Ctrl+P``
    palette (``App.COMMAND_PALETTE_BINDING``) lists our commands alongside
    Textual's system commands.

    Slash commands are inserted into the composer for review (the user can
    then add arguments and press Enter); F-key actions run immediately.
    """

    @classmethod
    def _app(cls, screen: Any) -> NativeGlmTui | None:
        app = getattr(screen, "app", None)
        return app if isinstance(app, NativeGlmTui) else None

    def _build_entries(
        self,
    ) -> list[tuple[str, str, Callable[[], None]]]:
        """Return ``(name, help_text, callback)`` tuples for palette entries."""
        app = self._app(self.screen)
        if app is None:
            return []

        entries: list[tuple[str, str, Callable[[], None]]] = []

        # Slash commands — insert into composer for review/argument-editing.
        # Iterate a snapshot so concurrent mutation of ``_slash_commands``
        # during a session cannot change the palette mid-render.
        for cmd in sorted(app._slash_commands):
            help_text = app._slash_commands.get(cmd, "")
            entries.append((cmd, help_text, app.make_insert_command_callback(cmd)))

        # F-key / Ctrl-* actions — invoke immediately. Wrap with
        # ``call_later`` so async actions are scheduled correctly and the
        # palette screen can dismiss before the action pushes a new screen.
        named_actions: list[tuple[str, str, str]] = [
            ("Help (F1)", "Show the help screen", "show_help"),
            ("Reasoning view (F2)", "Show or hide the reasoning panel", "toggle_thinking"),
            ("Settings (F3)", "Open the live session settings", "settings"),
            ("Working tree (F4)", "Cycle the working-tree panel", "toggle_working_tree"),
            ("Push to talk (F5)", "Toggle push-to-talk voice input", "toggle_voice"),
            ("History (F6)", "Browse and resume past sessions", "open_history"),
            ("Search (Ctrl-F)", "Grep the current conversation", "open_search"),
            ("Native mouse (F7)", "Toggle native terminal mouse mode", "toggle_native_mouse"),
            ("Copy response (Ctrl-Y)", "Copy the last agent response", "copy_last_response"),
            ("Clear view (Ctrl-L)", "Clear the visible transcript", "clear_transcript"),
            ("Quit (Ctrl-X)", "Close the terminal agent", "quit_agent"),
        ]
        for name, help_text, action in named_actions:
            entries.append((name, help_text, app.make_action_callback(action)))
        return entries

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_text, callback in self._build_entries():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help=help_text)

    async def discover(self) -> Hits:
        for name, help_text, callback in self._build_entries():
            yield DiscoveryHit(name, callback, help=help_text)


class NativeGlmTui(App[int]):
    """Full-screen coding-agent interface backed by one ``GlmAcpAgent``."""

    ACTIVITY_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    ACTIVITY_INTERVAL_SECONDS = 0.12
    ACTIVITY_HOLD_SECONDS = 1.6
    TITLE = "Native GLM ACP"
    SUB_TITLE = "Full harness terminal"
    # Enable Textual's built-in Ctrl+P command palette and register our
    # GlmCommandProvider so the palette surfaces every /-command and F-key
    # action alongside Textual's system commands.
    ENABLE_COMMAND_PALETTE = True
    COMMANDS = App.COMMANDS | {GlmCommandProvider}
    SHUTDOWN_TIMEOUT_SECONDS = 3.0
    # Make Textual's in-app text selection highly visible so users can see
    # what they are highlighting with click-drag (the default muted-purple
    # background is too subtle on dark themes).
    selection_style = Style(color="#ffffff", bgcolor="#1e4a82", bold=True)
    BINDINGS = [
        Binding("ctrl+x", "quit_agent", "Quit", priority=True, id="quit_agent"),
        Binding("f10", "quit_agent", "Quit", show=False, priority=True),
        # Ctrl-Q is swallowed by XON/XOFF flow control in many POSIX terminals.
        # Keep it as a hidden compatibility alias for terminals that deliver it.
        Binding("ctrl+q", "quit_agent", "Quit", show=False, priority=True),
        Binding("ctrl+c", "cancel_turn", "Cancel turn", priority=True, id="cancel_turn"),
        Binding("ctrl+l", "clear_transcript", "Clear view", priority=True, id="clear_transcript"),
        Binding("f1", "show_help", "Help", priority=True, id="show_help"),
        Binding("f2", "toggle_thinking", "Reasoning view", priority=True, id="toggle_thinking"),
        Binding("f3", "settings", "Settings", priority=True, id="settings"),
        Binding(
            "f4",
            "toggle_working_tree",
            "Working tree",
            priority=True,
            id="toggle_working_tree",
        ),
        Binding("f5", "toggle_voice", "Push to talk", priority=True, id="toggle_voice"),
        Binding("f6", "open_history", "History", priority=True, id="open_history"),
        Binding("ctrl+f", "open_search", "Search", priority=True, id="open_search"),
        Binding(
            "ctrl+y",
            "copy_last_response",
            "Copy response",
            priority=True,
            id="copy_last_response",
        ),
        Binding(
            "ctrl+shift+c",
            "copy_selection",
            "Copy selection",
            show=False,
            priority=True,
            id="copy_selection",
        ),
        # Native mouse mode toggle. When ON, Textual releases mouse capture
        # back to the terminal emulator so the user's native right-click
        # context menu and click-drag selection work (Codex/Claude-Code
        # approach). When OFF (default), Textual handles all mouse events.
        Binding(
            "f7",
            "toggle_native_mouse",
            "Native mouse",
            show=False,
            priority=True,
            id="toggle_native_mouse",
        ),
        # Screen-reader mode toggle. When ON, agent messages render as plain
        # text instead of Rich Markdown (avoiding ANSI/styling sequences that
        # trip up screen readers), the activity status line stops animating,
        # and the preference persists across sessions. Toggle via F8 or
        # ``/screen-reader``; force on at startup via GLM_ACP_SCREEN_READER=1.
        Binding(
            "f8",
            "toggle_screen_reader",
            "Screen reader",
            show=False,
            priority=True,
            id="toggle_screen_reader",
        ),
        Binding("f9", "toggle_vim", "Vim mode", show=False, priority=True),
    ]

    CSS = """
    Screen { layout: vertical; background: #0b1017; }
    Header { background: #111a24; color: #d7e3f4; }
    #workspace { height: 1fr; }
    #conversation { width: 1fr; }
    #working-tree-panel {
        width: 34; min-width: 24; border: round #4a9ee6; padding: 0 1;
        background: #0c1118;
    }
    #working-tree-panel.hidden { display: none; }
    #wt-switcher { height: 1fr; }
    #wt-tabs {
        height: 1; dock: bottom; background: #111a24; color: #7f96ab;
        padding: 0 1;
    }
    #transcript {
        height: 1fr; border: round #2589d8; padding: 0 1;
        background: #0d131b;
    }
    #thinking {
        height: 12; min-height: 6; border: round #8a5fd3; padding: 0 1;
        background: #10131c;
    }
    #thinking.hidden { display: none; }
    #sidebar {
        width: 32; min-width: 26; border: round #d29a32; padding: 0 1;
        background: #0c1118;
    }
    #session { height: auto; padding: 0 0 1 0; color: #c8d6e5; }
    #tools {
        height: 1fr; min-height: 7; border-top: solid #66502a;
        border-bottom: solid #66502a; color: #aebdca;
    }
    #plan { height: auto; max-height: 10; overflow-y: auto; padding-top: 1; }
    #command-menu {
        display: none; height: auto; max-height: 14; margin: 0 1;
        border: round #36a3f7; background: #111a24; color: #d9e7f5;
    }
    #command-menu.visible { display: block; }
    #command-hint {
        display: none; height: 1; margin: 0 2; color: #7f96ab;
        background: #0b1017;
    }
    #command-hint.visible { display: block; }
    #activity-status {
        height: 1; margin: 0 2; color: #85c8ff;
        background: #0b1017;
    }
    #queue-status {
        height: 1; margin: 0 2; color: #f6c85f;
        background: #0b1017;
    }
    #composer {
        height: 3; margin: 0 1; border: tall #2589d8;
        background: #111a24;
    }
    Footer { background: #111a24; }
    .welcome {
        margin: 1 3; padding: 1 2; border-left: thick #36a3f7;
        background: #111a24;
    }
    .user-message { margin: 1 1 0 8; padding: 1; background: #12314b; }
    .agent-message { margin: 1 8 0 1; padding: 1; background: #171d26; }
    .system-message { margin: 1 4; color: $text-muted; }
    """

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        agent_factory: Callable[[], GlmAcpAgent] = GlmAcpAgent,
    ) -> None:
        super().__init__()
        # Textual builds the application binding map during ``App.__init__``.
        # Keep an untouched copy so Reset defaults can take effect immediately
        # without waiting for the next process launch.
        self._default_keybindings = self._bindings.copy()
        self.args = args
        self.agent = agent_factory()
        self.client = TuiClient(self)
        self.session_id = ""
        self._agent_ready = False
        # Do not use MessagePump._closing: Textual owns it and setting it before
        # App.exit() prevents the queued ExitApp message from being processed.
        self._shutdown_requested = False
        self._agent_closed = False
        self._prompt_worker: Worker[None] | None = None
        self._prompt_queue: list[str] = []
        self._wt_visible: bool = False
        self._wt_view: int = 0
        self._recorder: object | None = None
        self._turn_start_time: float = time.monotonic()
        self._replaying = False
        self._current_agent: Static | None = None
        self._current_agent_text = ""
        self._agent_responses: list[str] = []
        self._last_agent_render: float = 0.0
        self._thinking_text = ""
        self._pending_images = list(args.image)
        self._slash_commands = dict(LOCAL_COMMANDS)
        self._keybind_overrides = load_keybinds_config()
        vim_env = os.environ.get("GLM_ACP_VIM", "").strip().lower()
        self._vim_enabled = vim_env in {"1", "true", "yes", "on"} or load_vim_config()
        self._command_values: list[str] = []
        self._provider_usage: PlanUsage | None = None
        self._provider_usage_error = ""
        animation_setting = os.environ.get("GLM_ACP_TUI_ANIMATION", "1")
        self._activity_animation_enabled = animation_setting.strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        # Screen-reader mode: when True, agent messages render as plain text
        # (no Rich Markdown), the activity status line stops animating, and
        # the user gets a screen-reader-friendlier transcript. Loaded from
        # ``config_dir()/screen-reader.json`` but forced on by
        # ``GLM_ACP_SCREEN_READER=1`` regardless of saved state.
        screen_reader_env = os.environ.get("GLM_ACP_SCREEN_READER", "").strip().lower()
        self._screen_reader = (
            screen_reader_env in {"1", "true", "yes", "on"} or load_screen_reader_config()
        )
        if self._screen_reader:
            # Activity animation relies on glyph cycling, which is hostile
            # to assistive tech — disable it whenever screen-reader is on.
            self._activity_animation_enabled = False
        self._activity_timer: Timer | None = None
        self._activity_frame = 0
        self._activity_label = "Starting session"
        self._activity_tone = "active"
        self._activity_active = False
        self._activity_started = time.monotonic()
        self._activity_hold_until: float | None = None
        # Native mouse mode: when True, Textual has released mouse capture
        # back to the terminal emulator so the terminal's own right-click
        # context menu and click-drag text selection work natively.
        self._native_mouse_mode = False
        # Statusline segments enabled by the user (loaded from
        # ``config_dir()/statusline.json``). ``_refresh_session_panel``
        # only renders segments whose IDs are in this set; defaults to
        # all visible on first run.
        self._statusline_segments: set[str] = load_statusline_config()
        # Persisted Textual theme name (loaded from ``config_dir()/theme.json``).
        # Applied on mount once the App is running; subsequent user changes
        # via ``/theme`` are persisted by ``watch_theme``.
        self._saved_theme: str | None = load_theme_config()
        self._theme_persist_scheduled = False
        # /loop state: an in-session ad-hoc prompt iterator (distinct from
        # the persistent cron subsystem). When active, _loop_timer fires
        # every _loop_interval_seconds and submits _loop_prompt.
        self._loop_timer: Timer | None = None
        self._loop_prompt: str = ""
        self._loop_interval_seconds: float = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="workspace"):
            with Vertical(id="working-tree-panel", classes="hidden"):
                yield ContentSwitcher(
                    VerticalScroll(id="wt-changes"),
                    VerticalScroll(id="wt-git"),
                    VerticalScroll(id="wt-diff"),
                    VerticalScroll(id="wt-files"),
                    VerticalScroll(id="wt-github"),
                    initial="wt-changes",
                    id="wt-switcher",
                )
                yield Static(
                    "[1]Changes [2]Git [3]Diff [4]Files [5]GitHub  (F4)",
                    id="wt-tabs",
                    markup=False,
                )
            with Vertical(id="conversation"):
                yield VerticalScroll(id="transcript")
                yield RichLog(
                    id="thinking",
                    classes="hidden",
                    wrap=True,
                    markup=False,
                    auto_scroll=True,
                )
            with Vertical(id="sidebar"):
                yield Static("Starting…", id="session", markup=False)
                yield RichLog(id="tools", wrap=True, markup=True, auto_scroll=True)
                yield Static("No active plan", id="plan", markup=False)
        yield OptionList(id="command-menu", compact=True)
        yield Static(
            "↑↓ navigate  ·  Enter run/select  ·  Tab complete  ·  Esc close",
            id="command-hint",
            markup=False,
        )
        yield Static("◌ Starting session", id="activity-status", markup=False)
        yield Static("", id="queue-status", markup=False)
        composer_type = ModalComposer if self._vim_enabled else CommandInput
        yield composer_type(
            placeholder="Ask Native GLM ACP… (/help for commands)",
            id="composer",
            disabled=True,
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#transcript").border_title = "Conversation"
        self.query_one("#thinking").border_title = "Reasoning"
        self.query_one("#sidebar").border_title = "Session"
        self.query_one("#tools").border_title = "Activity"
        self.query_one("#command-menu").border_title = "Commands"
        self.query_one("#tools", RichLog).write("[dim]Waiting for tool activity…[/dim]")
        if self._vim_enabled:
            self.query_one("#composer", ModalComposer).set_mode("normal")
        self._apply_keybind_overrides(self._keybind_overrides)
        # Apply persisted theme (if any) now that the App is running.
        if self._saved_theme and self._saved_theme in self.available_themes:
            try:
                self.theme = self._saved_theme
            except Exception:
                pass
        self._activity_timer = self.set_interval(
            self.ACTIVITY_INTERVAL_SECONDS,
            self._advance_activity_animation,
            name="tui-activity-animation",
            pause=True,
        )
        self._set_activity("Starting session", active=True)
        self.agent.on_connect(self.client)
        self.initialize_agent()
        # Honor GLM_ACP_NATIVE_MOUSE=1: start with mouse capture released
        # so the terminal emulator handles right-click + selection natively
        # (Codex/Claude-Code style). The driver is not yet available during
        # __init__, so the toggle is deferred to the next refresh.
        if os.environ.get("GLM_ACP_NATIVE_MOUSE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            self.call_after_refresh(self.action_toggle_native_mouse)

    def watch_theme(self, theme_name: str) -> None:
        """Persist user-initiated theme changes to ``config_dir()/theme.json``.

        Skipped during the initial load (when ``_saved_theme`` is being
        applied via ``on_mount``) to avoid writing back what we just read.
        Subsequent user changes via ``/theme`` or the Ctrl+P palette flow
        through here and are saved.
        """
        if theme_name and theme_name != self._saved_theme:
            try:
                save_theme_config(theme_name)
            except OSError:
                pass
        self._saved_theme = theme_name

    @work(exclusive=True, group="agent-initialize")
    async def initialize_agent(self) -> None:
        from .terminal_cli import _configure

        try:
            await self.agent.initialize(
                protocol_version=1,
                client_info={"name": "glm-acp-tui"},
                client_capabilities={"terminal": True},
            )
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
            self._set_activity("Ready", tone="ready")
            await self._append_welcome()
            self.refresh_provider_usage(silent=True)
        except Exception as error:
            await self._append_system(f"Startup failed: {error}")
            self._refresh_session_panel("Startup failed")
            self._set_activity("Startup failed", tone="error")

    @on(Input.Submitted, "#composer")
    async def submit_input(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        completion = self.accept_command_completion()
        if completion == "expanded":
            return
        if completion == "selected":
            text = event.input.value.strip()
        if not text or not self._agent_ready:
            return
        event.input.clear()
        if text.startswith("/planmode ") and self._agent_ready:
            prd = text.partition(" ")[2].strip()
            if prd:
                await self.agent.set_session_mode(mode_id="plan", session_id=self.session_id)
                self.notify("Plan Mode activated — read-only research mode", severity="information")
                text = prd
            else:
                return
        if await self._handle_local_command(text):
            return
        if text in {"/exit", "/quit"}:
            await self.action_quit_agent()
            return
        if text.startswith("/image "):
            path = text.partition(" ")[2].strip()
            if path:
                self._pending_images.append(path)
                await self._append_system(f"Queued image for the next prompt: {path}")
            return
        if self._prompt_worker is not None:
            self._prompt_queue.append(text)
            self._refresh_queue_display()
            return
        await self._append_user(text)
        self._current_agent = None
        self._current_agent_text = ""
        self._thinking_text = ""
        self.query_one("#thinking", RichLog).clear()
        self._refresh_session_panel("Running")
        self._set_activity("Thinking", active=True)
        self._prompt_worker = self.run_prompt(text, list(self._pending_images))
        self._pending_images.clear()

    @on(Input.Changed, "#composer")
    def composer_changed(self, event: Input.Changed) -> None:
        if event.value == event.input.value:
            self.refresh_command_menu(event.value)

    @on(OptionList.OptionSelected, "#command-menu")
    async def command_selected(self, event: OptionList.OptionSelected) -> None:
        result = self.accept_command_completion(index=event.option_index)
        if result == "selected":
            await self.query_one("#composer", CommandInput).action_submit()

    async def _handle_local_command(self, text: str) -> bool:
        """Handle presentation-only commands without entering the model loop."""
        if text in {"/reasoning-panel", "/toggle-thinking"}:
            self.action_toggle_thinking()
            return True
        if text == "/usage":
            self.refresh_provider_usage(silent=False)
            return True
        if text == "/settings":
            self.action_settings()
            return True
        if text == "/native-mouse":
            # Codex/Claude-Code approach: get out of the terminal's way.
            # Toggle whether Textual captures mouse events. When disabled,
            # the terminal emulator handles right-click (native context
            # menu) and click-drag (native selection → OS clipboard).
            self.action_toggle_native_mouse()
            return True
        if text.startswith("/max-iterations"):
            arg = text.partition(" ")[2].strip()
            session = getattr(self.agent, "_sessions", {}).get(self.session_id)
            if session is None:
                self.notify("Session not ready", severity="warning")
                return True
            current = getattr(session, "max_tool_iterations", 50)
            if not arg:
                self.notify(
                    f"Current tool-call iteration cap: {current} per turn "
                    "(use /max-iterations <N> to set)",
                    title="Max iterations",
                    severity="information",
                )
                return True
            try:
                new_cap = int(arg)
            except ValueError:
                self.notify(
                    f"Invalid value: {arg!r} — must be an integer",
                    severity="error",
                )
                return True
            # set_config_option signature is (config_id, session_id, value).
            # It clamps to [1, 1000] and persists on session.
            await self.agent.set_config_option("max_tool_iterations", self.session_id, str(new_cap))
            actual = session.max_tool_iterations
            self.notify(
                f"Tool-call iteration cap: {current} → {actual}",
                title="Max iterations updated",
                severity="success",
            )
            return True
        if text == "/clear-view":
            await self.action_clear_transcript()
            self.notify("Transcript view cleared", severity="information")
            return True
        if text == "/recap":
            recap = await self.agent.generate_recap(self.session_id)
            await self._append_system(f"Recap: {recap}")
            self.notify(recap, title="Session recap", severity="information")
            return True
        if text == "/blocks":
            await self.action_open_blocks_picker()
            return True
        if text == "/statusline":
            await self.action_open_statusline()
            return True
        if text == "/keybinds":
            await self.action_open_keybinds()
            return True
        if text == "/vim":
            await self.action_toggle_vim()
            return True
        if text == "/annotate":
            await self.action_annotate()
            return True
        if text == "/context":
            await self.action_open_context()
            return True
        if text == "/btw" or text.startswith("/btw "):
            question = text.partition(" ")[2].strip()
            await self.action_open_btw(question)
            return True
        if text == "/theme":
            self.action_change_theme()
            return True
        if text == "/tasks":
            await self.action_open_tasks()
            return True
        if text == "/insights":
            insights = await self.agent.generate_insights(self.session_id)
            await self._append_system(f"Insights:\n{insights}")
            self.notify("Insights generated — see transcript", severity="information")
            return True
        if text == "/loop" or text.startswith("/loop "):
            arg = text.partition(" ")[2].strip()
            await self.action_loop(arg)
            return True
        if text == "/security-review":
            review = await self.agent.security_review(self.session_id)
            await self._append_system(review)
            self.notify("Security review complete — see transcript", severity="information")
            return True
        if text == "/rewind" or text.startswith("/rewind "):
            # Alias for /rollback — insert into composer for review.
            arg = text.partition(" ")[2].strip()
            cmd = f"/rollback {arg}" if arg else "/rollback"
            self.insert_command(cmd)
            return True
        if text == "/smart" or text.startswith("/smart "):
            name = text.partition(" ")[2].strip()
            await self.action_smart(name)
            return True
        if text == "/sound":
            from .voice import is_sound_enabled, set_sound_enabled

            new_state = set_sound_enabled(not is_sound_enabled())
            state_str = "on" if new_state else "off"
            self.notify(
                f"Notification sounds: {state_str}",
                title="Sound toggle",
                severity="success" if new_state else "information",
            )
            return True
        if text == "/screen-reader":
            self.action_toggle_screen_reader()
            return True
        if text == "/copy" or text == "/copy last":
            await self._copy_response(None)
            return True
        if text.startswith("/copy "):
            arg = text.partition(" ")[2].strip()
            if arg.isdigit():
                await self._copy_response(int(arg))
                return True
            if arg == "all":
                await self._copy_all_responses()
                return True
        if text == "/export last":
            await self._export_last_response()
            return True
        if text == "/history":
            await self.action_open_history()
            return True
        if text == "/search":
            await self.action_open_search()
            return True
        if text == "/undo" or text.startswith("/undo "):
            await self._handle_undo(text)
            return True
        if text == "/prompt":
            await self._handle_prompt_editor()
            return True
        if text == "/journey":
            await self._handle_journey()
            return True
        if text == "/export":
            await self._export_session("")
            return True
        if text.startswith("/export "):
            await self._export_session(text[len("/export ") :].strip())
            return True
        command, separator, argument = text.partition(" ")
        if command in CONFIG_COMMANDS:
            if not separator or not argument.strip():
                composer = self.query_one("#composer", CommandInput)
                composer.value = f"{command} "
                composer.cursor_position = len(composer.value)
                self.refresh_command_menu(composer.value)
                return True
            await self._apply_inline_config(command, argument.strip())
            return True
        return False

    async def _apply_inline_config(self, command: str, value: str) -> None:
        choices = {item for item, _description in self._configuration_values(command)}
        _config_id, label = CONFIG_COMMANDS[command]
        if value not in choices:
            await self._append_system(
                f"Unknown {label.lower()} value: {value}. Available: {', '.join(sorted(choices))}"
            )
            return
        if command == "/mode":
            await self.agent.set_session_mode(mode_id=value, session_id=self.session_id)
        else:
            config_id = CONFIG_COMMANDS[command][0]
            await self.agent.set_config_option(
                config_id=config_id,
                session_id=self.session_id,
                value=value,
            )
        session = self.agent._sessions[self.session_id]
        actual = value if command == "/mode" else str(getattr(session, CONFIG_COMMANDS[command][0]))
        self._refresh_session_panel("Ready")
        await self._append_system(f"✓ {label}: {self._display_config_value(command, actual)}")
        if CONFIG_COMMANDS[command][0] == "api_endpoint":
            self.refresh_provider_usage(silent=True)

    def _configuration_values(self, command: str) -> list[tuple[str, str]]:
        session = self.agent._sessions.get(self.session_id)
        if session is None:
            return []
        config_id = CONFIG_COMMANDS.get(command, ("", ""))[0]
        values: list[tuple[str, str]]
        if config_id == "model":
            # Synthetic MoA entry at the top, matching the ACP-side picker
            # (Hermes v0.18 picker parity).
            values = [
                (
                    MOA_PICKER_VALUE,
                    "🔬 Mixture of Agents — toggle the MoA council layer",
                )
            ]
            values.extend(
                (
                    key,
                    f"{info['name']} — {info['description']} ({info['context_window']} context)",
                )
                for key, info in MODELS.items()
                if session.api_endpoint in info.get("plans", [])
            )
        elif config_id == "thought_level":
            values = [
                (
                    key,
                    f"{THOUGHT_LEVELS[key]['name']} — {THOUGHT_LEVELS[key]['description']}",
                )
                for key in thought_levels_for_model(session.model)
            ]
        elif config_id == "permission_mode":
            values = [
                ("ask", "Ask before changes"),
                ("read", "Read Only"),
                ("bypass", "Bypass"),
            ]
        elif config_id == "session_mode":
            values = [("ask", "Ask / explain"), ("code", "Code / act")]
        elif config_id == "api_endpoint":
            values = [
                (key, f"{info['name']} — {info['description']}")
                for key, info in API_ENDPOINTS.items()
            ]
        elif config_id == "generation_profile":
            values = [
                (key, f"{info['name']} — {info['description']}")
                for key, info in GENERATION_PROFILES.items()
            ]
        elif config_id == "auxiliary_model":
            values = [(DEFAULT_AUXILIARY_MODEL, "Use main model")]
            values.extend(
                (key, str(MODELS[key]["name"]))
                for key, info in MODELS.items()
                if session.api_endpoint in info.get("plans", []) and key not in VISION_MODELS
            )
        elif config_id == "mixture_mode":
            values = [("off", "Off"), ("enabled", "Reference review")]
        else:
            return []
        current = self._current_config_value(command)
        return [
            (item, f"{description} · current" if item == current else description)
            for item, description in values
        ]

    def _current_config_value(self, command: str) -> str:
        session = self.agent._sessions.get(self.session_id)
        if session is None:
            return ""
        if command == "/mode":
            return str(session.mode)
        config_id = CONFIG_COMMANDS.get(command, ("", ""))[0]
        return str(getattr(session, config_id, ""))

    @staticmethod
    def _display_config_value(command: str, value: str) -> str:
        config_id = CONFIG_COMMANDS.get(command, ("", ""))[0]
        if config_id == "api_endpoint":
            return str(API_ENDPOINTS.get(value, {}).get("name", value))
        if config_id == "model":
            if value == MOA_PICKER_VALUE:
                return "🔬 Mixture of Agents"
            return str(MODELS.get(value, {}).get("name", value))
        if config_id == "thought_level":
            return str(THOUGHT_LEVELS.get(value, {}).get("name", value))
        if config_id == "generation_profile":
            return str(GENERATION_PROFILES.get(value, {}).get("name", value))
        if config_id == "auxiliary_model" and value == DEFAULT_AUXILIARY_MODEL:
            return "Use main model"
        return value

    def refresh_command_menu(self, value: str) -> None:
        menu = self.query_one("#command-menu", OptionList)
        if not value.startswith("/") or self._prompt_worker is not None:
            self.hide_command_menu()
            return
        command, separator, argument = value.partition(" ")
        choices: list[tuple[str, str]]
        if command in CONFIG_COMMANDS and separator:
            query = argument.strip().lower()
            available = [
                (f"{command} {item}", description)
                for item, description in self._configuration_values(command)
            ]
            prefix_matches = [
                item for item in available if item[0].partition(" ")[2].lower().startswith(query)
            ]
            choices = (
                available
                if not query
                else prefix_matches or [item for item in available if query in item[1].lower()]
            )
        elif separator:
            self.hide_command_menu()
            return
        else:
            query = value.lower()
            available = [
                (
                    name,
                    (
                        f"{description} · current "
                        f"{self._display_config_value(name, self._current_config_value(name))}"
                        if name in CONFIG_COMMANDS
                        else description
                    ),
                )
                for name, description in self._slash_commands.items()
            ]
            exact_matches = [item for item in available if item[0] == query]
            prefix_matches = [item for item in available if item[0].startswith(query)]
            choices = (
                available
                if query == "/"
                else exact_matches
                or prefix_matches
                or [item for item in available if query[1:] in item[1].lower()]
            )
        choices = choices[:50]
        if not choices:
            self.hide_command_menu()
            return
        self._command_values = [choice for choice, _description in choices]
        menu.set_options(
            [
                Option(
                    Text.assemble(
                        (choice, "bold #62b5f5"),
                        "  ",
                        (
                            description
                            if len(description) <= 88
                            else description[:87].rstrip() + "…",
                            "dim",
                        ),
                    ),
                    id=f"choice-{index}",
                )
                for index, (choice, description) in enumerate(choices)
            ]
        )
        menu.highlighted = 0
        menu.add_class("visible")
        self.query_one("#command-hint").add_class("visible")

    def hide_command_menu(self) -> None:
        self.query_one("#command-menu").remove_class("visible")
        self.query_one("#command-hint").remove_class("visible")
        self._command_values = []

    def move_command_highlight(self, direction: int) -> None:
        menu = self.query_one("#command-menu", OptionList)
        if not menu.has_class("visible"):
            return
        if direction < 0:
            menu.action_cursor_up()
        else:
            menu.action_cursor_down()

    def accept_command_completion(
        self, *, index: int | None = None, submit: bool = True
    ) -> str | None:
        menu = self.query_one("#command-menu", OptionList)
        if not menu.has_class("visible") or not self._command_values:
            return None
        selected_index = menu.highlighted if index is None else index
        if selected_index is None or not 0 <= selected_index < len(self._command_values):
            return None
        value = self._command_values[selected_index]
        composer = self.query_one("#composer", CommandInput)
        if value in CONFIG_COMMANDS:
            composer.value = f"{value} "
            composer.cursor_position = len(composer.value)
            self.refresh_command_menu(composer.value)
            return "expanded"
        composer.value = value
        composer.cursor_position = len(value)
        if not submit:
            self.refresh_command_menu(value)
            return "expanded"
        self.hide_command_menu()
        return "selected"

    @work(exclusive=True, group="agent-prompt", exit_on_error=False)
    async def run_prompt(self, text: str, images: list[str]) -> None:
        from .terminal_cli import _prompt_blocks

        outcome = "completed"
        try:
            while True:
                self._turn_start_time = time.monotonic()
                try:
                    await self.agent.prompt(
                        prompt=_prompt_blocks(text, images),
                        session_id=self.session_id,
                        message_id=str(uuid4()),
                    )
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    await self._append_system("Turn cancelled.")
                    raise
                except Exception as error:
                    outcome = "failed"
                    await self._append_system(f"Turn failed: {error}")
                    return

                if self._current_agent is not None and self._current_agent_text:
                    self._current_agent.update(self._render_agent_content(self._current_agent_text))
                    self._last_agent_render = time.monotonic()

                if not self._prompt_queue or self._shutdown_requested:
                    return

                text = self._prompt_queue.pop(0)
                images = []
                self._refresh_queue_display()
                await self._append_user(text)
                self._current_agent = None
                self._current_agent_text = ""
                self._thinking_text = ""
                self.query_one("#thinking", RichLog).clear()
                self._refresh_session_panel("Running from queue")
                self._set_activity("Thinking", active=True)
        finally:
            self._prompt_worker = None
            if not self._shutdown_requested:
                from .voice import play_sound, send_notification

                turn_duration = time.monotonic() - self._turn_start_time
                if outcome == "completed":
                    play_sound("success")
                    send_notification("GLM ACP", "Task completed", turn_duration=turn_duration)
                elif outcome == "failed":
                    play_sound("error")
                    send_notification(
                        "GLM ACP", "Turn failed", error=True, turn_duration=turn_duration
                    )
                elif outcome == "cancelled":
                    play_sound("warning")

                composer = self.query_one("#composer", Input)
                composer.focus()
                self._refresh_session_panel("Ready")
                if outcome == "completed":
                    self._set_activity(
                        "Completed",
                        tone="success",
                        hold=self.ACTIVITY_HOLD_SECONDS,
                    )
                elif outcome == "cancelled":
                    self._set_activity(
                        "Cancelled",
                        tone="warning",
                        hold=self.ACTIVITY_HOLD_SECONDS,
                    )
                else:
                    self._set_activity(
                        "Turn failed",
                        tone="error",
                        hold=self.ACTIVITY_HOLD_SECONDS,
                    )

    @work(exclusive=True, group="provider-usage", exit_on_error=False)
    async def refresh_provider_usage(self, *, silent: bool) -> None:
        """Refresh provider-reported quota data without blocking the composer."""
        try:
            usage = await self._query_provider_usage()
        except Exception as error:
            self._provider_usage = None
            self._provider_usage_error = str(error)[:300]
            self._refresh_session_panel("Running" if self._prompt_worker is not None else "Ready")
            if not silent:
                await self._append_system(
                    f"Coding Plan usage is unavailable: {self._provider_usage_error}"
                )
            return
        self._provider_usage = usage
        self._provider_usage_error = ""
        self._refresh_session_panel("Running" if self._prompt_worker is not None else "Ready")
        if not silent:
            rendered = self.agent.format_provider_usage(usage).replace("**", "").replace("_", "")
            await self._append_system(rendered)

    async def _query_provider_usage(self) -> PlanUsage:
        """Run synchronous DNS/HTTP away from the UI loop in a daemon thread."""
        sync_query = getattr(self.agent, "query_provider_usage_sync", None)
        if not callable(sync_query):
            return await self.agent.query_provider_usage(self.session_id)

        loop = asyncio.get_running_loop()
        result: asyncio.Future[PlanUsage] = loop.create_future()

        def resolve() -> None:
            try:
                usage = sync_query(self.session_id)
            except Exception as error:
                outcome: tuple[PlanUsage | None, Exception | None] = (None, error)
            else:
                outcome = (usage, None)

            def deliver() -> None:
                if result.done():
                    return
                usage_value, error_value = outcome
                if error_value is not None:
                    result.set_exception(error_value)
                else:
                    assert usage_value is not None
                    result.set_result(usage_value)

            try:
                loop.call_soon_threadsafe(deliver)
            except RuntimeError:
                pass

        threading.Thread(
            target=resolve,
            name="glm-acp-provider-usage",
            daemon=True,
        ).start()
        return await result

    async def handle_session_update(self, update: Any) -> None:
        kind = str(getattr(update, "session_update", ""))
        if kind == "available_commands_update":
            for command in getattr(update, "available_commands", []):
                name = "/" + str(getattr(command, "name", "")).lstrip("/")
                description = str(getattr(command, "description", "") or "Harness command")
                if name != "/":
                    self._slash_commands[name] = description
        elif kind == "agent_message_chunk":
            if self._prompt_worker is not None:
                self._set_activity("Responding", active=True)
            await self._append_agent(self._content_text(update))
        elif kind == "user_message_chunk" and self._replaying:
            await self._append_user(self._content_text(update), history=True)
        elif kind == "agent_thought_chunk":
            if self._prompt_worker is not None:
                self._set_activity("Reasoning", active=True)
            text = self._content_text(update)
            self._thinking_text += text
            self.query_one("#thinking", RichLog).write(text, scroll_end=True)
        elif kind in {"tool_call", "tool_call_update"}:
            self.client.remember_tool(update)
            tool_call_id = str(getattr(update, "tool_call_id", ""))
            title = getattr(update, "title", None) or self.client._tool_titles.get(tool_call_id)
            status = getattr(update, "status", None)
            if title:
                if self._prompt_worker is not None:
                    self._set_activity(
                        f"Working · {self._bounded_activity_label(str(title))}",
                        active=True,
                    )
                self.query_one("#tools", RichLog).write(
                    f"[bold]{escape(str(title))}[/bold] · {escape(str(status or 'tool'))}",
                    scroll_end=True,
                )
            content_items = getattr(update, "content", None)
            if content_items:
                for item in content_items:
                    text = self._extract_tool_content_text(item)
                    if text:
                        for line in text.strip().splitlines()[:25]:
                            self.query_one("#tools", RichLog).write(
                                f"  [dim]{escape(line)}[/dim]",
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
        elif kind == "current_mode_update":
            mode_id = str(getattr(update, "current_mode_id", ""))
            if mode_id:
                session = self.agent._sessions.get(self.session_id)
                if session is not None:
                    session.mode = mode_id
                self._refresh_session_panel(
                    "Running" if self._prompt_worker is not None else "Ready"
                )
        elif kind == "session_info_update":
            self.sub_title = str(getattr(update, "title", "Full harness terminal"))

    @staticmethod
    def _content_text(update: Any) -> str:
        return str(getattr(getattr(update, "content", None), "text", ""))

    @staticmethod
    def _extract_tool_content_text(item: Any) -> str:
        """Extract readable text from a ToolCallContent item."""
        inner = getattr(item, "content", None)
        if inner is None:
            text = getattr(item, "text", None)
            return str(text) if text else ""
        if isinstance(inner, str):
            return inner
        parts: list[str] = []
        if isinstance(inner, (list, tuple)):
            for block in inner:
                text = getattr(block, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(parts)

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
            if self._current_agent_text:
                self._agent_responses.append(self._current_agent_text)
            self._current_agent_text = ""
            # Use SelectableStatic so Ctrl+Shift+C and the Copy-selection menu
            # entry can extract the raw markdown source (Static.get_selection
            # returns None for Rich renderables like Markdown).
            self._current_agent = SelectableStatic("", classes="agent-message", markup=False)
            await transcript.mount(self._current_agent)
        self._current_agent_text += text
        now = time.monotonic()
        if now - self._last_agent_render > 0.12:
            self._last_agent_render = now
            self._current_agent.update(
                self._render_agent_content(self._current_agent_text),
                raw_text=self._current_agent_text,
            )
        transcript.scroll_end(animate=False)

    async def _append_system(self, text: str) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(Static(text, classes="system-message", markup=False))
        transcript.scroll_end(animate=False)

    async def _append_welcome(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(
            Static(
                RichMarkdown(
                    "### Native GLM ACP\n"
                    "Full coding-agent runtime is ready. Type **`/`** for commands, "
                    "**`/plan`** to switch APIs, **`/thinking`** for reasoning depth, "
                    "or **F3** for all settings."
                ),
                classes="welcome",
                markup=False,
            )
        )
        transcript.scroll_end(animate=False)

    def _refresh_session_panel(self, state: str, *, used: int = 0, size: int = 0) -> None:
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        model = getattr(session, "model", self.args.model or "default")
        reasoning = getattr(session, "thought_level", "default")
        endpoint = getattr(session, "api_endpoint", "default")
        permission = getattr(session, "permission_mode", self.args.permission or "ask")
        mode = getattr(session, "mode", self.args.mode or "code")
        context = f"{used:,}/{size:,}" if size else "waiting"
        reasoning_name = str(THOUGHT_LEVELS.get(reasoning, {}).get("name", reasoning))
        endpoint_name = str(API_ENDPOINTS.get(endpoint, {}).get("name", endpoint))
        quota = self._quota_summary()
        awareness = self._awareness_summary()
        tokens = self._token_summary(session)
        enabled = self._statusline_segments
        # Build each segment conditionally so /statusline toggles take effect
        # on the very next refresh. State and session ID stay pinned at the top.
        lines: list[str] = []
        if "state" in enabled:
            lines.append(f"● {state}")
        if "session_id" in enabled:
            sid = (self.session_id[:8] + "…") if self.session_id else "starting"
            lines.append(sid)
        if lines:
            lines.append("")  # blank separator after the header block
        if "model" in enabled:
            lines.append(f"{model} · {reasoning_name}")
        if "endpoint" in enabled:
            lines.append(endpoint_name)
        if "mode" in enabled:
            lines.append(f"{mode} · {permission}")
        if "context" in enabled:
            lines.append(f"context {context}")
        if "tokens" in enabled:
            lines.append(tokens)
        if "awareness" in enabled:
            lines.append(awareness)
        if "quota" in enabled:
            lines.append(quota)
        # Always render at least the state line so the panel is never blank
        # (e.g., if a user disables everything but state).
        if not lines:
            lines.append(f"● {state}")
        self.query_one("#session", Static).update("\n".join(lines))

    @staticmethod
    def _token_summary(session: Any) -> str:
        """Live session token totals (real, not estimated)."""
        if session is None:
            return "tokens —"
        inp = int(getattr(session, "total_input_tokens", 0) or 0)
        out = int(getattr(session, "total_output_tokens", 0) or 0)
        cached = int(getattr(session, "total_cached_tokens", 0) or 0)
        if inp == 0 and out == 0:
            return "tokens waiting"
        cached_pct = f" · cache {cached * 100 // inp:g}%" if inp and cached else ""
        return f"tokens ↑{inp:,} ↓{out:,}{cached_pct}"

    def _awareness_summary(self) -> str:
        """Compact one-line epistemic and metacognitive state for the panel."""
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        if session is None:
            return "awareness —"
        awareness = getattr(session, "awareness", None)
        metacog = getattr(session, "metacognition", None)
        if awareness is None or metacog is None:
            return "awareness —"
        active = awareness.active_records()
        contradictions = sum(1 for r in active if r.kind == "contradiction")
        observations = sum(1 for r in active if r.kind == "observation")
        assessment = getattr(metacog, "assessment", None)
        exec_mode = getattr(assessment, "execution_mode", None) or "direct"
        risk = getattr(assessment, "risk_score", 0)
        if contradictions:
            return f"⚠ {exec_mode} · {contradictions} contradiction · /awareness"
        if observations:
            return f"⬡ {exec_mode} · {observations} evidence · risk {risk}"
        return f"⬡ {exec_mode} · risk {risk}"

    def _refresh_queue_display(self) -> None:
        """Update the queue-status widget to show queued prompts."""
        widget = self.query_one("#queue-status", Static)
        if not self._prompt_queue:
            widget.update("")
            return
        count = len(self._prompt_queue)
        preview = " · ".join(
            f"[{i + 1}] {item[:60]}" for i, item in enumerate(self._prompt_queue[:3])
        )
        suffix = f" (+{count - 3} more)" if count > 3 else ""
        widget.update(f"📋 Queue ({count}): {preview}{suffix}")

    def _quota_summary(self) -> str:
        if self._provider_usage is None:
            return "quota unavailable · /usage" if self._provider_usage_error else "quota loading…"
        windows: list[str] = []
        for quota in self._provider_usage.quotas:
            if quota.percentage is None:
                continue
            if quota.kind == "TOKENS_LIMIT" and quota.unit == 3:
                windows.append(f"5h {quota.percentage:g}%")
            elif quota.kind == "TOKENS_LIMIT" and quota.unit == 6:
                windows.append(f"week {quota.percentage:g}%")
            elif quota.kind == "TIME_LIMIT":
                windows.append(f"MCP {quota.percentage:g}%")
        return "quota " + (" · ".join(windows) if windows else "reported · /usage")

    @staticmethod
    def _bounded_activity_label(label: str, limit: int = 56) -> str:
        """Keep streamed tool titles to one bounded terminal line."""
        normalized = " ".join(label.split())
        return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"

    def _set_activity(
        self,
        label: str,
        *,
        active: bool = False,
        tone: str = "active",
        hold: float | None = None,
    ) -> None:
        """Set presentation-only activity without becoming session truth."""
        bounded_label = self._bounded_activity_label(label)
        if (
            hold is None
            and self._activity_hold_until is None
            and bounded_label == self._activity_label
            and tone == self._activity_tone
            and active == self._activity_active
        ):
            return
        self._activity_label = bounded_label
        self._activity_tone = tone
        self._activity_active = active
        self._activity_frame = 0
        self._activity_started = time.monotonic()
        self._activity_hold_until = (
            self._activity_started + hold if hold is not None and hold > 0 else None
        )
        self._render_activity()
        if self._activity_timer is not None:
            if (active and self._activity_animation_enabled) or self._activity_hold_until:
                self._activity_timer.resume()
            else:
                self._activity_timer.pause()

    def _advance_activity_animation(self) -> None:
        if self._activity_hold_until is not None and time.monotonic() >= self._activity_hold_until:
            self._set_activity("Ready", tone="ready")
            return
        if self._activity_active and self._activity_animation_enabled:
            self._activity_frame = (self._activity_frame + 1) % len(self.ACTIVITY_FRAMES)
        self._render_activity()

    def _render_activity(self) -> None:
        widget = self.query_one("#activity-status", Static)
        styles = {
            "active": "bold #85c8ff",
            "success": "bold #68d391",
            "warning": "bold #f6c85f",
            "error": "bold #ff7b72",
            "ready": "#7f96ab",
        }
        symbols = {
            "success": "✓",
            "warning": "○",
            "error": "!",
            "ready": "●",
        }
        if self._activity_active:
            symbol = (
                self.ACTIVITY_FRAMES[self._activity_frame]
                if self._activity_animation_enabled
                else "◆"
            )
        else:
            symbol = symbols.get(self._activity_tone, "•")
        rendered = Text()
        rendered.append(f"{symbol} {self._activity_label}", style=styles[self._activity_tone])
        if self._activity_active and self._activity_animation_enabled:
            elapsed = max(0.0, time.monotonic() - self._activity_started)
            rendered.append(f"  {elapsed:.1f}s", style="dim #7f96ab")
        widget.update(rendered)

    async def action_cancel_turn(self) -> None:
        if self._prompt_worker is None:
            self.notify("No active turn", severity="information")
            return
        self._set_activity("Cancelling", active=True, tone="warning")
        await self.agent.cancel(session_id=self.session_id)
        self._prompt_worker.cancel()

    async def action_quit_agent(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self.exit(0)

    async def _close_agent_resources(self) -> None:
        if self._agent_closed:
            return
        self._agent_closed = True
        if self._recorder is not None:
            getattr(self._recorder, "cleanup", lambda: None)()
            self._recorder = None
        try:
            if self._prompt_worker is not None and self.session_id:
                await self.agent.cancel(session_id=self.session_id)
                self._prompt_worker.cancel()
            await asyncio.wait_for(
                self.agent.aclose(),
                timeout=self.SHUTDOWN_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    async def action_clear_transcript(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        self._current_agent = None
        self._current_agent_text = ""

    def insert_command(self, command: str) -> None:
        """Insert a slash command into the composer for review.

        Used by the command-palette callbacks: the user can then add
        arguments and press Enter, or backspace to cancel. Mirrors the
        existing ``action_show_help`` pattern.
        """
        try:
            composer = self.query_one("#composer", CommandInput)
        except Exception:
            return
        composer.value = command
        composer.focus()
        composer.cursor_position = len(command)

    def make_insert_command_callback(self, command: str) -> Callable[[], None]:
        """Return a sync palette callback that inserts ``command`` for review."""

        def _insert() -> None:
            self.insert_command(command)

        return _insert

    def make_action_callback(self, action: str) -> Callable[[], None]:
        """Return a sync palette callback that schedules ``action`` on the app.

        Uses ``call_later`` so async actions are awaited correctly and the
        palette screen can dismiss before the action (which may push its own
        screen) runs.
        """

        def _run() -> None:
            self.call_later(getattr(self, f"action_{action}"))

        return _run

    async def action_show_help(self) -> None:
        composer = self.query_one("#composer", Input)
        if composer.disabled:
            self.notify("Help is unavailable while a turn is running", severity="warning")
            return
        composer.value = "/help"
        composer.focus()
        await composer.action_submit()

    def action_toggle_thinking(self) -> None:
        thinking = self.query_one("#thinking", RichLog)
        thinking.toggle_class("hidden")
        state = "hidden" if thinking.has_class("hidden") else "shown"
        self.notify(f"Reasoning panel {state}", severity="information")

    async def _set_vim_mode(self, enabled: bool) -> None:
        """Swap the composer without losing an in-progress prompt or focus."""
        old = self.query_one("#composer", CommandInput)
        composer_type = ModalComposer if enabled else CommandInput
        if isinstance(old, composer_type):
            composer = old
        else:
            value = old.value
            disabled = old.disabled
            had_focus = self.focused is old
            composer = composer_type(
                placeholder=old.placeholder,
                id="composer",
                disabled=disabled,
            )
            composer.value = value
            composer.cursor_position = min(old.cursor_position, len(value))
            await old.remove()
            await self.mount(composer, before=self.query_one(Footer))
            if had_focus:
                composer.focus()
        self._vim_enabled = enabled
        if isinstance(composer, ModalComposer):
            composer.set_mode("normal")
        self.notify(
            "Vim composer enabled — press i to edit" if enabled else "Vim composer disabled",
            severity="information",
        )

    async def action_toggle_vim(self) -> None:
        await self._set_vim_mode(not self._vim_enabled)
        try:
            save_vim_config(self._vim_enabled)
        except OSError as error:
            self.notify(f"Could not save Vim preference: {error}", severity="warning")

    def action_settings(self) -> None:
        if self._agent_ready and self._prompt_worker is None:
            self.open_settings()
        else:
            self.notify("Settings are unavailable while a turn is running", severity="warning")

    async def action_open_history(self) -> None:
        """F6 / /history: browse and resume persisted sessions."""
        if not self._agent_ready:
            self.notify("Session not ready", severity="warning")
            return
        if self._prompt_worker is not None:
            self.notify("Finish the current turn before switching sessions", severity="warning")
            return
        sessions = await asyncio.to_thread(self.agent._store.list)
        # Prefer the current workspace; fall back to all when it has none.
        cwd = self._session_cwd()
        same_workspace = [s for s in sessions if s.get("cwd") == cwd] if cwd else []
        candidates = same_workspace if same_workspace else sessions
        selected = await self.push_screen_wait(HistoryScreen(candidates[:200]))
        if not selected:
            return
        await self._resume_session(selected)

    async def action_open_search(self) -> None:
        """Ctrl-F / /search: grep the current conversation."""
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        messages = list(getattr(session, "messages", None) or [])
        if session is None or not messages:
            self.notify("Nothing to search yet", severity="information")
            return
        result = await self.push_screen_wait(SearchScreen(messages))
        if result is None:
            return
        _idx, full_text = result
        # Show the full message in a fresh transcript entry (read-only).
        await self._append_system(
            f"Search match · full message:\n\n{full_text[:4000]}"
            + (" …[truncated]" if len(full_text) > 4000 else "")
        )

    def _extract_code_blocks(self) -> list[tuple[str, str]]:
        """Extract ``(language, code)`` tuples from recent agent responses.

        Scans the last ~20 streamed responses (plus any in-flight text) for
        fenced code blocks. Used by ``/blocks`` and the picker modal.
        """
        responses = list(self._agent_responses)
        if self._current_agent_text:
            responses.append(self._current_agent_text)
        blocks: list[tuple[str, str]] = []
        pattern = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
        for response in responses[-20:]:
            for match in pattern.finditer(response):
                lang = (match.group(1) or "text").strip()[:16]
                code = match.group(2).strip()
                if code:
                    blocks.append((lang, code))
        return blocks

    async def action_open_blocks_picker(self) -> None:
        """``/blocks``: pick a code block from recent responses to copy or save."""
        blocks = self._extract_code_blocks()
        if not blocks:
            self.notify("No code blocks in recent responses", severity="information")
            return
        result = await self.push_screen_wait(CodeBlockPickerScreen(blocks))
        if result is None:
            return
        action, code = result
        if action == "copy":
            if _write_system_clipboard(code):
                preview = code.splitlines()[0][:60] if code else ""
                self.notify(f"Copied {len(code)} chars: {preview}…", severity="success")
            else:
                self.notify("Clipboard unavailable (install xclip or xsel)", severity="warning")
        elif action == "write":
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = Path(self._session_cwd()) / f"glm-block-{timestamp}.txt"
            try:
                target.write_text(code, encoding="utf-8")
                await self._append_system(f"Wrote code block to {target}")
                self.notify(f"Wrote {len(code)} chars to {target.name}", severity="success")
            except OSError as error:
                self.notify(f"Write failed: {error}", severity="error")

    async def action_open_statusline(self) -> None:
        """``/statusline``: toggle which sidebar session-panel segments are visible.

        Persists the user's choices to ``config_dir()/statusline.json`` so
        the selection survives restarts. The panel re-renders immediately
        on save so the change is visible without a refresh.
        """
        result = await self.push_screen_wait(StatusLineScreen(self._statusline_segments))
        if result is None:
            return
        try:
            self._statusline_segments = save_statusline_config(result)
        except OSError as error:
            self.notify(f"Could not save statusline config: {error}", severity="warning")
            self._statusline_segments = set(result)
        # Force an immediate re-render with the new segment set.
        self._refresh_session_panel("Running" if self._prompt_worker is not None else "Ready")
        enabled_count = len(self._statusline_segments)
        total = len(STATUSLINE_SEGMENTS)
        self.notify(
            f"{enabled_count}/{total} sidebar segments visible",
            title="Statusline updated",
            severity="success",
        )

    def _apply_keybind_overrides(self, overrides: dict[str, str]) -> None:
        """Overlay persisted keybinding overrides on the default Textual map.

        Textual's documented keymap API replaces bindings by their stable
        binding IDs. It avoids the duplicate-old-key behavior of repeatedly
        calling the lower-level runtime ``bind`` helper.
        """
        valid: dict[str, str] = {}
        unknown: list[str] = []
        for action, keys in overrides.items():
            if action not in KEYBINDABLE_ACTION_IDS:
                unknown.append(action)
            else:
                valid[action] = keys
        # Rebuild from the original map on every save/reset, then apply the
        # requested overlay. This makes Reset defaults immediate.
        self._bindings = self._default_keybindings.copy()
        try:
            self._bindings.apply_keymap(valid)
        except Exception as error:  # noqa: BLE001 - malformed user config
            self._bindings = self._default_keybindings.copy()
            self.call_later(
                self._append_system,
                f"Keybinding overrides ignored: {error}",
            )
            self.notify("Invalid keybinding override — using defaults", severity="warning")
            return
        if unknown:
            names = ", ".join(sorted(unknown))
            message = f"Ignored unknown keybinding action(s): {names}"
            self.call_later(self._append_system, message)
            self.notify(message, severity="warning")

    async def action_open_keybinds(self) -> None:
        """``/keybinds``: edit persistent F-key and chord binding overrides."""
        result = await self.push_screen_wait(KeybindsScreen(self._keybind_overrides))
        if result is None:
            return
        if not result:
            try:
                keybinds_path().unlink(missing_ok=True)
            except OSError as error:
                self.notify(f"Could not reset keybindings: {error}", severity="warning")
                return
            self._keybind_overrides = {}
            self._apply_keybind_overrides({})
            self.notify("Keybindings reset to defaults", severity="success")
            return
        try:
            self._keybind_overrides = save_keybinds_config(result)
        except OSError as error:
            self.notify(f"Could not save keybindings: {error}", severity="warning")
            return
        self._apply_keybind_overrides(self._keybind_overrides)
        self.notify("Keybindings updated", severity="success")

    async def action_open_context(self) -> None:
        """``/context``: visualize context-window usage by message-role segment."""
        try:
            breakdown = self.agent.context_breakdown(self.session_id)
        except Exception as error:  # noqa: BLE001 — surface any error plainly
            self.notify(f"Context breakdown failed: {error}", severity="error")
            return
        if not breakdown.get("segments"):
            self.notify("Session not ready — try again in a moment", severity="information")
            return
        await self.push_screen(ContextBudgetScreen(breakdown))

    async def action_open_btw(self, prefill_question: str = "") -> None:
        """``/btw [question]``: ask a side question in an overlay.

        The question goes to the auxiliary GLM and the answer stays in the
        overlay — it is NOT added to ``session.messages``, so the main
        conversation thread stays clean. If a question is passed (via
        ``/btw <question>``), the overlay fires the query immediately on
        mount; otherwise the user types and presses Enter.
        """
        await self.push_screen(BtwOverlayScreen(prefill_question))

    def _tasks_snapshot(self) -> dict[str, Any]:
        """Build a read-only snapshot of the current TUI + session state."""
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        elapsed = time.monotonic() - self._turn_start_time if self._prompt_worker else 0.0
        context_size = int(getattr(session, "context_size", 0)) or 1
        estimated = int(getattr(session, "estimated_tokens", 0))
        return {
            "turn_state": "Running" if self._prompt_worker is not None else "Idle",
            "turn_elapsed": elapsed,
            "activity": self._activity_label,
            "queue": list(self._prompt_queue),
            "session": {
                "model": getattr(session, "model", self.args.model or "?"),
                "mode": getattr(session, "mode", self.args.mode or "?"),
                "permission": getattr(session, "permission_mode", self.args.permission or "?"),
                "input_tokens": int(getattr(session, "total_input_tokens", 0)),
                "output_tokens": int(getattr(session, "total_output_tokens", 0)),
                "cached_tokens": int(getattr(session, "total_cached_tokens", 0)),
                "context_percent": round(estimated * 100.0 / context_size, 2),
                "max_iterations": int(getattr(session, "max_tool_iterations", 50)),
            },
        }

    async def action_open_tasks(self) -> None:
        """``/tasks``: show the session dashboard (turn state, queue, stats)."""
        await self.push_screen(TasksScreen(self._tasks_snapshot()))

    async def action_smart(self, name: str) -> None:
        """``/smart [name]``: expand a smart-prompt template with git context.

        Bare ``/smart`` lists available templates. ``/smart <name>`` resolves
        the template's ``{branch}``, ``{diff}``, ``{commit_log}``, ``{cwd}``
        variables from the workspace's git state and inserts the expanded
        prompt into the composer for review before sending.
        """
        if not name:
            lines = ["**Smart prompts** — use `/smart <name>`:"]
            for key, (label, _template) in SMART_PROMPTS.items():
                lines.append(f"  `/smart {key}` — {label}")
            await self._append_system("\n".join(lines))
            return

        if name not in SMART_PROMPTS:
            available = ", ".join(sorted(SMART_PROMPTS))
            self.notify(
                f"Unknown template: {name!r}. Available: {available}",
                severity="warning",
            )
            return

        _label, template = SMART_PROMPTS[name]
        expanded = self._resolve_smart_prompt(template)
        # Insert into the composer for review (the user can edit before Enter).
        try:
            composer = self.query_one("#composer", CommandInput)
            composer.value = expanded
            composer.focus()
            composer.cursor_position = len(expanded)
        except Exception:
            pass

    def _resolve_smart_prompt(self, template: str) -> str:
        """Resolve ``{branch}``, ``{diff}``, ``{commit_log}``, ``{cwd}`` in a template."""
        import subprocess as _sp

        cwd = self._session_cwd()

        def _git(args: list[str], fallback: str = "") -> str:
            try:
                result = _sp.run(
                    ["git", *args],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=cwd,
                )
                return result.stdout.strip() if result.returncode == 0 else fallback
            except Exception:
                return fallback

        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], "(unknown branch)")
        diff = _git(["diff"], "(no changes)")[:2000]
        commit_log = _git(
            ["log", "--oneline", "-10"],
            "(no commits)",
        )

        return template.format(
            branch=branch,
            diff=diff,
            commit_log=commit_log,
            cwd=cwd,
        )

    @staticmethod
    def _parse_loop_interval(arg: str) -> float | None:
        """Parse ``30s``, ``5m``, ``1h``, or a bare number into seconds."""
        arg = arg.strip().lower()
        if not arg:
            return None
        multipliers = {"s": 1.0, "m": 60.0, "h": 3600.0}
        if arg[-1] in multipliers:
            try:
                return float(arg[:-1]) * multipliers[arg[-1]]
            except ValueError:
                return None
        try:
            return float(arg)
        except ValueError:
            return None

    async def action_loop(self, arg: str) -> None:
        """``/loop [interval] [prompt]``: run a prompt repeatedly at an interval.

        In-session ad-hoc iteration (distinct from the persistent cron
        subsystem). Examples::

            /loop 5m check if the deploy finished
            /loop 30s re-run the tests
            /loop stop

        The loop fires immediately on start and then every ``interval``.
        Prompts that fire while a turn is running are queued in the FIFO.
        """
        stripped = arg.strip()
        # /loop stop / /loop (bare) → cancel
        if not stripped or stripped.lower() in {"stop", "cancel", "off", "clear"}:
            if self._loop_timer is not None:
                self._loop_timer.stop()
                self._loop_timer = None
                self._loop_prompt = ""
                self._loop_interval_seconds = 0.0
                self.notify("Loop stopped", severity="information")
            else:
                self.notify("No active loop", severity="information")
            return

        parts = stripped.split(None, 1)
        if len(parts) < 2:
            self.notify(
                "Usage: /loop <interval> <prompt>  (e.g. /loop 5m check CI status)",
                severity="warning",
            )
            return

        interval_str, prompt = parts
        interval = self._parse_loop_interval(interval_str)
        if interval is None or interval < 5:
            self.notify(
                f"Invalid interval: {interval_str!r} — use e.g. 30s, 5m, 1h (min 5s)",
                severity="error",
            )
            return

        # Stop any existing loop first.
        if self._loop_timer is not None:
            self._loop_timer.stop()

        self._loop_prompt = prompt
        self._loop_interval_seconds = interval
        self._loop_timer = self.set_interval(
            interval,
            self._loop_tick,
            name=f"loop-{interval_str}",
        )
        # Fire immediately on start.
        self.call_later(self._loop_tick)
        mins, secs = divmod(int(interval), 60)
        time_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
        preview = prompt[:60].replace("\n", " ")
        self.notify(f"Loop every {time_str}: {preview}…", severity="success")

    def _loop_tick(self) -> None:
        """Submit the loop prompt (queued if a turn is running)."""
        if not self._loop_prompt or not self._agent_ready:
            return
        text = self._loop_prompt
        if self._prompt_worker is not None:
            # Queue with a loop marker so the user can distinguish it.
            self._prompt_queue.append(f"🔄 {text}")
            self._refresh_queue_display()
            return
        # Submit directly — schedule the async submit on the event loop.
        self.call_later(self._submit_loop_prompt, text)

    async def _submit_loop_prompt(self, text: str) -> None:
        """Append + submit a loop prompt (mirrors submit_input's core path)."""
        await self._append_user(f"🔄 Loop: {text}")
        self._current_agent = None
        self._current_agent_text = ""
        self._thinking_text = ""
        self.query_one("#thinking", RichLog).clear()
        self._refresh_session_panel("Running")
        self._set_activity("Thinking", active=True)
        self._prompt_worker = self.run_prompt(text, [])

    async def run_compact_from_context_view(self) -> None:
        """Run ``/compact`` after the context-view modal signals it (press ``c``).

        Inserts ``/compact`` into the composer and submits it, reusing the
        existing agent-side handler (which calls ``_maybe_compact`` with
        ``force=True``).
        """
        composer = self.query_one("#composer", CommandInput)
        if composer.disabled:
            self.notify("Cannot compact while a turn is running", severity="warning")
            return
        composer.value = "/compact"
        await composer.action_submit()

    async def _resume_session(self, session_id: str) -> None:
        """Resume a persisted session through the shared agent runtime."""
        if session_id == self.session_id:
            self.notify("Already on this session", severity="information")
            return
        cwd = self._session_cwd()
        try:
            await self.agent.resume_session(cwd=cwd, session_id=session_id)
        except Exception as exc:  # pragma: no cover - defensive
            self.notify(f"Resume failed: {exc}", severity="error")
            return
        # Switch the live view to the resumed session.
        await self.action_clear_transcript()
        self.session_id = session_id
        self._agent_responses.clear()
        self._current_agent = None
        self._current_agent_text = ""
        self._replaying = True
        # The agent replays history via session_update notifications which
        # handle_session_update() will append to the transcript.
        await asyncio.sleep(0)
        self._replaying = False
        self.query_one("#composer", Input).focus()
        self.notify(f"Resumed session {session_id[:8]}", severity="success")

    async def action_toggle_working_tree(self) -> None:
        """Cycle: closed → Changes → Git → Diff → Files → closed."""
        panel = self.query_one("#working-tree-panel")
        if not self._wt_visible:
            self._wt_visible = True
            self._wt_view = 0
            panel.remove_class("hidden")
            await self._switch_wt_view(0)
            return
        self._wt_view += 1
        if self._wt_view >= 5:
            self._wt_visible = False
            panel.add_class("hidden")
            self.notify("Working tree panel closed", severity="information")
        else:
            await self._switch_wt_view(self._wt_view)

    async def action_annotate(self) -> None:
        """Open the line-anchored diff annotator and prefill its follow-up."""
        if self._wt_visible:
            self._wt_view = 2
            await self._switch_wt_view(2)
        diff_text = await asyncio.to_thread(self._run_git, self._session_cwd(), "diff", "HEAD")
        if not diff_text or not diff_text.strip():
            self.notify("No working-tree diff to annotate", severity="information")
            return
        comments = await self.push_screen_wait(DiffAnnotationScreen(diff_text))
        if not comments:
            return
        prompt = "Please revise the following hunks:\n" + "\n".join(
            f"- {file_path}:{line} — {comment}" for file_path, line, comment in comments
        )
        composer = self.query_one("#composer", Input)
        composer.value = prompt
        composer.cursor_position = len(prompt)
        composer.focus()
        self.notify(f"Added {len(comments)} diff annotation(s) to the composer", severity="success")

    _WT_VIEW_IDS = ("wt-changes", "wt-git", "wt-diff", "wt-files", "wt-github")
    _WT_VIEW_LABELS = ("Changes", "Git", "Diff", "Files", "GitHub")

    async def _switch_wt_view(self, view: int) -> None:
        switcher = self.query_one("#wt-switcher", ContentSwitcher)
        switcher.current = self._WT_VIEW_IDS[view]
        tabs = []
        for i, label in enumerate(self._WT_VIEW_LABELS):
            prefix = "▶ " if i == view else "  "
            tabs.append(f"{prefix}[{i + 1}]{label}")
        self.query_one("#wt-tabs", Static).update(" ".join(tabs) + "  (F4)")
        await self._refresh_wt_view(view)

    async def _refresh_wt_view(self, view: int) -> None:
        refreshers = (
            self._refresh_wt_changes,
            self._refresh_wt_git,
            self._refresh_wt_diff,
            self._refresh_wt_files,
            self._refresh_wt_github,
        )
        await refreshers[view]()

    async def _handle_journey(self) -> None:
        """`/journey` — show memories + skills + profile as a timeline."""
        cwd = self._session_cwd()
        try:
            memories = await asyncio.to_thread(_journey_extract_memory_lines, cwd)
            skills = await asyncio.to_thread(list_learned_skills, cwd)
            profile = await asyncio.to_thread(_journey_extract_profile_lines)
        except Exception as exc:  # pragma: no cover - defensive
            self.notify(f"Journey failed: {exc}", severity="error")
            return
        await self.push_screen_wait(
            JourneyScreen(memories=memories, skills=skills, profile=profile)
        )

    def _session_cwd(self) -> str:
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        return str(getattr(session, "cwd", os.getcwd()))

    async def _handle_undo(self, text: str) -> None:
        """`/undo [N]` — pop N user turns via the shared agent, prefill composer."""
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        if session is None:
            self.notify("Session not ready", severity="warning")
            return
        if self._prompt_worker is not None:
            self.notify("Finish the current turn before undoing", severity="warning")
            return
        try:
            response = await self.agent._handle_command(session, text)
        except Exception as exc:  # pragma: no cover - defensive
            self.notify(f"Undo failed: {exc}", severity="error")
            return
        # The agent returns the response with an optional "---PROMPT---"
        # marker carrying the most recent removed user message.
        marker = "\n---PROMPT---\n"
        if marker in response:
            head, prefill = response.split(marker, 1)
            await self._append_system(head.strip())
            composer = self.query_one("#composer", Input)
            composer.value = prefill
            composer.cursor_position = len(composer.value)
            composer.focus()
            self.notify("Last message prefilled — edit and resend", severity="information")
        else:
            await self._append_system(response)
            self.query_one("#composer", Input).focus()

    async def _handle_prompt_editor(self) -> None:
        """`/prompt` — open $EDITOR on a tempfile and queue the result."""
        if self._prompt_worker is not None:
            self.notify("Finish the current turn first", severity="warning")
            return
        prompt = await self._compose_prompt_in_editor()
        if not prompt:
            return
        # Inject the prompt into the composer and submit so it routes through
        # the normal queueing path (which respects in-flight turns).
        composer = self.query_one("#composer", CommandInput)
        composer.value = prompt.replace("\n", " ").strip()
        await composer.action_submit()

    @staticmethod
    async def _compose_prompt_in_editor(editor_argv: list[str] | None = None) -> str:
        """Run $VISUAL/$EDITOR on a tempfile and return the cleaned prompt.

        If ``editor_argv`` is provided (e.g. by tests), it bypasses env-var
        parsing entirely — callers supply the ready argv list. Otherwise the
        ``$VISUAL``/``$EDITOR`` env var is shell-parsed (POSIX-aware).
        Returns an empty string if the user saved an empty file or the
        editor was unavailable. Comment lines starting with '#' are stripped.
        """
        import shlex
        import tempfile

        if editor_argv is None:
            editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
            try:
                editor_argv = shlex.split(editor, posix=(os.name != "nt"))
            except ValueError:
                editor_argv = editor.split()
        if not editor_argv:
            return ""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(
                "# Compose your prompt below this line.\n"
                "# Lines starting with '#' are stripped. Save and quit to send.\n\n"
            )
            temp_path = fh.name
        try:
            proc = await asyncio.create_subprocess_exec(
                *editor_argv,
                temp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
        except (FileNotFoundError, OSError):
            return ""
        except Exception:  # pragma: no cover - defensive
            return ""
        try:
            with open(temp_path, encoding="utf-8") as fh:
                raw = fh.read()
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        cleaned_lines = [line for line in raw.splitlines() if not line.lstrip().startswith("#")]
        return "\n".join(cleaned_lines).strip()

    async def _refresh_wt_changes(self) -> None:
        widget = self.query_one("#wt-changes", VerticalScroll)
        await widget.remove_children()
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        verification = getattr(session, "verification", None)
        paths = getattr(verification, "changed_paths", None) or []
        if not paths:
            await widget.mount(Static("No files changed this session yet.", markup=False))
            return
        for path in sorted(set(str(p) for p in paths)):
            await widget.mount(Static(f"📝 {path}", markup=False))

    async def _refresh_wt_git(self) -> None:
        widget = self.query_one("#wt-git", VerticalScroll)
        await widget.remove_children()
        cwd = self._session_cwd()
        result = await asyncio.to_thread(self._run_git, cwd, "status", "--short", "--porcelain")
        if result is None:
            await widget.mount(Static("Not a git repository.", markup=False))
            return
        if not result.strip():
            await widget.mount(Static("Working tree clean — no changes.", markup=False))
            return
        for line in result.strip().splitlines():
            await widget.mount(Static(line, markup=False))

    async def _refresh_wt_diff(self) -> None:
        widget = self.query_one("#wt-diff", VerticalScroll)
        await widget.remove_children()
        cwd = self._session_cwd()
        result = await asyncio.to_thread(self._run_git, cwd, "diff", "--stat")
        if not result or not result.strip():
            await widget.mount(Static("No uncommitted changes to diff.", markup=False))
            return
        await widget.mount(Static(result[:8000], markup=False))

    async def _refresh_wt_files(self) -> None:
        widget = self.query_one("#wt-files", VerticalScroll)
        await widget.remove_children()
        cwd = self._session_cwd()
        try:
            entries = sorted(os.listdir(cwd))
        except OSError:
            await widget.mount(Static("Cannot read directory.", markup=False))
            return
        shown = 0
        for entry in entries:
            if entry.startswith("."):
                continue
            full = os.path.join(cwd, entry)
            marker = "📁" if os.path.isdir(full) else "📄"
            await widget.mount(Static(f"{marker} {entry}", markup=False))
            shown += 1
            if shown >= 200:
                await widget.mount(Static(f"… ({len(entries) - shown} more)", markup=False))
                break

    async def _refresh_wt_github(self) -> None:
        """Populate the GitHub view: PR for current branch + assigned issues."""
        widget = self.query_one("#wt-github", VerticalScroll)
        await widget.remove_children()
        cwd = self._session_cwd()

        # Get current branch.
        branch = self._run_git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        branch = branch.strip() if branch else "(unknown)"
        await widget.mount(Static(f"Branch: {branch}", markup=False))

        # Try gh CLI for PR + issues.
        async def _gh(args: list[str]) -> str | None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "gh",
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                return out.decode() if proc.returncode == 0 else None
            except Exception:
                return None

        # PRs for this branch.
        pr_json = await _gh(
            [
                "pr",
                "list",
                "--head",
                branch,
                "--json",
                "number,title,state,isDraft,reviewDecision",
                "--limit",
                "5",
            ]
        )
        if pr_json is not None:
            try:
                prs = json.loads(pr_json)
            except json.JSONDecodeError:
                prs = []
            await widget.mount(Static("", markup=False))  # spacer
            if prs:
                await widget.mount(Static("Pull Requests", markup=False))
                for pr in prs:
                    marker = "🔵" if pr.get("isDraft") else "🟢"
                    review = pr.get("reviewDecision", "")
                    review_str = f"  review:{review}" if review else ""
                    await widget.mount(
                        Static(
                            f"  {marker} #{pr['number']} {pr['title']}{review_str}",
                            markup=False,
                        )
                    )
            else:
                await widget.mount(Static("No open PRs for this branch.", markup=False))
        else:
            await widget.mount(Static("", markup=False))
            await widget.mount(Static("(gh not available or not authenticated)", markup=False))

        # Assigned issues.
        issue_json = await _gh(
            [
                "issue",
                "list",
                "--assignee",
                "@me",
                "--json",
                "number,title,state",
                "--limit",
                "5",
            ]
        )
        if issue_json is not None:
            try:
                issues = json.loads(issue_json)
            except json.JSONDecodeError:
                issues = []
            await widget.mount(Static("", markup=False))
            if issues:
                await widget.mount(Static("Assigned Issues", markup=False))
                for issue in issues:
                    await widget.mount(
                        Static(
                            f"  #{issue['number']} {issue['title']}",
                            markup=False,
                        )
                    )
            else:
                await widget.mount(Static("No issues assigned to you.", markup=False))

    @staticmethod
    def _run_git(cwd: str, *args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            return result.stdout
        except (OSError, subprocess.TimeoutExpired):
            return None

    async def action_toggle_voice(self) -> None:
        """Push-to-talk: F5 toggles recording, transcribes via local Whisper."""
        from .voice import (
            VoiceRecorder,
            is_voice_available,
            suppress_sound_during_recording,
            transcribe_audio,
        )

        if not is_voice_available():
            self.notify(
                "Voice requires: uv pip install -e '.[voice]'",
                title="Push to talk unavailable",
                severity="warning",
            )
            return

        if self._recorder is not None and getattr(self._recorder, "recording", False):
            wav_path = self._recorder.stop()
            suppress_sound_during_recording()
            self._set_activity("Transcribing…", active=True)
            if wav_path:
                text = await transcribe_audio(wav_path)
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
                if text:
                    composer = self.query_one("#composer", Input)
                    if composer.value and not composer.value.endswith(" "):
                        composer.value += " "
                    composer.value += text
                    composer.cursor_position = len(composer.value)
                    composer.focus()
                    self._set_activity(f"✓ {text[:50]}", tone="success", hold=3.0)
                else:
                    self._set_activity("Transcription empty", tone="warning", hold=3.0)
            else:
                self._set_activity("Recording failed", tone="error", hold=3.0)
            self._recorder = None
        else:
            recorder = VoiceRecorder()
            if recorder.start():
                self._recorder = recorder
                suppress_sound_during_recording()
                self._set_activity("🎤 Recording… (F5 to stop)", active=True)
            else:
                self._recorder = None
                self.notify("Microphone unavailable", severity="warning")

    async def action_copy_last_response(self) -> None:
        """Ctrl+Y: copy the last agent response to the system clipboard."""
        await self._copy_response(None)

    async def action_copy_selection(self) -> None:
        """Ctrl+Shift+C: copy the currently selected text to the clipboard."""
        try:
            text = self.screen.get_selected_text()
        except Exception:
            text = ""
        if text:
            if _write_system_clipboard(text):
                self.notify(f"Copied {len(text)} characters to clipboard", severity="success")
            else:
                self.notify("Clipboard unavailable (install xclip or xsel)", severity="warning")
        else:
            self.notify(
                "No text selected — click and drag to select, then Ctrl+Shift+C",
                severity="information",
            )

    async def _copy_response(self, index: int | None) -> None:
        """Copy a specific agent response to the clipboard.

        index=None copies the most recent response.
        index=1 copies the first response, index=2 the second, etc.
        """
        if index is None:
            text = self._current_agent_text or (
                self._agent_responses[-1] if self._agent_responses else ""
            )
        else:
            responses = self._agent_responses[:]
            if self._current_agent_text:
                responses.append(self._current_agent_text)
            if 1 <= index <= len(responses):
                text = responses[index - 1]
            else:
                self.notify(
                    f"Response {index} not found (have {len(responses)})", severity="warning"
                )
                return
        if not text:
            self.notify("No response to copy", severity="warning")
            return
        if _write_system_clipboard(text):
            preview = text[:60].replace("\n", " ")
            self.notify(f"Copied to clipboard: {preview}…", severity="success")
        else:
            self.notify("Clipboard unavailable (install xclip or xsel)", severity="warning")

    async def _copy_all_responses(self) -> None:
        """Copy all agent responses concatenated to the clipboard."""
        responses = self._agent_responses[:]
        if self._current_agent_text:
            responses.append(self._current_agent_text)
        if not responses:
            self.notify("No responses to copy", severity="warning")
            return
        text = "\n\n---\n\n".join(responses)
        if _write_system_clipboard(text):
            self.notify(f"Copied {len(responses)} responses to clipboard", severity="success")
        else:
            self.notify("Clipboard unavailable", severity="warning")

    # ------------------------------------------------------------------
    # Native mouse mode toggle (F7 / /native-mouse / GLM_ACP_NATIVE_MOUSE=1)
    #
    # When a Textual app starts it sends ``\x1b[?1000h`` (and friends) to
    # enable X11 / SGR mouse reporting. The terminal emulator then routes
    # all mouse events to the app and stops handling native left-click
    # selection and right-click context menus. This is why copy/paste via
    # the terminal's own menu breaks whenever the TUI is running.
    #
    # The fix is NOT to draw our own dropdown menu — the terminal emulator
    # already knows how to copy/paste natively. The fix is to give mouse
    # control back to the terminal on demand. Textual's driver exposes
    # ``_disable_mouse_support()`` / ``_enable_mouse_support()`` which write
    # the proper enable/disable escape sequences (1000, 1003, 1015, 1006).
    # ------------------------------------------------------------------

    def action_toggle_native_mouse(self) -> None:
        """Toggle between Textual mouse capture and native terminal mouse.

        When **native mouse mode** is ON, the terminal emulator handles
        right-click (its own context menu) and click-drag (native text
        selection that copies to the OS clipboard). TUI mouse features
        (clickable widgets, mouse-wheel scrolling inside the transcript)
        are disabled until the user toggles back. This is the
        Codex/Claude-Code approach: get out of the terminal's way and let
        the user's existing terminal muscle memory work.
        """
        driver = getattr(self, "_driver", None)
        if driver is None or not hasattr(driver, "_disable_mouse_support"):
            self.notify(
                "Mouse toggle unavailable in this driver",
                severity="warning",
            )
            return
        self._native_mouse_mode = not self._native_mouse_mode
        try:
            if self._native_mouse_mode:
                driver._disable_mouse_support()
                self.notify(
                    "Native mouse ON — terminal handles right-click + selection.\n"
                    "Hold Shift+drag for native select while in TUI mode.\n"
                    "Press F7 or /native-mouse to restore TUI mouse.",
                    title="Native mouse mode",
                    severity="information",
                    timeout=12,
                )
            else:
                driver._enable_mouse_support()
                self.notify(
                    "TUI mouse restored — Textual handles clicks again.",
                    severity="success",
                    timeout=4,
                )
        except Exception as error:  # pragma: no cover - defensive
            self._native_mouse_mode = not self._native_mouse_mode
            self.notify(
                f"Could not toggle mouse mode: {error}",
                severity="error",
            )

    def action_toggle_screen_reader(self) -> None:
        """Toggle screen-reader (plain-text) mode and persist the choice.

        When screen-reader mode is **ON**:

        * Agent messages render as raw markdown text instead of a Rich
          Markdown renderable. ANSI styling sequences, box drawing, and
          Rich's heading rules are stripped so assistive technology reads
          the message naturally.
        * The activity status line stops animating (no glyph cycling) —
          the activity label becomes the only visible signal.
        * The preference persists across sessions in
          ``config_dir()/screen-reader.json`` and is also forced on at
          startup via ``GLM_ACP_SCREEN_READER=1``.

        Press **F8** or run ``/screen-reader`` to toggle. The currently
        visible agent message is re-rendered immediately so the user
        sees the change without restarting.
        """
        self._screen_reader = not self._screen_reader
        # Animation is hostile to screen readers — always force it off
        # when screen-reader is on, and restore the env-var preference
        # when toggling back off.
        if self._screen_reader:
            self._activity_animation_enabled = False
        else:
            animation_setting = os.environ.get("GLM_ACP_TUI_ANIMATION", "1")
            self._activity_animation_enabled = animation_setting.strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        try:
            save_screen_reader_config(self._screen_reader)
        except Exception:  # pragma: no cover - defensive
            pass
        # Re-render the in-flight agent message so the change is visible
        # without waiting for the next streaming chunk.
        if self._current_agent is not None and self._current_agent_text:
            self._current_agent.update(
                self._render_agent_content(self._current_agent_text),
                raw_text=self._current_agent_text,
            )
        state_str = "on" if self._screen_reader else "off"
        self.notify(
            f"Screen-reader mode: {state_str}",
            title="Screen reader",
            severity="information" if self._screen_reader else "success",
            timeout=6,
        )

    def _render_agent_content(self, text: str) -> Any:
        """Return the renderable for an agent message respecting screen-reader mode.

        In screen-reader mode we render the **raw markdown source** as
        plain text — no Rich styling, no box drawing, no heading rules —
        so screen readers and braille displays read the message naturally
        without tripping over ANSI escape sequences. In normal mode we
        render the rich :class:`Markdown` renderable as before.
        """
        if self._screen_reader:
            return text
        return RichMarkdown(text)

    async def _export_last_response(self) -> None:
        """Export the last agent response to a timestamped Markdown file."""
        text = self._current_agent_text or (
            self._agent_responses[-1] if self._agent_responses else ""
        )
        if not text:
            self.notify("No response to export", severity="warning")
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"glm-acp-export-{timestamp}.md"
        cwd = self._session_cwd()
        filepath = os.path.join(cwd, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# GLM ACP Response\n\n{text}\n")
            self.notify(f"Exported to {filename}", severity="success")
        except OSError as exc:
            self.notify(f"Export failed: {exc}", severity="error")

    async def _export_session(self, args: str) -> None:
        """Export the full current session.

        Syntax: ``/export [md|json] [file|clip]``
        Defaults: markdown format, copied to clipboard.
        """
        tokens = args.split() if args else []
        fmt = "md"
        target = "clip"
        for tok in tokens:
            if tok.lower() in {"md", "markdown"}:
                fmt = "md"
            elif tok.lower() == "json":
                fmt = "json"
            elif tok.lower() in {"file", "f"}:
                target = "file"
            elif tok.lower() in {"clip", "clipboard", "c"}:
                target = "clip"
        session = getattr(self.agent, "_sessions", {}).get(self.session_id)
        messages = list(getattr(session, "messages", None) or [])
        if session is None or not messages:
            self.notify("Nothing to export yet", severity="warning")
            return

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        sid_short = (self.session_id or "session")[:8]
        filename = f"glm-acp-{sid_short}-{timestamp}.{fmt}"

        if fmt == "json":
            if hasattr(session, "to_dict"):
                data = session.to_dict()
            else:
                data = {"messages": list(getattr(session, "messages", None) or [])}
            payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        else:
            payload = self._render_session_markdown(session)

        if target == "clip":
            if len(payload) > MAX_CLIPBOARD_CHARS:
                # Fall back to file when the transcript is too large for clipboard.
                target = "file"
            elif _write_system_clipboard(payload):
                self.notify(
                    f"Copied {len(payload):,} chars ({fmt}) to clipboard",
                    severity="success",
                )
                return
            else:
                self.notify(
                    "Clipboard unavailable (install xclip/xsel) — writing file instead",
                    severity="warning",
                )
                target = "file"

        if target == "file":
            cwd = self._session_cwd()
            filepath = os.path.join(cwd, filename)
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(payload)
                self.notify(f"Exported to {filename}", severity="success")
            except OSError as exc:
                self.notify(f"Export failed: {exc}", severity="error")

    @staticmethod
    def _render_session_markdown(session: Any) -> str:
        """Render a session as a self-contained Markdown transcript."""
        lines: list[str] = []
        title = getattr(session, "title", "") or "GLM ACP session"
        sid = getattr(session, "id", "") or ""
        model = getattr(session, "model", "")
        endpoint = getattr(session, "api_endpoint", "")
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"# {title}")
        lines.append("")
        lines.append(f"- Session: `{sid}`")
        if model:
            lines.append(f"- Model: `{model}`")
        if endpoint:
            lines.append(f"- API plan: `{endpoint}`")
        lines.append(f"- Exported: {when}")
        lines.append("")
        lines.append("---")
        lines.append("")
        for msg in session.messages:
            role = msg.get("role", "?")
            label = {
                "user": "## You",
                "assistant": "## Agent",
                "tool": "### Tool result",
                "system": "## System",
            }.get(role, f"## {role}")
            lines.append(label)
            lines.append("")
            text = _extract_message_text(msg).strip()
            if text:
                lines.append(text)
            else:
                lines.append("_(no text content)_")
            lines.append("")
        return "\n".join(lines)

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
        self._shutdown_requested = True
        await self._close_agent_resources()


def run_tui_command(args: argparse.Namespace) -> int:
    """Run the full-screen frontend and return its process exit status."""
    from pathlib import Path

    if not Path(args.cwd).is_dir():
        print(f"Workspace does not exist: {args.cwd}", file=__import__("sys").stderr)
        return 2
    app = NativeGlmTui(args)
    exit_code = 0
    try:
        result = app.run()
        exit_code = int(result or 0)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as error:
        print(f"Native GLM ACP TUI failed: {error}", file=__import__("sys").stderr)
        exit_code = 1
    session_id = getattr(app, "session_id", "")
    if session_id:
        print(
            f"\n📋 Session saved. To resume this conversation:\n"
            f"   glm-acp chat --resume {session_id}\n",
            file=sys.stderr,
        )
    return exit_code
