---
name: acp-stdio-smoke-test
description: "Smoke-test the bare `glm-acp` ACP server over stdio. Both stdin AND stdout must be real pipes; redirecting either to a regular file makes asyncio raise ValueError. Use when verifying the bare launch path after agent.py/cli.py changes."
environments: ["python", "uv"]
requires_tools: ["run_command"]
tasks: ["acp", "smoke-test", "stdio", "verification"]
---

# Acp Stdio Smoke Test

The bare `glm-acp` launch (no subcommand) starts an ACP JSON-RPC server over stdio via `acp.run_agent` → `stdio_streams` → `loop.connect_read_pipe`/`connect_write_pipe`. asyncio REFUSES regular files: redirecting either side with `< file` or `> file` raises `ValueError: Pipe transport is for pipes/sockets only` (read) or `Pipe transport is only for pipes, sockets and character devices` (write). The traceback looks like an agent bug but is purely a test-harness artifact.

Correct pattern — chain BOTH stdin and stdout through pipes:
```
(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientInfo":{"name":"smoke","version":"0.0.1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}'; \
 sleep 5) | timeout 10 uv run glm-acp 2>/dev/null | head -3
```
Note: `clientInfo.version` is required by acp's pydantic schema — omitting it returns a clean JSON-RPC -32602 error (still proves the stack works).

A successful initialize returns `{"jsonrpc":"2.0","id":1,"result":{"agentInfo":{"name":"glm-acp","version":"..."},"agentCapabilities":{...},"authMethods":[{"id":"zai-api-key-setup",...}],"protocolVersion":1}}`.

Never use `> out.txt` or `< in.txt` for the bare launch — only pipes (`|`) or FIFOs work.

## Provenance

Learned by GLM ACP after successful task verification on 2026-07-25.
