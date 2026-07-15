"""Fail-safe post-write syntax checks and optional LSP diagnostics."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib

logger = logging.getLogger("glm_acp")

_SERVERS: dict[str, tuple[list[str], str]] = {
    ".py": (["pyright-langserver", "--stdio"], "python"),
    ".js": (["typescript-language-server", "--stdio"], "javascript"),
    ".jsx": (["typescript-language-server", "--stdio"], "javascriptreact"),
    ".ts": (["typescript-language-server", "--stdio"], "typescript"),
    ".tsx": (["typescript-language-server", "--stdio"], "typescriptreact"),
    ".go": (["gopls"], "go"),
    ".rs": (["rust-analyzer"], "rust"),
}


def syntax_diagnostics(path: Path, text: str) -> list[dict[str, Any]]:
    """Return deterministic syntax errors for formats supported by the stdlib."""
    try:
        if path.suffix == ".py":
            ast.parse(text, filename=str(path))
        elif path.suffix == ".json":
            json.loads(text)
        elif path.suffix == ".toml" or path.name == "pyproject.toml":
            tomllib.loads(text)
    except (SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        return [
            {
                "source": "syntax",
                "severity": "error",
                "line": max(1, int(getattr(error, "lineno", 1) or 1)),
                "column": max(1, int(getattr(error, "offset", 1) or 1)),
                "message": str(error),
            }
        ]
    return []


@dataclass
class _LspProcess:
    command: list[str]
    root: Path
    process: asyncio.subprocess.Process | None = None
    reader_task: asyncio.Task[None] | None = None
    next_id: int = 1
    pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict)
    diagnostics: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)
    events: dict[str, asyncio.Event] = field(default_factory=dict)

    async def start(self) -> None:
        if self.process is not None:
            return
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.root,
        )
        self.reader_task = asyncio.create_task(self._read_loop())
        await self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": self.root.as_uri(),
                "capabilities": {
                    "textDocument": {"publishDiagnostics": {"relatedInformation": True}}
                },
                "clientInfo": {"name": "native-glm-acp"},
            },
        )
        await self.notify("initialized", {})

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP server is not running")
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.process.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self.next_id
        self.next_id += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return await asyncio.wait_for(future, timeout=8)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _read_message(self) -> dict[str, Any] | None:
        if self.process is None or self.process.stdout is None:
            return None
        length = 0
        while True:
            line = await self.process.stdout.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            name, _, value = line.decode(errors="replace").partition(":")
            if name.lower() == "content-length":
                length = int(value.strip())
        if length <= 0:
            return None
        raw = await self.process.stdout.readexactly(length)
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    async def _read_loop(self) -> None:
        try:
            while message := await self._read_message():
                request_id = message.get("id")
                if request_id in self.pending and ("result" in message or "error" in message):
                    future = self.pending.pop(request_id)
                    if not future.done():
                        future.set_result(message.get("result"))
                    continue
                method = message.get("method")
                if method == "textDocument/publishDiagnostics":
                    params = message.get("params") or {}
                    uri = str(params.get("uri", ""))
                    self.diagnostics[uri] = list(params.get("diagnostics") or [])
                    self.events.setdefault(uri, asyncio.Event()).set()
                elif request_id is not None:
                    result: Any = [] if method == "workspace/configuration" else None
                    await self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except (asyncio.CancelledError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.debug("LSP reader stopped", exc_info=True)

    async def diagnose(self, path: Path, text: str, language_id: str) -> list[dict[str, Any]]:
        await self.start()
        uri = path.resolve().as_uri()
        version = self.versions.get(uri, 0) + 1
        self.versions[uri] = version
        event = self.events.setdefault(uri, asyncio.Event())
        event.clear()
        if version == 1:
            await self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": version,
                        "text": text,
                    }
                },
            )
        else:
            await self.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                },
            )
        try:
            await asyncio.wait_for(event.wait(), timeout=3)
        except asyncio.TimeoutError:
            return []
        return self.diagnostics.get(uri, [])

    async def close(self) -> None:
        try:
            if self.process is not None and self.process.returncode is None:
                await self.request("shutdown", {})
                await self.notify("exit", {})
                await asyncio.wait_for(self.process.wait(), timeout=2)
        except Exception:
            if self.process is not None and self.process.returncode is None:
                self.process.kill()
        if self.reader_task is not None:
            self.reader_task.cancel()


class DiagnosticsManager:
    """Lazy per-language LSP pool; every failure falls back to syntax checks."""

    def __init__(self) -> None:
        self._servers: dict[tuple[str, str], _LspProcess] = {}

    async def check(self, path: str, text: str, root: str) -> dict[str, Any]:
        file_path = Path(path)
        syntax = syntax_diagnostics(file_path, text)
        result: dict[str, Any] = {"syntax": syntax, "lsp": [], "lsp_status": "unsupported"}
        spec = _SERVERS.get(file_path.suffix.lower())
        if syntax or spec is None:
            return result
        command, language_id = spec
        if shutil.which(command[0]) is None:
            result["lsp_status"] = "unavailable"
            return result
        key = (str(Path(root).resolve()), language_id)
        server = self._servers.setdefault(key, _LspProcess(command, Path(root).resolve()))
        try:
            result["lsp"] = await server.diagnose(file_path, text, language_id)
            result["lsp_status"] = "ok"
        except Exception:
            result["lsp_status"] = "failed"
            logger.debug("LSP diagnostics failed safely", exc_info=True)
        return result

    async def close(self) -> None:
        await asyncio.gather(
            *(server.close() for server in self._servers.values()), return_exceptions=True
        )
        self._servers.clear()
