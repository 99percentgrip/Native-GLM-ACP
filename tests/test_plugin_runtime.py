from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from glm_acp.plugin_runtime import PluginRuntime
from glm_acp.plugins import PluginError


class Registry:
    def __init__(self, accepted=True):
        self.accepted = accepted

    def verify(self, _name):
        if not self.accepted:
            raise PluginError("Plugin hash mismatch")
        return {"verified": True}


def manifest(tmp_path, **extra):
    root = tmp_path / "demo"
    root.mkdir()
    payload = {
        "version": "1",
        "commands": ["/demo Run demo"],
        "widgets": ["status"],
        "output_watchers": ["error"],
    } | extra
    (root / "plugin.json").write_text(json.dumps(payload))
    return root / "plugin.json"


def test_manifest_parsing(tmp_path):
    assert PluginRuntime(Registry()).load(manifest(tmp_path)).name == "demo"


def test_hash_mismatch_refused(tmp_path):
    with pytest.raises(PluginError):
        PluginRuntime(Registry(False)).load(manifest(tmp_path))


def test_command_registration(tmp_path):
    runtime = PluginRuntime(Registry())
    plugin = runtime.load(manifest(tmp_path))
    app = SimpleNamespace(_slash_commands={})
    runtime.register_commands(plugin, app)
    assert "/demo" in app._slash_commands
    assert runtime.owner_for("/demo") == "demo"


def test_unload_removes_commands(tmp_path):
    runtime = PluginRuntime(Registry())
    plugin = runtime.load(manifest(tmp_path))
    app = SimpleNamespace(_slash_commands={})
    runtime.register_commands(plugin, app)
    runtime.unload("demo", app)
    assert "/demo" not in app._slash_commands


def test_invalid_command_refused(tmp_path):
    runtime = PluginRuntime(Registry())
    plugin = runtime.load(manifest(tmp_path, commands=["bad"]))
    with pytest.raises(PluginError):
        runtime.register_commands(plugin, SimpleNamespace(_slash_commands={}))


def test_hot_reload_reregisters(tmp_path):
    runtime = PluginRuntime(Registry())
    app = SimpleNamespace(_slash_commands={})
    root = manifest(tmp_path).parent
    runtime.hot_reload(root, app)
    assert "/demo" in app._slash_commands


def test_watch_reloads_manifest_changes_when_watchdog_is_available(monkeypatch, tmp_path):
    class Handler:
        pass

    class Observer:
        def schedule(self, handler, _path, recursive=False):
            self.handler = handler
            self.recursive = recursive

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    events = SimpleNamespace(FileSystemEventHandler=Handler)
    observers = SimpleNamespace(Observer=Observer)
    monkeypatch.setitem(sys.modules, "watchdog", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "watchdog.events", events)
    monkeypatch.setitem(sys.modules, "watchdog.observers", observers)
    runtime = PluginRuntime(Registry())
    app = SimpleNamespace(_slash_commands={})
    root = manifest(tmp_path).parent

    observer = runtime.watch(root, app)
    assert observer is not None and observer.started is True
    observer.handler.on_modified(SimpleNamespace(is_directory=False, src_path=root / "plugin.json"))

    assert "/demo" in app._slash_commands
    runtime.shutdown()
    assert observer.stopped is True
