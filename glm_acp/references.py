"""Bounded explicit context-reference expansion."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .security import wrap_untrusted_output
from .tools import Sandbox, ToolError

MAX_REFERENCES = 12
MAX_REFERENCE_CHARS = 48_000
MAX_FILE_CHARS = 16_000
MAX_FOLDER_FILES = 40
_REFERENCE = re.compile(r"(?<!\w)@(file|folder|symbol):([^\s]+)|(?<!\w)@diff\b")
_IGNORED = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
_SENSITIVE_NAMES = {
    ".env",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
}


def _sensitive(path: Path) -> bool:
    return (
        any(part == ".ssh" for part in path.parts)
        or path.name in _SENSITIVE_NAMES
        or (path.name.startswith(".env."))
    )


def _text(path: Path, limit: int = MAX_FILE_CHARS) -> str:
    data = path.read_bytes()
    if b"\0" in data:
        return "[binary file omitted]"
    try:
        value = data.decode("utf-8")
    except UnicodeDecodeError:
        return "[non-UTF-8 file omitted]"
    return value[:limit] + ("\n… [truncated]" if len(value) > limit else "")


def _folder(path: Path) -> str:
    if not path.is_dir():
        raise ToolError(f"Reference folder not found: {path}")
    output: list[str] = []
    count = 0
    for current, dirs, files in os.walk(path):
        dirs[:] = [name for name in sorted(dirs) if name not in _IGNORED]
        for name in sorted(files):
            file_path = Path(current) / name
            if file_path.is_symlink() or not file_path.is_file() or _sensitive(file_path):
                continue
            relative = file_path.relative_to(path)
            body = _text(file_path, 2_000)
            output.append(f"--- {relative.as_posix()} ---\n{body}")
            count += 1
            if count >= MAX_FOLDER_FILES:
                output.append("… [folder file limit reached]")
                return "\n".join(output)
    return "\n".join(output) or "[empty folder]"


def _symbol(root: Path, symbol: str) -> str:
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_.$:-]{0,199}", symbol):
        raise ToolError(f"Invalid symbol reference: {symbol}")
    keyword = r"(?:class|def|function|interface|type|struct|enum|trait|const|let|var)"
    pattern = rf"{keyword}\s+{re.escape(symbol.split('.')[-1])}\b"
    rg = shutil_which("rg")
    if not rg:
        return f"Symbol lookup unavailable: ripgrep is not installed ({symbol})"
    result = subprocess.run(
        [rg, "-n", "--glob", "!**/.git/**", "--glob", "!**/node_modules/**", pattern, "."],
        cwd=root,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
        check=False,
    )
    return (result.stdout.strip() or f"No definition-like match found for {symbol}")[
        :MAX_FILE_CHARS
    ]


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def _diff(root: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--"],
        cwd=root,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
        check=False,
        env={"PATH": os.environ.get("PATH", "")},
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--binary", "--"],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
    if result.returncode != 0:
        return "Git diff unavailable for this workspace"
    return (result.stdout or "[no tracked working-tree diff]")[:MAX_FILE_CHARS]


def expand_references(content: str, sandbox: Sandbox) -> tuple[str, list[str]]:
    """Append bounded, untrusted reference bodies and return addressed paths."""
    matches = list(_REFERENCE.finditer(content))
    if not matches:
        return content, []
    if len(matches) > MAX_REFERENCES:
        raise ToolError(f"At most {MAX_REFERENCES} @ references are allowed per prompt")
    root = sandbox.roots[0]
    sections: list[str] = []
    targets: list[str] = []
    total = 0
    for match in matches:
        kind = match.group(1) or "diff"
        value = (match.group(2) or "").rstrip(".,;)")
        if kind == "file":
            path = sandbox.resolve(value)
            if not path.is_file():
                raise ToolError(f"Reference file not found: {path}")
            if _sensitive(path):
                raise ToolError(f"Sensitive file cannot be expanded as a context reference: {path}")
            body = _text(path)
            targets.append(str(path))
        elif kind == "folder":
            path = sandbox.resolve(value)
            body = _folder(path)
            targets.append(str(path))
        elif kind == "symbol":
            body = _symbol(root, value)
        else:
            body = _diff(root)
        remaining = MAX_REFERENCE_CHARS - total
        if remaining <= 0:
            break
        body = body[:remaining]
        total += len(body)
        sections.append(f"Reference @{kind}{':' + value if value else ''}:\n{body}")
    expanded = "\n\n".join(sections)
    return content + "\n\nResolved explicit context references:\n" + wrap_untrusted_output(
        expanded, "explicit-references"
    ), targets
