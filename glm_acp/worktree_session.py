"""Managed Git worktrees for parallel terminal sessions.

Unlike implementation-worker worktrees, these directories are user-facing
session roots.  They stay inside ``<repository>/.glm-acp-worktrees`` and are
never removed while dirty unless the caller explicitly requests ``force``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeSessionError(RuntimeError):
    """A managed worktree session could not be created, listed, or removed."""


@dataclass(frozen=True)
class WorktreeInfo:
    """One entry returned by ``git worktree list --porcelain``."""

    path: Path
    head: str = ""
    branch: str | None = None
    detached: bool = False
    locked: bool = False

    @property
    def name(self) -> str:
        return self.path.name


_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise WorktreeSessionError(
            "Git is unavailable; install Git to manage worktree sessions"
        ) from error


def _repo_root(cwd: str | Path | None) -> Path:
    start = Path(cwd or Path.cwd()).expanduser().resolve()
    result = _git(start, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0 or not result.stdout.strip():
        raise WorktreeSessionError("Worktree sessions require a Git repository")
    return Path(result.stdout.strip()).resolve()


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not _SAFE_NAME.fullmatch(cleaned):
        raise WorktreeSessionError(
            "Session names must be 1-64 characters: letters, digits, '.', '_' or '-'"
        )
    return cleaned


def _managed_dir(repo: Path) -> Path:
    return repo / ".glm-acp-worktrees"


def create_worktree_session(
    base_ref: str, name: str, cwd: str | Path | None = None
) -> Path:
    """Create a detached managed worktree from ``base_ref`` and return its path."""
    repo = _repo_root(cwd)
    safe_name = _validate_name(name)
    resolved = _git(repo, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
    if resolved.returncode != 0:
        raise WorktreeSessionError("Session base ref is not a valid commit")
    target = _managed_dir(repo) / safe_name
    if target.exists():
        raise WorktreeSessionError(f"A worktree session named {safe_name!r} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = _git(repo, ["worktree", "add", "--detach", str(target), resolved.stdout.strip()])
    if result.returncode != 0:
        raise WorktreeSessionError(result.stderr.strip() or "Could not create worktree session")
    return target.resolve()


def list_worktree_sessions(cwd: str | Path | None = None) -> list[WorktreeInfo]:
    """List managed worktree sessions in the current repository."""
    repo = _repo_root(cwd)
    result = _git(repo, ["worktree", "list", "--porcelain"])
    if result.returncode != 0:
        raise WorktreeSessionError(result.stderr.strip() or "Could not list worktree sessions")
    managed = _managed_dir(repo).resolve()
    entries: list[WorktreeInfo] = []
    current: dict[str, str | bool] = {}

    def finish() -> None:
        path_value = str(current.get("path") or "")
        if not path_value:
            return
        path = Path(path_value).resolve()
        try:
            path.relative_to(managed)
        except ValueError:
            return
        entries.append(
            WorktreeInfo(
                path=path,
                head=str(current.get("head") or ""),
                branch=str(current["branch"]) if current.get("branch") else None,
                detached=bool(current.get("detached")),
                locked=bool(current.get("locked")),
            )
        )

    for line in [*result.stdout.splitlines(), ""]:
        if not line:
            finish()
            current = {}
        elif line.startswith("worktree "):
            current["path"] = line.partition(" ")[2]
        elif line.startswith("HEAD "):
            current["head"] = line.partition(" ")[2]
        elif line.startswith("branch "):
            current["branch"] = line.partition(" ")[2]
        elif line == "detached":
            current["detached"] = True
        elif line.startswith("locked"):
            current["locked"] = True
    return entries


def remove_worktree_session(
    name: str, cwd: str | Path | None = None, *, force: bool = False
) -> None:
    """Remove a managed session, refusing dirty worktrees by default."""
    repo = _repo_root(cwd)
    target = (_managed_dir(repo) / _validate_name(name)).resolve()
    if target not in {item.path for item in list_worktree_sessions(repo)}:
        raise WorktreeSessionError(f"No managed worktree session named {name!r}")
    if not force:
        status = _git(target, ["status", "--porcelain"])
        if status.returncode != 0:
            raise WorktreeSessionError("Could not inspect worktree session state")
        if status.stdout.strip():
            raise WorktreeSessionError("Worktree session is dirty; refuse removal without force")
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    result = _git(repo, [*args, str(target)])
    if result.returncode != 0:
        raise WorktreeSessionError(result.stderr.strip() or "Could not remove worktree session")
