"""Project roots, progressive instructions, and canonical verification facts."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .security import safe_context_text

MAX_CONTEXT_CHARS = 32_000
_ROOT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
)
_DIRECT_INSTRUCTIONS = (".hermes.md", "HERMES.md", "AGENTS.md", "CLAUDE.md", "GLM.md")


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def project_root(cwd: str | Path) -> Path:
    """Return the nearest git/manifest root or resolved cwd without a subprocess."""
    current = Path(cwd).expanduser().resolve()
    for candidate in (current, *current.parents):
        if any(_exists(candidate / marker) for marker in _ROOT_MARKERS):
            return candidate
    return current


def _bounded_text(path: Path, remaining: int) -> str:
    try:
        if not path.is_file() or path.is_symlink():
            return ""
        return path.read_text(encoding="utf-8")[:remaining]
    except (OSError, UnicodeDecodeError):
        return ""


def instruction_files(cwd: str | Path, targets: list[str] | None = None) -> list[Path]:
    """Discover instruction files from project root toward accessed target paths."""
    root = project_root(cwd)
    directories: set[Path] = {root}
    cwd_path = Path(cwd).expanduser().resolve()
    candidates = [cwd_path]
    for raw in targets or []:
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = cwd_path / path
            path = path.resolve()
            path = path if path.is_dir() else path.parent
            path.relative_to(root)
            candidates.append(path)
        except (OSError, ValueError):
            continue
    for target in candidates:
        try:
            relative = target.relative_to(root)
        except ValueError:
            continue
        cursor = root
        directories.add(cursor)
        for part in relative.parts:
            cursor /= part
            directories.add(cursor)

    ordered = sorted(directories, key=lambda item: (len(item.relative_to(root).parts), str(item)))
    found: list[Path] = []
    for directory in ordered:
        for name in _DIRECT_INSTRUCTIONS:
            path = directory / name
            if _is_file(path) and not path.is_symlink():
                found.append(path)
        cursor_rules = directory / ".cursorrules"
        if _is_file(cursor_rules) and not cursor_rules.is_symlink():
            found.append(cursor_rules)
        rules_dir = directory / ".cursor" / "rules"
        if _is_dir(rules_dir):
            try:
                found.extend(
                    path
                    for path in sorted(rules_dir.glob("*.mdc"))[:50]
                    if path.is_file() and not path.is_symlink()
                )
            except OSError:
                pass
    return list(dict.fromkeys(found))


def progressive_instructions(cwd: str, targets: list[str] | None = None) -> str:
    """Render bounded, promptware-scanned progressive project instructions."""
    root = project_root(cwd)
    remaining = MAX_CONTEXT_CHARS
    sections: list[str] = []
    for path in instruction_files(cwd, targets):
        text = _bounded_text(path, remaining)
        if not text:
            continue
        try:
            label = str(path.relative_to(root))
        except ValueError:
            label = path.name
        sections.append(f"### {label}\n{safe_context_text(text, label)}")
        remaining -= len(text)
        if remaining <= 0:
            break
    return "\n\n".join(sections)


@dataclass(frozen=True)
class ProjectFacts:
    root: str
    manifests: tuple[str, ...]
    package_managers: tuple[str, ...]
    verify_commands: tuple[str, ...]
    branch: str = ""
    dirty: bool = False

    def render(self) -> str:
        lines = [f"- Root: {self.root}"]
        if not self.manifests:
            lines.append("- no project files detected")
        if any(name in self.manifests for name in ("pyproject.toml", "setup.py", "setup.cfg")):
            lines.append("- Python project")
        if "package.json" in self.manifests:
            lines.append("- Node.js project")
        if "Cargo.toml" in self.manifests:
            lines.append("- Rust project")
        if "go.mod" in self.manifests:
            lines.append("- Go project")
        if self.manifests:
            lines.append(f"- Project manifests: {', '.join(self.manifests)}")
        if self.package_managers:
            lines.append(f"- Package managers: {', '.join(self.package_managers)}")
        if self.verify_commands:
            lines.append(f"- Canonical verification: {'; '.join(self.verify_commands)}")
        if self.branch:
            lines.append(f"- Git branch: {self.branch} ({'dirty' if self.dirty else 'clean'})")
        return "\n".join(lines)


def _read_small(path: Path, limit: int = 128_000) -> str:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def detect_project_facts(cwd: str | Path) -> ProjectFacts:
    """Detect stable project metadata and repository-defined verification commands."""
    root = project_root(cwd)
    manifest_names = (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Makefile",
    )
    manifests = tuple(name for name in manifest_names if _is_file(root / name))
    managers: list[str] = []
    locks = (
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("package-lock.json", "npm"),
        ("Cargo.lock", "cargo"),
        ("go.sum", "go"),
    )
    managers.extend(manager for lock, manager in locks if _is_file(root / lock))
    verify: list[str] = []
    pyproject = _read_small(root / "pyproject.toml")
    if "pyproject.toml" in manifests:
        runner = "uv run " if "uv" in managers else ""
        if "[tool.pytest" in pyproject or (root / "tests").is_dir():
            verify.append(f"{runner}pytest")
        if "[tool.ruff" in pyproject:
            verify.append(f"{runner}ruff check .")
        if "[tool.mypy" in pyproject:
            verify.append(f"{runner}mypy .")
    if "package.json" in manifests:
        try:
            scripts = json.loads(_read_small(root / "package.json") or "{}").get("scripts", {})
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        manager = next(
            (value for value in ("pnpm", "yarn", "bun", "npm") if value in managers), "npm"
        )
        for name in ("test", "lint", "typecheck", "check", "build"):
            if name in scripts:
                verify.append(f"{manager} run {name}")
    if "Cargo.toml" in manifests:
        verify.extend(("cargo test", "cargo check"))
    if "go.mod" in manifests:
        verify.append("go test ./...")
    makefile = _read_small(root / "Makefile")
    for name in ("test", "lint", "check", "verify", "build"):
        if re.search(rf"^{name}\s*:", makefile, re.MULTILINE):
            verify.append(f"make {name}")
    branch = ""
    dirty = False
    if _exists(root / ".git"):
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain=v1", "--branch"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if status.returncode == 0:
                lines = status.stdout.splitlines()
                branch = lines[0].removeprefix("## ").split("...")[0] if lines else ""
                dirty = len(lines) > 1
        except (OSError, subprocess.TimeoutExpired):
            pass
    return ProjectFacts(
        root=str(root),
        manifests=manifests,
        package_managers=tuple(dict.fromkeys(managers)),
        verify_commands=tuple(dict.fromkeys(verify)),
        branch=branch,
        dirty=dirty,
    )
