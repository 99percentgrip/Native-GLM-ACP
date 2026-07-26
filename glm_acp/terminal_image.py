"""Optional terminal-image helpers with no heavyweight image dependencies.

The external terminal helpers own protocol encoding, so frozen installs stay
small. Callers always retain a path-link fallback when no compatible helper is
available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from rich.text import Text

GraphicsProtocol = Literal["kitty", "sixel", "iterm2", "none"]
_PROTOCOLS = {"kitty", "sixel", "iterm2", "none"}
_HELPERS = {"kitty": "kitten", "sixel": "img2sixel", "iterm2": "imgcat"}


def detect_graphics_protocol() -> GraphicsProtocol:
    """Detect a graphics protocol from explicit override and terminal metadata."""
    override = os.environ.get("GLM_ACP_IMAGE_PROTOCOL", "").strip().lower()
    if override in _PROTOCOLS:
        return override  # type: ignore[return-value]
    program = os.environ.get("TERM_PROGRAM", "").lower()
    term = os.environ.get("TERM", "").lower()
    if program in {"iterm.app", "iterm2"}:
        return "iterm2"
    if "kitty" in program or "kitty" in term or "wezterm" in program or "wezterm" in term:
        return "kitty"
    if "sixel" in term:
        return "sixel"
    return "none"


def path_link(path: Path, *, protocol: GraphicsProtocol | None = None) -> str:
    """Return the accessible fallback shown when inline rendering is unavailable."""
    selected = protocol or detect_graphics_protocol()
    helper = _HELPERS.get(selected)
    hint = f" [install {helper} for inline images]" if helper and not shutil.which(helper) else ""
    return f"[image saved to {path}]{hint}"


def render_inline(path: Path, protocol: GraphicsProtocol | None = None) -> Text | None:
    """Ask the compatible external helper to render *path*, or return ``None``.

    Helpers are deliberately optional. Their output is preserved as a Rich text
    renderable so Textual can place it beside the transcript/path fallback.
    """
    selected = protocol or detect_graphics_protocol()
    helper = _HELPERS.get(selected)
    if not helper or not path.is_file() or not shutil.which(helper):
        return None
    command = [helper, str(path)]
    if selected == "kitty":
        command = [helper, "icat", "--align", "left", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return Text(result.stdout or f"[inline image: {path.name}]")
