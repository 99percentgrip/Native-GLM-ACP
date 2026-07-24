"""Tests for glm_acp.config — model registry, plans, thought levels."""

import os
import sys

import pytest

from glm_acp.config import (
    API_ENDPOINTS,
    CONFIG_DIR_ENV,
    CONTEXT_WINDOW_TOKENS,
    DEFAULT_API_ENDPOINT,
    DEFAULT_MODEL,
    DESTRUCTIVE_TOOLS,
    GENERATION_PROFILES,
    MAX_RETRIES,
    MODELS,
    RETRYABLE_STATUS_CODES,
    VISION_MODELS,
    credentials_path,
    get_api_key,
    has_api_key,
    load_stored_api_key,
    models_for_plan,
    store_api_key,
    thought_levels_for_model,
)


class TestModelRegistry:
    def test_all_models_have_context_window(self):
        for model_id in MODELS:
            assert model_id in CONTEXT_WINDOW_TOKENS, (
                f"{model_id} missing from CONTEXT_WINDOW_TOKENS"
            )

    def test_all_models_have_plans(self):
        for model_id, info in MODELS.items():
            assert "plans" in info, f"{model_id} missing 'plans' key"
            assert len(info["plans"]) > 0, f"{model_id} has no plans"

    def test_vision_models_flagged(self):
        assert VISION_MODELS == frozenset({"glm-5v-turbo", "glm-4.5v", "glm-4.6v"})

    def test_default_model_exists(self):
        assert DEFAULT_MODEL in MODELS

    def test_context_window_sizes(self):
        assert CONTEXT_WINDOW_TOKENS["glm-5.2"] == 1_000_000
        assert CONTEXT_WINDOW_TOKENS["glm-5-turbo"] == 200_000
        assert CONTEXT_WINDOW_TOKENS["glm-4.7"] == 200_000
        assert CONTEXT_WINDOW_TOKENS["glm-5v-turbo"] == 200_000
        assert CONTEXT_WINDOW_TOKENS["glm-4.5v"] == 65_536


class TestPlanModelSync:
    def test_coding_plan_excludes_vision(self):
        models = models_for_plan("coding")
        assert len(models) == 3
        assert "glm-4.5v" not in models
        assert "glm-4.6v" not in models

    def test_standard_plan_includes_vision(self):
        models = models_for_plan("standard")
        assert len(models) == 6
        assert "glm-5v-turbo" in models
        assert "glm-4.5v" in models
        assert "glm-4.6v" in models

    def test_bigmodel_plan_includes_vision(self):
        models = models_for_plan("bigmodel")
        assert len(models) == 6

    def test_default_plan_is_coding(self):
        assert DEFAULT_API_ENDPOINT == "coding"


class TestThoughtLevels:
    def test_glm52_has_all_levels(self):
        levels = thought_levels_for_model("glm-5.2")
        assert len(levels) == 4
        assert set(levels.keys()) == {"disabled", "enabled", "high", "max"}

    def test_glm47_excludes_deep(self):
        levels = thought_levels_for_model("glm-4.7")
        assert len(levels) == 2
        assert set(levels.keys()) == {"disabled", "enabled"}

    def test_vision_model_supports_standard_thinking(self):
        levels = thought_levels_for_model("glm-4.5v")
        assert set(levels) == {"disabled", "enabled"}

    def test_vision_model_supports_standard_thinking_46v(self):
        levels = thought_levels_for_model("glm-4.6v")
        assert set(levels) == {"disabled", "enabled"}


class TestConstants:
    def test_destructive_tools(self):
        assert "write_file" in DESTRUCTIVE_TOOLS
        assert "edit_file" in DESTRUCTIVE_TOOLS
        assert "run_command" in DESTRUCTIVE_TOOLS
        assert "apply_patch" in DESTRUCTIVE_TOOLS
        assert "apply_patch_set" in DESTRUCTIVE_TOOLS
        assert "learn_skill" in DESTRUCTIVE_TOOLS
        assert "forget_skill" in DESTRUCTIVE_TOOLS
        assert "store_user_profile" in DESTRUCTIVE_TOOLS
        assert "forget_memory" in DESTRUCTIVE_TOOLS
        assert "manage_skill" in DESTRUCTIVE_TOOLS
        assert "curate_skills" in DESTRUCTIVE_TOOLS
        assert "manage_skill_bundle" in DESTRUCTIVE_TOOLS
        assert "evolve_skill" in DESTRUCTIVE_TOOLS
        assert "delegate_task" in DESTRUCTIVE_TOOLS
        assert "vision_analyze" in DESTRUCTIVE_TOOLS
        assert "browser_ui" in DESTRUCTIVE_TOOLS
        assert "read_file" not in DESTRUCTIVE_TOOLS

    def test_retry_config(self):
        assert MAX_RETRIES == 3
        assert 429 in RETRYABLE_STATUS_CODES
        assert 500 in RETRYABLE_STATUS_CODES
        assert 400 not in RETRYABLE_STATUS_CODES

    def test_api_endpoints_have_urls(self):
        for endpoint_id, info in API_ENDPOINTS.items():
            assert "base_url" in info
            assert info["base_url"].startswith("https://")

    def test_generation_profiles_adjust_one_sampling_control(self):
        assert GENERATION_PROFILES["balanced"]["temperature"] is None
        assert GENERATION_PROFILES["balanced"]["top_p"] is None


class TestMaxToolIterations:
    """Per-turn tool-call iteration cap — env-var override + bounds clamp."""

    def test_default_is_fifty(self, monkeypatch):
        monkeypatch.delenv("GLM_ACP_MAX_TOOL_ITERATIONS", raising=False)
        from glm_acp.config import max_tool_iterations

        assert max_tool_iterations() == 50

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("GLM_ACP_MAX_TOOL_ITERATIONS", "100")
        from glm_acp.config import max_tool_iterations

        assert max_tool_iterations() == 100

    def test_env_var_clamped_to_ceiling(self, monkeypatch):
        monkeypatch.setenv("GLM_ACP_MAX_TOOL_ITERATIONS", "5000")
        from glm_acp.config import MAX_TOOL_ITERATIONS_CEILING, max_tool_iterations

        assert max_tool_iterations() == MAX_TOOL_ITERATIONS_CEILING == 1000

    def test_env_var_clamped_to_minimum(self, monkeypatch):
        monkeypatch.setenv("GLM_ACP_MAX_TOOL_ITERATIONS", "0")
        from glm_acp.config import MIN_TOOL_ITERATIONS, max_tool_iterations

        assert max_tool_iterations() == MIN_TOOL_ITERATIONS == 1

    def test_env_var_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("GLM_ACP_MAX_TOOL_ITERATIONS", "not-a-number")
        from glm_acp.config import max_tool_iterations

        assert max_tool_iterations() == 50

    def test_negative_env_var_clamped_to_minimum(self, monkeypatch):
        monkeypatch.setenv("GLM_ACP_MAX_TOOL_ITERATIONS", "-7")
        from glm_acp.config import max_tool_iterations

        assert max_tool_iterations() == 1

    def test_session_default_uses_env_var(self, monkeypatch, tmp_path):
        """A new Session picks up the env var at creation time."""
        # Redirect config dir so we don't touch the real user config file.
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("GLM_ACP_MAX_TOOL_ITERATIONS", "200")
        from glm_acp.agent import Session

        session = Session("test-iter", cwd=".")
        assert session.max_tool_iterations == 200

    @pytest.mark.asyncio
    async def test_set_config_option_updates_session_cap(self, monkeypatch, tmp_path):
        """``/max-iterations <N>`` routes through set_config_option."""
        # Redirect config dir so the new file-persistence side effect doesn't
        # corrupt the user's real ~/.config/glm-acp/max-iterations.json.
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GLM_ACP_MAX_TOOL_ITERATIONS", raising=False)
        from glm_acp.agent import GlmAcpAgent, Session

        agent = GlmAcpAgent()
        session = Session("test-iter-cfg", cwd=".")
        agent._sessions["test-iter-cfg"] = session
        assert session.max_tool_iterations == 50

        # Raise to 200 — signature is (config_id, session_id, value).
        await agent.set_config_option("max_tool_iterations", "test-iter-cfg", "200")
        assert session.max_tool_iterations == 200

        # Out-of-range values get clamped, not rejected
        await agent.set_config_option("max_tool_iterations", "test-iter-cfg", "99999")
        assert session.max_tool_iterations == 1000

        await agent.set_config_option("max_tool_iterations", "test-iter-cfg", "-5")
        assert session.max_tool_iterations == 1

        # Garbage falls back to default 50
        await agent.set_config_option("max_tool_iterations", "test-iter-cfg", "garbage")
        assert session.max_tool_iterations == 50
        for profile in ("precise", "exploratory"):
            info = GENERATION_PROFILES[profile]
            assert sum(info[key] is not None for key in ("temperature", "top_p")) == 1

    # --- Persistent user default (Fix B) -------------------------------
    # These cover the file-backed default introduced so that ``/max-iterations``
    # survives closing ``glm-acp chat`` and starting a fresh session.

    def test_persisted_default_is_loaded_when_no_env_var(self, monkeypatch, tmp_path):
        """Save 200, then max_tool_iterations() returns 200 (no env var)."""
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GLM_ACP_MAX_TOOL_ITERATIONS", raising=False)
        from glm_acp.config import max_tool_iterations, save_max_tool_iterations

        assert max_tool_iterations() == 50
        save_max_tool_iterations(200)
        assert max_tool_iterations() == 200

    def test_env_var_wins_over_persisted_default(self, monkeypatch, tmp_path):
        """Env var precedence: file says 200, env says 75 → returns 75."""
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("GLM_ACP_MAX_TOOL_ITERATIONS", "75")
        from glm_acp.config import max_tool_iterations, save_max_tool_iterations

        save_max_tool_iterations(200)
        assert max_tool_iterations() == 75

    def test_save_clamps_value_before_writing(self, monkeypatch, tmp_path):
        """Out-of-range saves are clamped to [1, 1000] inside the file."""
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        import json

        from glm_acp.config import (
            MAX_TOOL_ITERATIONS_CEILING,
            MIN_TOOL_ITERATIONS,
            max_tool_iterations_path,
            save_max_tool_iterations,
        )

        assert save_max_tool_iterations(5000) == MAX_TOOL_ITERATIONS_CEILING
        assert save_max_tool_iterations(0) == MIN_TOOL_ITERATIONS

        save_max_tool_iterations(5000)
        payload = json.loads(max_tool_iterations_path().read_text())
        assert payload["value"] == 1000

    def test_malformed_file_falls_back_to_constant(self, monkeypatch, tmp_path):
        """Garbage JSON in the file → default 50, never raise."""
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GLM_ACP_MAX_TOOL_ITERATIONS", raising=False)
        from glm_acp.config import max_tool_iterations, max_tool_iterations_path

        max_tool_iterations_path().write_text("not json at all {")
        assert max_tool_iterations() == 50

    def test_out_of_range_file_value_falls_back_to_constant(
        self, monkeypatch, tmp_path
    ):
        """A persisted value outside [1, 1000] is rejected, not clamped-on-read."""
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GLM_ACP_MAX_TOOL_ITERATIONS", raising=False)
        import json

        from glm_acp.config import max_tool_iterations, max_tool_iterations_path

        max_tool_iterations_path().write_text(json.dumps({"schema": 1, "value": 99999}))
        assert max_tool_iterations() == 50

    def test_wrong_schema_file_falls_back_to_constant(self, monkeypatch, tmp_path):
        """A file missing the ``value`` field falls back to the constant."""
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GLM_ACP_MAX_TOOL_ITERATIONS", raising=False)
        import json

        from glm_acp.config import max_tool_iterations, max_tool_iterations_path

        max_tool_iterations_path().write_text(json.dumps({"schema": 1}))
        assert max_tool_iterations() == 50

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX file modes are not honored on Windows NTFS",
    )
    def test_saved_file_is_user_read_write_only(self, monkeypatch, tmp_path):
        """The persisted file is created with 0600 perms (credential-safety parity).

        Skipped on Windows because chmod() is a no-op there; the code still
        calls it (matching cron.py and checkpoints.py) for Unix parity.
        """
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))

        from glm_acp.config import max_tool_iterations_path, save_max_tool_iterations

        save_max_tool_iterations(100)
        mode = max_tool_iterations_path().stat().st_mode & 0o777
        assert mode == 0o600

    @pytest.mark.asyncio
    async def test_set_config_option_persists_to_file(self, monkeypatch, tmp_path):
        """``/max-iterations 200`` writes the file so the next launch reads it."""
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GLM_ACP_MAX_TOOL_ITERATIONS", raising=False)
        import json

        from glm_acp.agent import GlmAcpAgent, Session
        from glm_acp.config import max_tool_iterations_path

        agent = GlmAcpAgent()
        session = Session("test-persist", cwd=".")
        agent._sessions["test-persist"] = session

        await agent.set_config_option("max_tool_iterations", "test-persist", "200")

        path = max_tool_iterations_path()
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload == {"schema": 1, "value": 200}

    def test_new_session_reads_persisted_default(self, monkeypatch, tmp_path):
        """A brand-new Session (no env var) picks up the saved file value."""
        monkeypatch.setenv("GLM_ACP_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GLM_ACP_MAX_TOOL_ITERATIONS", raising=False)
        from glm_acp.agent import Session
        from glm_acp.config import save_max_tool_iterations

        save_max_tool_iterations(123)
        fresh_session = Session("test-fresh", cwd=".")
        assert fresh_session.max_tool_iterations == 123


class TestApiKey:
    def test_missing_key_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        monkeypatch.delenv("Z_AI_API_KEY", raising=False)
        monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
        with pytest.raises(RuntimeError, match="ZAI_API_KEY"):
            get_api_key()

    def test_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ZAI_API_KEY", "test-key-123")
        assert get_api_key() == "test-key-123"

    def test_key_from_alt_env(self, monkeypatch):
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        monkeypatch.setenv("Z_AI_API_KEY", "alt-key-456")
        assert get_api_key() == "alt-key-456"

    def test_stored_key_round_trip(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        monkeypatch.delenv("Z_AI_API_KEY", raising=False)
        monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))

        path = store_api_key("  stored-secret  ")

        assert path == credentials_path()
        assert load_stored_api_key() == "stored-secret"
        assert get_api_key() == "stored-secret"
        assert has_api_key() is True
        if os.name != "nt":
            assert path.stat().st_mode & 0o077 == 0

    def test_environment_key_precedes_stored_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
        store_api_key("stored-secret")
        monkeypatch.setenv("ZAI_API_KEY", "environment-secret")
        assert get_api_key() == "environment-secret"

    def test_invalid_stored_state_is_ignored(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        monkeypatch.delenv("Z_AI_API_KEY", raising=False)
        monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
        credentials_path().write_text("not-json", encoding="utf-8")
        assert load_stored_api_key() is None
        assert has_api_key() is False

    def test_empty_key_is_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
        with pytest.raises(ValueError, match="cannot be empty"):
            store_api_key("   ")
