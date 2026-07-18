"""Conflict-aware lifecycle for opt-in Git worktree implementation workers."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import config_dir

MAX_WORKER_PATCH_BYTES = 8 * 1024 * 1024
MAX_WORKER_PATHS = 200


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or config_dir() / "worktrees"

    @staticmethod
    def _git(cwd: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def _git_patch(
        cwd: Path, args: list[str], patch: bytes, timeout: int = 30
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            input=patch,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def _after_apply(_repo: Path, _paths: list[str]) -> None:
        """Fault-injection seam used by offline recovery tests."""

    def create(self, cwd: str, base_ref: str = "HEAD") -> dict[str, str]:
        root = Path(cwd).resolve()
        probe = self._git(root, ["rev-parse", "--show-toplevel"])
        if probe.returncode != 0:
            raise WorktreeError("Implementation workers require a Git repository")
        repo = Path(probe.stdout.strip()).resolve()
        resolved = self._git(repo, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
        if resolved.returncode != 0:
            raise WorktreeError("Worker base_ref is not a valid commit")
        base_sha = resolved.stdout.strip()
        identity = hashlib.sha256(str(repo).encode()).hexdigest()[:16]
        worker_id = "worker-" + uuid4().hex[:10]
        path = self.base_dir / identity / worker_id
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self._git(
            repo,
            [
                "worktree",
                "add",
                "--detach",
                "--lock",
                "--reason",
                "glm-acp implementation worker",
                str(path),
                base_sha,
            ],
        )
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip() or "Could not create Git worktree")
        return {"id": worker_id, "path": str(path), "repo": str(repo), "base_ref": base_sha}

    def _validate(self, repo: str, path: str) -> tuple[Path, Path]:
        repo_path = Path(repo).resolve()
        worker = Path(path).resolve()
        try:
            worker.relative_to(self.base_dir.resolve())
        except ValueError as error:
            raise WorktreeError("Worker path is outside the managed worktree directory") from error
        if worker.is_symlink() or not worker.is_dir():
            raise WorktreeError("Worker worktree is missing or unsafe")
        root = self._git(worker, ["rev-parse", "--show-toplevel"])
        if root.returncode != 0 or Path(root.stdout.strip()).resolve() != worker:
            raise WorktreeError("Worker path is not a Git worktree root")
        listing = self._git(repo_path, ["worktree", "list", "--porcelain"])
        registered = {
            Path(line.partition(" ")[2]).resolve()
            for line in listing.stdout.splitlines()
            if line.startswith("worktree ")
        }
        if worker not in registered:
            raise WorktreeError("Worker is not registered with the target repository")
        return repo_path, worker

    def _paths(self, worker: Path, base_ref: str) -> list[str]:
        changed = self._git(worker, ["diff", "--name-only", "-z", base_ref, "--"])
        if changed.returncode != 0:
            raise WorktreeError(changed.stderr.strip() or "Could not enumerate worker changes")
        untracked = self._git(worker, ["ls-files", "--others", "--exclude-standard", "-z"])
        if untracked.returncode != 0:
            raise WorktreeError(untracked.stderr.strip() or "Could not enumerate worker files")
        paths = [value for value in (changed.stdout + untracked.stdout).split("\0") if value]
        paths = list(dict.fromkeys(paths))
        if len(paths) > MAX_WORKER_PATHS:
            raise WorktreeError(f"Worker patch exceeds the {MAX_WORKER_PATHS}-path limit")
        for relative in paths:
            candidate = (worker / relative).resolve(strict=False)
            try:
                candidate.relative_to(worker)
            except ValueError as error:
                raise WorktreeError(f"Worker change escapes its worktree: {relative}") from error
        return paths

    def _patch(self, worker: Path, base_ref: str) -> tuple[bytes, list[str]]:
        resolved = self._git(worker, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
        if resolved.returncode != 0:
            raise WorktreeError("Worker base_ref is not a valid commit")
        base_sha = resolved.stdout.strip()
        paths = self._paths(worker, base_sha)
        tracked = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--binary", base_sha, "--"],
            cwd=worker,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if tracked.returncode != 0:
            raise WorktreeError(tracked.stderr.decode(errors="replace") or "Could not read diff")
        patch = bytearray(tracked.stdout)
        untracked = self._git(worker, ["ls-files", "--others", "--exclude-standard"])
        for relative in [value for value in untracked.stdout.splitlines() if value]:
            addition = subprocess.run(
                ["git", "diff", "--no-index", "--binary", "--", "/dev/null", relative],
                cwd=worker,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if addition.returncode not in {0, 1}:
                raise WorktreeError(
                    addition.stderr.decode(errors="replace") or f"Could not stage {relative}"
                )
            patch.extend(addition.stdout)
            if len(patch) > MAX_WORKER_PATCH_BYTES:
                raise WorktreeError("Worker patch exceeds 8 MiB")
        return bytes(patch), paths

    def inspect(self, repo: str, path: str, base_ref: str) -> dict[str, Any]:
        _, worker = self._validate(repo, path)
        patch, paths = self._patch(worker, base_ref)
        status = self._git(worker, ["status", "--short"])
        return {
            "path": str(worker),
            "base_ref": base_ref,
            "diff_sha256": hashlib.sha256(patch).hexdigest(),
            "patch_bytes": len(patch),
            "paths": paths,
            "status": status.stdout.splitlines()[:MAX_WORKER_PATHS],
            "diff": patch.decode("utf-8", errors="replace")[:60_000],
        }

    def diff(self, path: str, base_ref: str = "HEAD") -> str:
        worker = Path(path).resolve()
        patch, _ = self._patch(worker, base_ref)
        return patch.decode("utf-8", errors="replace")[:64_000]

    def promote(self, repo: str, path: str, base_ref: str, expected_sha256: str) -> dict[str, Any]:
        repo_path, worker = self._validate(repo, path)
        patch, paths = self._patch(worker, base_ref)
        digest = hashlib.sha256(patch).hexdigest()
        if not patch or not paths:
            raise WorktreeError("Worker has no changes to promote")
        if digest != expected_sha256.lower():
            raise WorktreeError("Worker diff changed after review; inspect it again")
        check = self._git_patch(repo_path, ["apply", "--check", "--binary", "-"], patch)
        if check.returncode != 0:
            detail = check.stderr.decode(errors="replace").strip()
            raise WorktreeError(f"Worker promotion conflicts with the primary workspace: {detail}")
        applied = self._git_patch(repo_path, ["apply", "--binary", "-"], patch)
        if applied.returncode != 0:
            detail = applied.stderr.decode(errors="replace").strip()
            raise WorktreeError(f"Worker promotion failed without applying changes: {detail}")
        try:
            self._after_apply(repo_path, paths)
        except Exception as error:
            reversed_patch = self._git_patch(
                repo_path, ["apply", "--reverse", "--binary", "-"], patch
            )
            if reversed_patch.returncode != 0:
                raise WorktreeError(
                    "Worker promotion faulted and automatic rollback failed; inspect the workspace"
                ) from error
            raise WorktreeError(
                "Worker promotion faulted; all applied changes were rolled back"
            ) from error
        return {
            "promoted": True,
            "diff_sha256": digest,
            "paths": [str(repo_path / relative) for relative in paths],
            "worker_preserved": str(worker),
        }

    def discard(self, repo: str, path: str, base_ref: str, expected_sha256: str) -> None:
        repo_path, worker = self._validate(repo, path)
        patch, _ = self._patch(worker, base_ref)
        if hashlib.sha256(patch).hexdigest() != expected_sha256.lower():
            raise WorktreeError("Worker diff changed after review; inspect it before discarding")
        unlock = self._git(repo_path, ["worktree", "unlock", str(worker)])
        if unlock.returncode != 0:
            raise WorktreeError(unlock.stderr.strip() or "Could not unlock worker worktree")
        removed = self._git(repo_path, ["worktree", "remove", "--force", str(worker)])
        if removed.returncode != 0:
            raise WorktreeError(removed.stderr.strip() or "Could not discard worker worktree")

    def remove_if_clean(self, repo: str, path: str) -> None:
        repo_path, worker = self._validate(repo, path)
        status = self._git(worker, ["status", "--porcelain"])
        if status.returncode != 0 or status.stdout.strip():
            raise WorktreeError(
                "Worker worktree is dirty; preserve or apply its diff before removal"
            )
        unlock = self._git(repo_path, ["worktree", "unlock", str(worker)])
        if unlock.returncode != 0:
            raise WorktreeError(unlock.stderr.strip() or "Could not unlock worker worktree")
        removed = self._git(repo_path, ["worktree", "remove", str(worker)])
        if removed.returncode != 0:
            raise WorktreeError(removed.stderr.strip() or "Could not remove worker worktree")
