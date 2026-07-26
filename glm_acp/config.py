"""GLM model registry and configuration constants."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "glm-5.2"
DEFAULT_TIMEOUT = 180
DEFAULT_MAX_TOKENS = 128_000
MAX_AUTO_CONTINUATIONS = 20
MAX_TOOL_ITERATIONS = 50

# Synthetic value used by the model picker to represent the
# Mixture-of-Agents layer (Hermes v0.18 picker parity). It is NOT a real
# model id; picking it toggles ``session.mixture_mode`` and leaves the
# underlying ``session.model`` untouched.
MOA_PICKER_VALUE = "__moa__"
MAX_REPEATED_TOOL_BATCHES = 3
MAX_DELEGATIONS_PER_TURN = 3
MAX_DELEGATE_TOOL_ITERATIONS = 6
DELEGATE_TIMEOUT_SECONDS = 180
MAX_DELEGATE_TOOL_CALLS_PER_TURN = 24
MAX_DELEGATE_INPUT_TOKENS_PER_TURN = 120_000
MAX_DELEGATE_OUTPUT_TOKENS_PER_TURN = 16_000

# Hard timeout for the smart-approval auxiliary reviewer. Smart approvals
# (Hermes v0.19 parity) must never block the user long: any verdict that
# takes longer than this falls back to the normal user prompt.
SMART_APPROVAL_TIMEOUT_SECONDS = 12

# Per-session cap on concurrent background delegate_task workers
# (Hermes v0.18 fan-out parity). Background workers use isolated budgets
# and deliver reports via session messages when complete; this bound
# prevents runaway fan-out from exhausting API quota.
MAX_BACKGROUND_WORKERS_PER_SESSION = 3

# Bounds for the per-session iteration override (``/max-iterations`` and
# ``GLM_ACP_MAX_TOOL_ITERATIONS``). The lower bound guards against accidental
# zero/negative; the upper bound guards against runaway loops.
MIN_TOOL_ITERATIONS = 1
MAX_TOOL_ITERATIONS_CEILING = 1000


def max_tool_iterations() -> int:
    """Resolve the default per-turn tool-call iteration cap.

    Precedence (highest to lowest):
    1. ``GLM_ACP_MAX_TOOL_ITERATIONS`` env var (ad-hoc runs, CI, scripts).
    2. The persistent user default in ``config_dir()/max-iterations.json``
       — the last value set via ``/max-iterations [N]``.
    3. The ``MAX_TOOL_ITERATIONS`` constant (50).

    The env var intentionally wins so CI/scripts/one-off runs are never
    silently overridden by a stored user preference.
    """
    raw = os.environ.get("GLM_ACP_MAX_TOOL_ITERATIONS")
    if raw:
        try:
            requested = int(str(raw).strip())
        except (TypeError, ValueError):
            return MAX_TOOL_ITERATIONS
        if requested < MIN_TOOL_ITERATIONS:
            return MIN_TOOL_ITERATIONS
        if requested > MAX_TOOL_ITERATIONS_CEILING:
            return MAX_TOOL_ITERATIONS_CEILING
        return requested
    persisted = _load_persisted_max_tool_iterations()
    if persisted is not None:
        return persisted
    return MAX_TOOL_ITERATIONS


def max_tool_iterations_path() -> Path:
    """Return the path to the persistent user default for the iteration cap."""
    return config_dir() / "max-iterations.json"


def _load_persisted_max_tool_iterations() -> int | None:
    """Return the persisted user default, or ``None`` if unset or malformed.

    The file is best-effort: a missing file, parse error, out-of-range value,
    or wrong schema all fall back to ``None`` so the caller uses the constant.
    """
    try:
        path = max_tool_iterations_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("value")
    # ``bool`` is a subclass of ``int`` — exclude it explicitly.
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value < MIN_TOOL_ITERATIONS or value > MAX_TOOL_ITERATIONS_CEILING:
        return None
    return value


def save_max_tool_iterations(value: int) -> int:
    """Persist ``value`` as the new user default and return the clamped result.

    The value is clamped to ``[MIN_TOOL_ITERATIONS, MAX_TOOL_ITERATIONS_CEILING]``
    and written atomically so concurrent readers never see a partial file.
    The env var ``GLM_ACP_MAX_TOOL_ITERATIONS`` always wins on read; this file
    only affects processes that do not set the env var.
    """
    clamped = max(MIN_TOOL_ITERATIONS, min(MAX_TOOL_ITERATIONS_CEILING, int(value)))
    path = max_tool_iterations_path()
    _secure_dir(path.parent)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"schema": 1, "value": clamped},
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _secure_file(temporary)
        os.replace(temporary, path)
        _secure_file(path)
    finally:
        temporary.unlink(missing_ok=True)
    return clamped


# Toggleable sidebar segments surfaced by the TUI ``/statusline`` command.
# Each entry is ``(segment_id, human_label)``; the order here is the order
# shown in the picker. ``load_statusline_config`` returns the full set on
# first run so the default UI matches the pre-feature behavior.
STATUSLINE_SEGMENTS: tuple[tuple[str, str], ...] = (
    ("state", "State (● Ready / Running)"),
    ("session_id", "Session ID preview"),
    ("model", "Model · reasoning"),
    ("endpoint", "API plan"),
    ("mode", "Mode · permissions"),
    ("context", "Context used/size"),
    ("tokens", "Token totals"),
    ("awareness", "Awareness indicator"),
    ("quota", "Quota windows"),
)
STATUSLINE_SEGMENT_IDS = frozenset(sid for sid, _ in STATUSLINE_SEGMENTS)


def statusline_path() -> Path:
    """Return the path to the persistent user default for statusline segments."""
    return config_dir() / "statusline.json"


def load_statusline_config() -> set[str]:
    """Return the set of enabled statusline segment IDs.

    Falls back to ``STATUSLINE_SEGMENT_IDS`` (everything enabled) when the
    file is missing, malformed, or contains unknown segments — so the
    default UI matches the pre-feature behavior and a corrupt config can
    never wipe the sidebar.
    """
    try:
        payload = json.loads(statusline_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return set(STATUSLINE_SEGMENT_IDS)
    if not isinstance(payload, dict):
        return set(STATUSLINE_SEGMENT_IDS)
    raw = payload.get("segments")
    if not isinstance(raw, list):
        return set(STATUSLINE_SEGMENT_IDS)
    enabled = {sid for sid in raw if isinstance(sid, str) and sid in STATUSLINE_SEGMENT_IDS}
    # Unknown/empty sets fall back to "all visible" rather than blank.
    return enabled or set(STATUSLINE_SEGMENT_IDS)


def save_statusline_config(segments: set[str]) -> set[str]:
    """Persist ``segments`` as the new user default and return the cleaned set.

    Unknown segment IDs are dropped. The file is written atomically so
    concurrent readers never see a partial file.
    """
    cleaned = {sid for sid in segments if sid in STATUSLINE_SEGMENT_IDS}
    enabled = cleaned or set(STATUSLINE_SEGMENT_IDS)
    payload = {"segments": sorted(enabled)}
    path = statusline_path()
    _secure_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _secure_file(temporary)
        os.replace(temporary, path)
        _secure_file(path)
    finally:
        temporary.unlink(missing_ok=True)
    return enabled


def theme_path() -> Path:
    """Return the path to the persistent user theme preference."""
    return config_dir() / "theme.json"


def screen_reader_path() -> Path:
    """Return the path to the persistent screen-reader-mode preference."""
    return config_dir() / "screen-reader.json"


def keybinds_path() -> Path:
    """Return the path to the persistent TUI keybinding overrides."""
    return config_dir() / "keybinds.json"


def load_keybinds_config() -> dict[str, str]:
    """Return persisted TUI keybinding overrides, or an empty mapping.

    The mapping is deliberately framework-agnostic: action names and key
    sequences are validated by the TUI when it applies them. A missing,
    malformed, or wrong-schema file is best-effort and simply means no
    overrides.
    """
    try:
        payload = json.loads(keybinds_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if not all(
        isinstance(action, str)
        and bool(action.strip())
        and isinstance(keys, str)
        and bool(keys.strip())
        for action, keys in payload.items()
    ):
        return {}
    return {action.strip(): keys.strip() for action, keys in payload.items()}


def save_keybinds_config(mapping: dict[str, str]) -> dict[str, str]:
    """Atomically persist TUI keybinding overrides and return the cleaned map."""
    cleaned = {
        str(action).strip(): str(keys).strip()
        for action, keys in mapping.items()
        if isinstance(action, str)
        and action.strip()
        and isinstance(keys, str)
        and keys.strip()
    }
    path = keybinds_path()
    _secure_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _secure_file(temporary)
        os.replace(temporary, path)
        _secure_file(path)
    finally:
        temporary.unlink(missing_ok=True)
    return cleaned


def load_screen_reader_config() -> bool:
    """Return whether the TUI should start in screen-reader (plain-text) mode.

    Best-effort: missing file, parse error, or wrong schema all fall back
    to ``False`` so users who never opted in get the default Rich-rendered
    experience. The on-disk schema is ``{"enabled": bool}``.
    """
    try:
        payload = json.loads(screen_reader_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    value = payload.get("enabled")
    if not isinstance(value, bool):
        return False
    return value


def save_screen_reader_config(enabled: bool) -> bool:
    """Persist ``enabled`` as the user default and return it.

    The value is written atomically so concurrent readers never see a
    partial file. The persisted shape is ``{"enabled": bool}``.
    """
    payload = {"enabled": bool(enabled)}
    path = screen_reader_path()
    _secure_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _secure_file(temporary)
        os.replace(temporary, path)
        _secure_file(path)
    finally:
        temporary.unlink(missing_ok=True)
    return bool(enabled)


def load_theme_config() -> str | None:
    """Return the persisted Textual theme name, or ``None`` if unset/invalid.

    Best-effort: missing file, parse error, or wrong schema all fall back
    to ``None`` so the caller uses Textual's default theme. The returned
    name is validated against Textual's available themes by the caller
    (the TUI App), not here — config stays framework-agnostic.
    """
    try:
        payload = json.loads(theme_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("theme")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def save_theme_config(theme_name: str) -> str:
    """Persist ``theme_name`` as the user default and return it.

    The value is written atomically so concurrent readers never see a
    partial file. Validation against Textual's available themes is the
    caller's responsibility — config persists whatever string is given.
    """
    cleaned = (theme_name or "").strip() or "textual-dark"
    payload = {"theme": cleaned}
    path = theme_path()
    _secure_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _secure_file(temporary)
        os.replace(temporary, path)
        _secure_file(path)
    finally:
        temporary.unlink(missing_ok=True)
    return cleaned


def _secure_dir(path: Path) -> None:
    """Create ``path`` (and parents) with 0700 perms; never raise on chmod."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _secure_file(path: Path) -> None:
    """Set 0600 perms on ``path``; never raise."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


# Retry configuration for transient API errors (429, 500, 502, 503, 504)
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds, exponential: 1s, 2s, 4s
RETRY_MAX_DELAY = 60.0
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Per-model max_tokens limits.  Models not listed here fall back to
# DEFAULT_MAX_TOKENS.
MAX_TOKENS_BY_MODEL: dict[str, int] = {
    "glm-4.5v": 16_384,
    "glm-4.6v": 32_768,
}

# Vision models accept multimodal message blocks. Current Z.ai vision models
# support standard thinking; reasoning_effort remains GLM-5.2-only.
VISION_MODELS = frozenset({"glm-5v-turbo", "glm-4.5v", "glm-4.6v"})
THINKING_UNSUPPORTED_MODELS: frozenset[str] = frozenset()

# --- API endpoints (plans) ---
# The user can switch between these from the chat dropdown so they're not
# locked into a single plan.  Each maps to a different Z.ai base URL.
API_ENDPOINTS: dict[str, dict[str, str]] = {
    "coding": {
        "name": "Coding Plan",
        "description": "Z.ai Coding Plan — GLM-5.2, GLM-5-Turbo, GLM-4.7 (default)",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
    },
    "standard": {
        "name": "Standard API",
        "description": "Z.ai standard API — pay-as-you-go, broader model access incl. vision",
        "base_url": "https://api.z.ai/api/paas/v4",
    },
    "bigmodel": {
        "name": "BigModel (CN)",
        "description": "BigModel open platform (China) — Chinese mainland endpoint",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
}
DEFAULT_API_ENDPOINT = "coding"

GENERATION_PROFILES: dict[str, dict[str, Any]] = {
    "balanced": {
        "name": "Balanced",
        "description": "Use Z.ai model defaults; recommended for general coding",
        "temperature": None,
        "top_p": None,
    },
    "precise": {
        "name": "Precise",
        "description": "Lower sampling variance for focused fixes and deterministic edits",
        "temperature": 0.7,
        "top_p": None,
    },
    "exploratory": {
        "name": "Exploratory",
        "description": "Broader nucleus sampling for ideation and alternative designs",
        "temperature": None,
        "top_p": 0.98,
    },
}
DEFAULT_GENERATION_PROFILE = "balanced"
DEFAULT_AUXILIARY_MODEL = "main"

# --- Token estimation (heuristic) ---
# _estimate_tokens uses 3.5 chars/token (code is denser than natural
# language which averages ~4 chars/token). The ratio is applied locally
# in GlmAcpAgent._estimate_tokens rather than referenced from here.

# --- Context compaction (Claude Code parity) ---
# Trigger compaction when estimated context usage exceeds this fraction of the
# model's context window.
COMPACTION_THRESHOLD = 0.85
# Number of most-recent messages to preserve verbatim after compaction.
COMPACTION_KEEP_RECENT = 4
# Max tokens for the summarization call itself.
COMPACTION_SUMMARY_MAX_TOKENS = 16_384
CONTEXT_PRESSURE_THRESHOLDS = (0.60, 0.75, 0.85)
MAX_COMPACTION_QUALITY_HISTORY = 20
COMPACTION_QUALITY_DECLINE = 0.15

# Context window sizes in tokens, keyed by model id.
CONTEXT_WINDOW_TOKENS: dict[str, int] = {
    "glm-5.2": 1_000_000,
    "glm-5-turbo": 200_000,
    "glm-4.7": 200_000,
    "glm-5v-turbo": 200_000,
    "glm-4.5v": 65_536,
    "glm-4.6v": 131_072,
}

COMPACTION_SYSTEM_PROMPT = """\
You are a conversation summarizer for an AI coding assistant. Your job is to \
create a concise but information-dense summary of the conversation so far, \
so that the assistant can continue working with full context after the older \
messages are compacted.

Your summary MUST preserve:
1. The user's original goal and any refined requirements
2. Key decisions made and their rationale
3. Files that were read, created, or modified — include paths and a brief \
description of their current state / important contents
4. Any errors encountered and how they were resolved (or remain unresolved)
5. Pending tasks or next steps that were planned
6. Any important code snippets, function signatures, or configuration values \
that are still relevant
7. Tool results that contain critical information (e.g. test output, command \
results, search results)

Write the summary as a clear, structured document. Be specific — include \
actual file paths, function names, error messages, and values. Do not \
hallucinate information that was not in the conversation. If something is \
uncertain, note the uncertainty.

Format:
## Goal
...
## Work Done
...
## Key Files
...
## Decisions
...
## Pending / Next Steps
...
## Important Context
...
"""

COMPACTION_USER_PREFIX = (
    "Here is the conversation to summarize. Produce a comprehensive summary "
    "following the structure in your instructions:\n\n---\n\n"
)

MODELS: dict[str, dict[str, Any]] = {
    "glm-5.2": {
        "name": "GLM-5.2 (Flagship)",
        "description": (
            "Latest flagship — maximum reasoning, coding, and long-horizon agentic tasks"
        ),
        "context_window": "1M",
        "plans": ["coding", "standard", "bigmodel"],
    },
    "glm-5-turbo": {
        "name": "GLM-5-Turbo",
        "description": "Flagship model optimized for speed — complex tasks with lower latency",
        "context_window": "200K",
        "plans": ["coding", "standard", "bigmodel"],
    },
    "glm-4.7": {
        "name": "GLM-4.7",
        "description": "Balanced model for daily development and routine tasks",
        "context_window": "200K",
        "plans": ["coding", "standard", "bigmodel"],
    },
    "glm-5v-turbo": {
        "name": "GLM-5V-Turbo (Vision Coding)",
        "description": "Multimodal coding model for screenshots, video, UI, and agent workflows",
        "context_window": "200K",
        "plans": ["standard", "bigmodel"],
    },
    "glm-4.5v": {
        "name": "GLM-4.5V (Vision)",
        "description": "Vision-capable — analyze screenshots, diagrams, charts",
        "context_window": "64K",
        "plans": ["standard", "bigmodel"],
    },
    "glm-4.6v": {
        "name": "GLM-4.6V (Vision)",
        "description": (
            "Vision model — newer vision model with improved OCR and image understanding"
        ),
        "context_window": "128K",
        "plans": ["standard", "bigmodel"],
    },
}

THOUGHT_LEVELS: dict[str, dict[str, Any]] = {
    "disabled": {
        "name": "Off",
        "description": "No reasoning — fast responses for simple tasks",
        "thinking_type": "disabled",
        "reasoning_effort": None,
        "models": None,  # None = all models
    },
    "enabled": {
        "name": "Standard",
        "description": "Full reasoning traces streamed live",
        "thinking_type": "enabled",
        "reasoning_effort": None,
        "models": None,
    },
    "high": {
        "name": "Deep · High",
        "description": "Deeper multi-step reasoning for complex tasks (GLM-5.2 only)",
        "thinking_type": "enabled",
        "reasoning_effort": "high",
        "models": ["glm-5.2"],
    },
    "max": {
        "name": "Deep · Max",
        "description": "Maximum reasoning depth — deepest analysis (GLM-5.2 only)",
        "thinking_type": "enabled",
        "reasoning_effort": "max",
        "models": ["glm-5.2"],
    },
}


def thought_levels_for_model(model: str) -> dict[str, dict[str, Any]]:
    """Return the subset of thought levels available for the given model.

    Deep reasoning levels are restricted to models that list them.
    """
    return {k: v for k, v in THOUGHT_LEVELS.items() if v["models"] is None or model in v["models"]}


def models_for_plan(plan: str) -> dict[str, dict[str, Any]]:
    """Return the subset of models available on the given API plan."""
    return {model_id: info for model_id, info in MODELS.items() if plan in info.get("plans", [])}


# Tools that modify the filesystem or execute commands — these require
# user permission when the session is in "ask" mode and are blocked in
# "read" mode.
DESTRUCTIVE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "apply_patch",
        "apply_patch_set",
        "run_command",
        "store_memory",
        "store_user_profile",
        "forget_memory",
        "learn_skill",
        "forget_skill",
        "manage_skill",
        "curate_skills",
        "manage_skill_bundle",
        "evolve_skill",
        "delegate_task",
        "cronjob",
        "mcp_call",
        "mcp_list_tools",
        "vision_analyze",
        "browser_ui",
        "run_workflow",
        "plugin_package",
        "worktree_worker",
        "failure_corpus",
        "rollback",
    }
)

AUTH_METHOD_ID = "zai-api-key-setup"
CONFIG_DIR_ENV = "GLM_ACP_CONFIG_DIR"
CREDENTIALS_FILENAME = "credentials.json"
PERSIST_REASONING_ENV = "GLM_ACP_PERSIST_REASONING"


def persist_reasoning() -> bool:
    """Whether exact reasoning traces may be written to session storage."""
    return os.environ.get(PERSIST_REASONING_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def config_dir() -> Path:
    """Return the per-user configuration directory without creating it."""
    from .profiles import profile_path

    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return profile_path(Path(override).expanduser())
    if os.name == "nt" and os.environ.get("APPDATA"):
        return profile_path(Path(os.environ["APPDATA"]) / "glm-acp")
    if sys_platform() == "darwin":
        return profile_path(Path.home() / "Library" / "Application Support" / "glm-acp")
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    return profile_path(
        Path(xdg_config_home) / "glm-acp"
        if xdg_config_home
        else Path.home() / ".config" / "glm-acp"
    )


def sys_platform() -> str:
    """Small indirection that keeps platform selection easy to test."""
    import sys

    return sys.platform


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILENAME


def load_stored_api_key() -> str | None:
    """Load the locally stored API key, returning None for invalid state."""
    try:
        payload = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    key = payload.get("zai_api_key") if isinstance(payload, dict) else None
    return key.strip() if isinstance(key, str) and key.strip() else None


def store_api_key(key: str) -> Path:
    """Atomically store an API key in a user-only configuration file."""
    normalized = key.strip()
    if not normalized:
        raise ValueError("API key cannot be empty")

    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass

    target = credentials_path()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=directory,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"zai_api_key": normalized}, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def has_api_key() -> bool:
    return bool(
        os.environ.get("ZAI_API_KEY") or os.environ.get("Z_AI_API_KEY") or load_stored_api_key()
    )


def get_api_key() -> str:
    key = os.environ.get("ZAI_API_KEY") or os.environ.get("Z_AI_API_KEY") or load_stored_api_key()
    if not key:
        raise RuntimeError(
            "Z.ai API credentials are required. Run `glm-acp --setup` or set "
            "ZAI_API_KEY. Get your key at https://z.ai/"
        )
    return key
