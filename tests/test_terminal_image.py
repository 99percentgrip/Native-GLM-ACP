"""Offline tests for optional terminal graphics support."""

from __future__ import annotations

from types import SimpleNamespace

from rich.text import Text

from glm_acp import terminal_image


def test_detects_iterm2(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TERM", raising=False)
    assert terminal_image.detect_graphics_protocol() == "iterm2"


def test_detects_kitty(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-kitty")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert terminal_image.detect_graphics_protocol() == "kitty"


def test_detects_wezterm_as_kitty_compatible(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "WezTerm")
    monkeypatch.delenv("TERM", raising=False)
    assert terminal_image.detect_graphics_protocol() == "kitty"


def test_tmux_and_unknown_fall_back_to_none(monkeypatch):
    monkeypatch.setenv("TERM", "screen.tmux-256color")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert terminal_image.detect_graphics_protocol() == "none"
    monkeypatch.setenv("TERM", "dumb")
    assert terminal_image.detect_graphics_protocol() == "none"


def test_explicit_protocol_override(monkeypatch):
    monkeypatch.setenv("GLM_ACP_IMAGE_PROTOCOL", "kitty")
    assert terminal_image.detect_graphics_protocol() == "kitty"


def test_render_returns_none_and_link_when_helper_is_missing(monkeypatch, tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"png")
    monkeypatch.setattr(terminal_image.shutil, "which", lambda _name: None)
    assert terminal_image.render_inline(path, "kitty") is None
    assert terminal_image.path_link(path, protocol="kitty") == (
        f"[image saved to {path}] [install kitten for inline images]"
    )


def test_render_uses_available_helper(monkeypatch, tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"png")
    monkeypatch.setattr(terminal_image.shutil, "which", lambda _name: "/usr/bin/kitten")
    monkeypatch.setattr(
        terminal_image.subprocess,
        "run",
        lambda command, **_kwargs: SimpleNamespace(returncode=0, stdout="rendered"),
    )
    rendered = terminal_image.render_inline(path, "kitty")
    assert isinstance(rendered, Text)
    assert str(rendered) == "rendered"
