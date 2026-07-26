import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from glm_acp.agent import GlmAcpAgent, Session
from glm_acp.glm_client import StreamResult
from glm_acp.jit_tools import (
    SEARCH_TOOLS_DEFINITION,
    SEARCH_TOOLS_NAME,
    DeferredToolRegistry,
    ToolSearchError,
)
from glm_acp.mcp import MCP_TOOL_DEFINITIONS
from glm_acp.observability import observability_snapshot, render_observability
from glm_acp.telemetry import TrajectoryRecorder
from glm_acp.tools import TOOL_DEFINITIONS


def _tool_call(call_id: str, name: str, arguments: dict) -> StreamResult:
    return StreamResult(
        tool_calls=[
            {
                "id": call_id,
                "function": {"name": name, "arguments": arguments},
            }
        ],
        finish_reason="tool_calls",
    )


class _ScriptedClient:
    def __init__(self, results):
        self.results = iter(results)
        self.requests = []
        self.cancelled = False

    def begin_turn(self):
        self.cancelled = False

    async def stream_completion(self, **kwargs):
        self.requests.append(kwargs)
        return next(self.results)


def _configured_agent(client: _ScriptedClient) -> GlmAcpAgent:
    agent = GlmAcpAgent()
    # Keep tests independent of the developer's user-level MCP configuration.
    agent._mcp.servers = {}
    agent._telemetry = MagicMock()
    connection = MagicMock()
    connection.session_update = AsyncMock()
    connection.request_permission = AsyncMock()
    agent._conn = connection
    agent._client_for_session = lambda _session: client
    return agent


def test_registry_keyword_search_is_bounded_relevant_and_fast():
    registry = DeferredToolRegistry(TOOL_DEFINITIONS)

    started = time.perf_counter()
    matches = registry.search("read Python file contents", limit=50)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert matches
    assert matches[0].name == "read_file"
    assert len(matches) <= 5
    assert elapsed_ms < 50


def test_bm25_and_regex_search_argument_names_and_descriptions():
    registry = DeferredToolRegistry(
        [
            {
                "type": "function",
                "function": {
                    "name": "execute_operation",
                    "description": "Perform a remote operation.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "destination_city": {
                                "type": "string",
                                "description": "Location for current weather forecast data.",
                            }
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "archive_record",
                    "description": "Archive one database record.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
    )

    assert registry.search("weather forecast location")[0].name == "execute_operation"
    assert registry.search(
        r"destination_city|forecast", mode="regex"
    )[0].name == "execute_operation"


def test_search_modes_enforce_anthropic_compatible_limits_and_safe_regex():
    registry = DeferredToolRegistry(TOOL_DEFINITIONS)

    with pytest.raises(ToolSearchError, match="500"):
        registry.search("x" * 501)
    with pytest.raises(ToolSearchError, match="200"):
        registry.search("x" * 201, mode="regex")
    with pytest.raises(ToolSearchError, match="Invalid regex"):
        registry.search("(", mode="regex")
    with pytest.raises(ToolSearchError, match="unsafe"):
        registry.search("(a+)+", mode="regex")


def test_gateway_reduces_initial_schema_payload_by_more_than_85_percent():
    full_catalog = json.dumps([*TOOL_DEFINITIONS, *MCP_TOOL_DEFINITIONS])
    gateway = json.dumps([SEARCH_TOOLS_DEFINITION])

    assert len(gateway) < len(full_catalog) * 0.15


def test_registry_preserves_mcp_schema_route_and_qualifies_collision():
    registry = DeferredToolRegistry(
        [
            {
                "type": "function",
                "function": {
                    "name": "get_cpu_usage",
                    "description": "Native CPU reader",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    )

    records = registry.register_mcp_tools(
        "metrics server",
        [
            {
                "name": "get_cpu_usage",
                "title": "CPU",
                "description": "Read current server CPU metrics.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"host": {"type": "string"}},
                    "required": ["host"],
                },
                "outputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
                "_meta": {"provider": "test"},
            }
        ],
    )

    assert len(records) == 1
    record = records[0]
    assert record.name == "mcp__metrics_server__get_cpu_usage"
    assert record.server == "metrics server"
    assert record.remote_name == "get_cpu_usage"
    assert record.read_only is True
    assert record.defer_loading is True
    assert record.schema["function"]["parameters"]["required"] == ["host"]
    assert record.mcp_schema["outputSchema"] == {"type": "object"}
    assert record.mcp_schema["_meta"] == {"provider": "test"}


@pytest.mark.asyncio
async def test_gateway_is_initially_alone_then_appends_loaded_schema(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("hello", encoding="utf-8")
    client = _ScriptedClient(
        [
            _tool_call("search-1", SEARCH_TOOLS_NAME, {"intent": "read file contents"}),
            _tool_call("read-1", "read_file", {"path": "sample.txt"}),
            StreamResult(content="done", finish_reason="stop"),
        ]
    )
    agent = _configured_agent(client)
    session = Session("jit-native", str(tmp_path))
    session.permission_mode = "bypass"

    assert await agent._run_turn(session) == "end_turn"

    first_names = [tool["function"]["name"] for tool in client.requests[0]["tools"]]
    second_names = [tool["function"]["name"] for tool in client.requests[1]["tools"]]
    assert first_names == [SEARCH_TOOLS_NAME]
    assert second_names[0] == SEARCH_TOOLS_NAME
    assert "read_file" in second_names[1:]
    assert session.loaded_tool_names
    gateway_results = [
        message["content"]
        for message in session.messages
        if message.get("tool_call_id") == "search-1"
    ]
    assert len(gateway_results) == 1
    assert gateway_results[0].startswith("Tools loaded successfully:")
    jit_events = [
        call
        for call in agent._telemetry.record.call_args_list
        if call.args and call.args[0] == "jit_tool_search"
    ]
    assert len(jit_events) == 1
    assert jit_events[0].kwargs["mode"] == "bm25"
    assert jit_events[0].kwargs["success"] is True
    assert "read file contents" not in str(jit_events[0])
    await agent.aclose()


@pytest.mark.asyncio
async def test_discovered_mcp_tool_loads_and_executes_by_original_route(tmp_path):
    client = _ScriptedClient(
        [
            _tool_call("search-1", SEARCH_TOOLS_NAME, {"intent": "server CPU metrics"}),
            _tool_call("cpu-1", "get_cpu_usage", {"host": "api-1"}),
            StreamResult(content="CPU is healthy", finish_reason="stop"),
        ]
    )
    agent = _configured_agent(client)
    agent._tool_registry.register_mcp_tools(
        "metrics",
        [
            {
                "name": "get_cpu_usage",
                "description": "Read server CPU metrics.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"host": {"type": "string"}},
                    "required": ["host"],
                },
                "annotations": {"readOnlyHint": True},
            }
        ],
    )
    agent._mcp.call = AsyncMock(return_value={"cpu": 12})
    session = Session("jit-mcp", str(tmp_path))
    session.permission_mode = "bypass"

    assert await agent._run_turn(session) == "end_turn"

    second_names = [tool["function"]["name"] for tool in client.requests[1]["tools"]]
    assert second_names[:2] == [SEARCH_TOOLS_NAME, "get_cpu_usage"]
    agent._mcp.call.assert_awaited_once_with("metrics", "get_cpu_usage", {"host": "api-1"})
    await agent.aclose()


def test_observability_surfaces_secret_safe_jit_metrics(tmp_path):
    path = tmp_path / "trajectory.jsonl"
    recorder = TrajectoryRecorder(path)
    recorder.record(
        "jit_tool_search",
        "session",
        mode="regex",
        success=True,
        duration_ms=4,
        matches=3,
        newly_loaded=2,
        loaded_total=2,
        registry_tools=80,
    )

    snapshot = observability_snapshot(path)

    assert snapshot["jit_tool_loading"] == {
        "searches": 1,
        "failures": 0,
        "matches": 3,
        "newly_loaded": 2,
        "max_loaded": 2,
        "latency_ms_p50": 4,
        "latency_ms_p95": 4,
        "by_mode": {"regex": 1},
    }
    assert "JIT tools: active" in render_observability(snapshot)
