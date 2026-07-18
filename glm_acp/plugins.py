"""Hash-pinned declarative plugin packages; executable code is intentionally unsupported."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .config import config_dir

MAX_PLUGIN_FILES = 32
MAX_PLUGIN_BYTES = 2 * 1024 * 1024
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_PERMISSIONS = {"prompt_context", "policy_templates", "workflows"}


class PluginError(RuntimeError):
    pass


class PluginRegistry:
    """Install and verify data-only packages containing prompts, policies, or workflows."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or config_dir() / "plugins"

    @staticmethod
    def _manifest(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PluginError(f"Invalid plugin manifest: {error}") from error
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise PluginError("Plugin manifest requires schema 1")
        plugin_id = str(payload.get("id", ""))
        if not _ID.fullmatch(plugin_id):
            raise PluginError(
                "Plugin id must use lowercase letters, digits, underscores, or dashes"
            )
        files = payload.get("files")
        if not isinstance(files, dict) or not 1 <= len(files) <= MAX_PLUGIN_FILES:
            raise PluginError(f"Plugin files must contain 1-{MAX_PLUGIN_FILES} hash entries")
        permissions = payload.get("permissions", [])
        if not isinstance(permissions, list) or not all(
            isinstance(value, str) and value in _PERMISSIONS for value in permissions
        ):
            raise PluginError("Plugin permissions contain an unsupported capability")
        if payload.get("prompt_files") and "prompt_context" not in permissions:
            raise PluginError("prompt_files require the prompt_context permission")
        return payload

    @staticmethod
    def _verified_files(root: Path, manifest: dict[str, Any]) -> list[Path]:
        files: list[Path] = []
        total = 0
        for relative, expected in manifest["files"].items():
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise PluginError("Plugin file hashes must be string pairs")
            path = (root / relative).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError as error:
                raise PluginError(f"Plugin file escapes package: {relative}") from error
            if path.is_symlink() or not path.is_file():
                raise PluginError(f"Plugin file missing or unsafe: {relative}")
            data = path.read_bytes()
            total += len(data)
            if total > MAX_PLUGIN_BYTES:
                raise PluginError("Plugin package exceeds 2 MiB")
            if hashlib.sha256(data).hexdigest() != expected.lower():
                raise PluginError(f"Plugin hash mismatch: {relative}")
            if path.suffix not in {".json", ".md", ".toml", ".yaml", ".yml"}:
                raise PluginError(f"Executable plugin content is not supported: {relative}")
            files.append(path)
        return files

    def install(self, manifest_path: Path) -> dict[str, Any]:
        manifest_path = manifest_path.resolve()
        manifest = self._manifest(manifest_path)
        source = manifest_path.parent
        files = self._verified_files(source, manifest)
        plugin_id = manifest["id"]
        target = self.base_dir / plugin_id
        temporary = self.base_dir / f".{plugin_id}.tmp"
        backup = self.base_dir / f".{plugin_id}.backup"
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self.base_dir, 0o700)
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            for path in files:
                relative = path.relative_to(source)
                destination = temporary / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())
                if os.name != "nt":
                    os.chmod(destination, 0o600)
            manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            (temporary / "plugin.json").write_bytes(manifest_bytes)
            (temporary / "manifest.sha256").write_text(
                hashlib.sha256(manifest_bytes).hexdigest() + "\n", encoding="ascii"
            )
            if os.name != "nt":
                os.chmod(temporary / "plugin.json", 0o600)
                os.chmod(temporary / "manifest.sha256", 0o600)
            if target.exists():
                if backup.exists():
                    shutil.rmtree(backup)
                os.replace(target, backup)
                try:
                    os.replace(temporary, target)
                except OSError:
                    os.replace(backup, target)
                    raise
                else:
                    shutil.rmtree(backup)
            else:
                os.replace(temporary, target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return {"id": plugin_id, "version": manifest.get("version", "0"), "files": len(files)}

    def verify(self, plugin_id: str) -> dict[str, Any]:
        if not _ID.fullmatch(plugin_id):
            raise PluginError("Invalid plugin id")
        root = self.base_dir / plugin_id
        try:
            expected_manifest = (root / "manifest.sha256").read_text(encoding="ascii").strip()
            manifest_bytes = (root / "plugin.json").read_bytes()
        except OSError as error:
            raise PluginError(f"Plugin manifest pin is unavailable: {error}") from error
        if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest:
            raise PluginError("Plugin manifest hash mismatch")
        manifest = self._manifest(root / "plugin.json")
        if manifest.get("id") != plugin_id:
            raise PluginError("Plugin directory and manifest id differ")
        files = self._verified_files(root, manifest)
        return {
            "id": plugin_id,
            "version": manifest.get("version", "0"),
            "verified": True,
            "files": len(files),
        }

    def list(self) -> list[dict[str, Any]]:
        results = []
        if not self.base_dir.exists():
            return results
        for path in sorted(self.base_dir.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                results.append(self.verify(path.name))
            except PluginError as error:
                results.append({"id": path.name, "verified": False, "error": str(error)})
        return results[:100]

    def prompt_fragments(self) -> str:
        """Load only verified, explicitly declared Markdown prompt fragments."""
        fragments: list[str] = []
        for item in self.list():
            if not item.get("verified"):
                continue
            root = self.base_dir / str(item["id"])
            manifest = self._manifest(root / "plugin.json")
            prompt_files = manifest.get("prompt_files", [])
            if not isinstance(prompt_files, list) or "prompt_context" not in manifest.get(
                "permissions", []
            ):
                continue
            verified = {
                path.relative_to(root).as_posix() for path in self._verified_files(root, manifest)
            }
            for relative in prompt_files[:8]:
                if relative in verified and str(relative).endswith(".md"):
                    fragments.append((root / str(relative)).read_text(encoding="utf-8")[:8_000])
        return "\n\n".join(fragments)[:32_000]
