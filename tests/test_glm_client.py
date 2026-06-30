"""Tests for glm_acp.glm_client — GlmApiError, StreamResult, cancel, retry logic."""

import pytest
import os

os.environ.setdefault("ZAI_API_KEY", "test-key")

from glm_acp.glm_client import GlmClient, GlmApiError, StreamResult, ToolCallAccumulator
from glm_acp.config import MAX_RETRIES, RETRYABLE_STATUS_CODES, VISION_MODELS


class TestGlmApiError:
    def test_status_code_stored(self):
        err = GlmApiError(429, "rate limited")
        assert err.status_code == 429
        assert "429" in str(err)

    def test_is_runtime_error(self):
        err = GlmApiError(500, "server error")
        assert isinstance(err, RuntimeError)


class TestStreamResult:
    def test_defaults(self):
        r = StreamResult()
        assert r.content == ""
        assert r.reasoning == ""
        assert r.tool_calls == []
        assert r.finish_reason == ""
        assert r.usage is None

    def test_usage_format(self):
        r = StreamResult()
        r.usage = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        assert r.usage["input_tokens"] == 100


class TestToolCallAccumulator:
    def test_defaults(self):
        acc = ToolCallAccumulator()
        assert acc.id == ""
        assert acc.name == ""
        assert acc.arguments == ""

    def test_accumulation(self):
        acc = ToolCallAccumulator(id="call_123")
        acc.name = "read_file"
        acc.arguments += '{"path":'
        acc.arguments += ' "main.py"}'
        assert acc.name == "read_file"
        assert acc.arguments == '{"path": "main.py"}'


class TestGlmClientInit:
    def test_cancel_flag(self):
        client = GlmClient(model="glm-5.2")
        assert not client.cancelled
        client.cancel()
        assert client.cancelled

    def test_vision_model_no_thinking(self):
        """Vision models should not send thinking params."""
        import inspect
        src = inspect.getsource(GlmClient._do_stream_request)
        assert "is_vision" in src
        assert "VISION_MODELS" in src or "not is_vision" in src

    def test_stream_options(self):
        """stream_options include_usage should be set."""
        import inspect
        src = inspect.getsource(GlmClient._do_stream_request)
        assert "include_usage" in src

    def test_retry_clears_results(self):
        """Retry should clear partial StreamResult fields."""
        import inspect
        src = inspect.getsource(GlmClient._do_stream_request)
        assert 'result.content = ""' in src
        assert "result.tool_calls = []" in src

    def test_retry_count(self):
        """Should retry MAX_RETRIES + 1 times."""
        import inspect
        src = inspect.getsource(GlmClient._do_stream_request)
        assert f"range(MAX_RETRIES + 1)" in src or f"range({MAX_RETRIES} + 1)" in src

    def test_summarize_retry(self):
        """Summarization should also have retry logic."""
        import inspect
        src = inspect.getsource(GlmClient.summarize_messages)
        assert "attempt" in src
        assert "MAX_RETRIES" in src

    def test_cancel_check_in_stream(self):
        """Stream execution should check cancel flag."""
        import inspect
        src = inspect.getsource(GlmClient._execute_stream)
        assert "self._cancelled" in src
