"""Validated, isolated user-profile paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

PROFILE_ENV = "GLM_ACP_PROFILE"
_PROFILE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}")


def active_profile() -> str:
    """Return the validated profile id; ``default`` preserves legacy paths."""
    value = os.environ.get(PROFILE_ENV, "default").strip()
    if not _PROFILE_RE.fullmatch(value):
        raise ValueError("GLM_ACP_PROFILE must be 1-32 letters, numbers, underscores, or dashes")
    return value


def profile_path(base: Path) -> Path:
    """Scope *base* to the active profile without changing default installations."""
    profile = active_profile()
    return base if profile == "default" else base / "profiles" / profile
