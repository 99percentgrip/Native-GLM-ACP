"""Safe lifecycle for opt-in Git worktree implementation workers."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from uuid import uuid4

from .config import config_dir


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
                base_ref,
            ],
        )
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip() or "Could not create Git worktree")
        return {"id": worker_id, "path": str(path), "repo": str(repo), "base_ref": base_sha}

    def diff(self, path: str, base_ref: str = "HEAD") -> str:
        root = Path(path).resolve()
        result = self._git(root, ["diff", "--no-ext-diff", "--binary", base_ref, "--"])
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip() or "Could not read worker diff")
        untracked = self._git(root, ["ls-files", "--others", "--exclude-standard"])
        suffix = ""
        if untracked.stdout.strip():
            patches: list[str] = []
            for relative in untracked.stdout.splitlines()[:50]:
                candidate = (root / relative).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    continue
                data = candidate.read_bytes()
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    patches.append(
                        f"Binary untracked file: {relative} "
                        f"(sha256:{hashlib.sha256(data).hexdigest()})"
                    )
                    continue
                lines = text.splitlines()
                body = "\n".join("+" + line for line in lines)
                patches.append(
                    f"diff --git a/{relative} b/{relative}\n"
                    f"new file mode 100644\n--- /dev/null\n+++ b/{relative}\n"
                    f"@@ -0,0 +1,{len(lines)} @@\n{body}\n"
                )
            suffix = "\n" + "\n".join(patches)
        return result.stdout[:60_000] + suffix[:4_000]

    def remove_if_clean(self, repo: str, path: str) -> None:
        repo_path = Path(repo).resolve()
        worker = Path(path).resolve()
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
