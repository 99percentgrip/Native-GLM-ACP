"""Declarative runtime for hash-pinned, verified plugin manifests.

Python code is intentionally not loaded.  This keeps plugin packages within
the existing data-only, Ed25519-verifiable security contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .plugins import PluginError, PluginRegistry


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    commands: list[str]
    widgets: list[str]
    output_watchers: list[str]
    entry_point: str = ""


class PluginRuntime:
    """Expose verified declarative plugin metadata to one TUI application."""

    def __init__(self, registry: PluginRegistry | None = None) -> None:
        self.registry = registry or PluginRegistry()
        self.loaded: dict[str, PluginManifest] = {}
        self._command_owners: dict[str, str] = {}
        self._watchers: dict[str, Any] = {}

    def load(self, manifest_path: Path) -> PluginManifest:
        root = manifest_path.parent
        plugin_id = root.name
        verified = self.registry.verify(plugin_id)
        if not verified.get("verified"):
            raise PluginError("Plugin verification failed")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PluginError(f"Invalid plugin manifest: {error}") from error
        commands = payload.get("commands", [])
        widgets = payload.get("widgets", [])
        watchers = payload.get("output_watchers", [])
        if not all(
            isinstance(values, list) and all(isinstance(item, str) for item in values)
            for values in (commands, widgets, watchers)
        ):
            raise PluginError("Plugin runtime fields must be string lists")
        entry_point = payload.get("entry_point", "")
        if not isinstance(entry_point, str):
            raise PluginError("Plugin entry_point must be a string")
        manifest = PluginManifest(
            plugin_id,
            str(payload.get("version", "0")),
            commands,
            widgets,
            watchers,
            entry_point,
        )
        self.loaded[plugin_id] = manifest
        return manifest

    def register_commands(self, plugin: PluginManifest, app: Any) -> None:
        for command in plugin.commands:
            name, _, description = command.partition(" ")
            if not name.startswith("/") or not name[1:].replace("-", "").isalnum():
                raise PluginError(
                    "Plugin command must start with / and use letters, digits, or dashes"
                )
            app._slash_commands[name] = description or f"Plugin command from {plugin.name}"
            self._command_owners[name] = plugin.name

    def unload(self, plugin_name: str, app: Any) -> None:
        for command, owner in list(self._command_owners.items()):
            if owner == plugin_name:
                app._slash_commands.pop(command, None)
                del self._command_owners[command]
        self.loaded.pop(plugin_name, None)

    def owner_for(self, command: str) -> str | None:
        """Return the verified plugin that registered a live slash command."""
        return self._command_owners.get(command)

    def hot_reload(self, plugin_dir: Path, app: Any) -> None:
        """Reload on demand after re-running every registry verification gate."""
        name = plugin_dir.name
        self.unload(name, app)
        self.register_commands(self.load(plugin_dir / "plugin.json"), app)

    def watch(self, plugin_dir: Path, app: Any) -> Any | None:
        """Optionally watch a plugin directory; missing watchdog changes nothing."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return None
        runtime = self

        class ReloadHandler(FileSystemEventHandler):
            def on_modified(self, event: Any) -> None:
                if not event.is_directory and Path(event.src_path).name == "plugin.json":
                    def reload() -> None:
                        try:
                            runtime.hot_reload(plugin_dir, app)
                        except PluginError:
                            return

                    call_from_thread = getattr(app, "call_from_thread", None)
                    if callable(call_from_thread):
                        call_from_thread(reload)
                    else:
                        reload()

        self.stop_watch(plugin_dir.name)
        observer = Observer()
        observer.schedule(ReloadHandler(), str(plugin_dir), recursive=False)
        observer.start()
        self._watchers[plugin_dir.name] = observer
        return observer

    def stop_watch(self, plugin_name: str) -> None:
        """Stop one optional observer without making watchdog a dependency."""
        observer = self._watchers.pop(plugin_name, None)
        if observer is not None:
            observer.stop()

    def shutdown(self) -> None:
        """Stop optional observers during TUI shutdown."""
        for name in tuple(self._watchers):
            self.stop_watch(name)
