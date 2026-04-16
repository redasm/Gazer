"""Tests for runtime.config_manager -- ConfigManager."""

import os
import yaml
import pytest
from runtime.config_manager import (
    ConfigManager,
    DEFAULT_CONFIG,
    is_internal_admin_config_path,
    is_sensitive_config_path,
)


class TestConfigManager:
    def test_default_config_omits_internal_planning_policy(self):
        defaults = DEFAULT_CONFIG.get("agents", {}).get("defaults", {})

        assert isinstance(defaults, dict)
        assert "planning" not in defaults

    def test_creates_default_config(self, tmp_config_file):
        """Should create default config file if it doesn't exist."""
        cm = ConfigManager(config_path=tmp_config_file)
        assert os.path.exists(tmp_config_file)
        assert cm.get("personality.name") == "Gazer"

    def test_get_nested(self, tmp_config_file):
        cm = ConfigManager(config_path=tmp_config_file)
        assert cm.get("voice.provider") == "edge-tts"
        assert cm.get("voice.cloud.provider_ref") == ""
        assert cm.get("voice.cloud.response_format") == "pcm"
        assert cm.get("voice.cloud.retry_count") == 1
        assert cm.get("voice.cloud.fallback_to_edge") is True
        assert cm.get("voice.cloud.strict_required") is False
        assert cm.get("perception.camera_device_index") == 0
        assert cm.get("perception.spatial.cloud.strict_required") is False
        assert cm.get("asr.input_device") is None
        assert cm.get("asr.cloud.strict_required") is False
        assert cm.get("feishu.simulated_typing.enabled") is False
        assert cm.get("feishu.simulated_typing.text") == "正在思考中..."
        assert cm.get("feishu.simulated_typing.min_interval_seconds") == 8
        assert cm.get("feishu.simulated_typing.auto_recall_on_reply") is True
        assert cm.get("feishu.media_analysis.enabled") is True
        assert cm.get("feishu.media_analysis.transcribe_audio") is True
        assert cm.get("coding.exec_backend") == "local"
        assert cm.get("coding.max_output_chars") == 100000
        assert cm.get("coding.max_parallel_tool_calls") == 4
        assert cm.get("coding.allow_local_fallback") is False
        assert cm.get("coding.ssh.enabled") is False
        assert cm.get("coding.ssh.remote_workspace") == "."
        assert cm.get("runtime.backend") == "python"
        assert cm.get("runtime.rust_sidecar.endpoint") == ""
        assert cm.get("runtime.rust_sidecar.timeout_ms") == 3000
        assert cm.get("runtime.rust_sidecar.auto_fallback_on_error") is True
        assert cm.get("runtime.rust_sidecar.error_fallback_threshold") == 3
        assert cm.get("runtime.rust_sidecar.rollout.enabled") is False
        assert cm.get("runtime.rust_sidecar.rollout.owner_only") is False
        assert cm.get("runtime.rust_sidecar.rollout.channels") == []
        assert cm.get("devices.local.backend") == "python"
        assert cm.get("devices.body_node.node_id") == "body-main"
        assert cm.get("personality.evolution.auto_optimize.enabled") is False
        assert cm.get("personality.evolution.auto_optimize.min_feedback_total") == 6
        assert cm.get("personality.evolution.auto_optimize.min_actionable_feedback") == 3
        assert cm.get("personality.evolution.auto_optimize.cooldown_seconds") == 1800
        assert cm.get("personality.evolution.publish_gate.enabled") is True
        assert cm.get("personality.evolution.publish_gate.min_similarity") == 0.45
        assert cm.get("personality.evolution.publish_gate.respect_release_gate") is True
        assert cm.get("personality.evolution.pre_publish_eval.enabled") is True
        assert cm.get("personality.evolution.pre_publish_eval.min_score") == 0.55
        assert cm.get("personality.evolution.history.max_records") == 300
        assert cm.get("personality.runtime.tool_policy_linkage.enabled") is True
        assert cm.get("personality.runtime.tool_policy_linkage.high_risk_levels") == ["critical"]
        assert cm.get("personality.runtime.tool_policy_linkage.sources") == ["persona_eval"]
        assert cm.get("personality.runtime.tool_policy_linkage.deny_names_by_level.warning") == [
            "exec",
            "node_invoke",
        ]
        assert cm.get("personality.runtime.tool_policy_linkage.deny_providers_by_level.critical") == [
            "devices",
            "system",
            "runtime",
        ]
        assert cm.get("observability.cost_quality_slo_targets.min_success_rate") == 0.9
        assert cm.get("observability.cost_quality_slo_targets.max_p95_latency_ms") == 3000.0
        assert cm.get("observability.efficiency_baseline_targets.min_success_rate") == 0.9
        assert cm.get("observability.efficiency_baseline_targets.max_avg_tokens_per_run") == 6000.0
        assert cm.get("web.search.providers_order") == ["brave", "duckduckgo", "wikipedia", "bing_rss"]
        assert cm.get("web.search.providers_enabled.brave") is True
        assert cm.get("web.search.providers_enabled.duckduckgo") is True
        assert cm.get("web.search.scenario_routing.enabled") is True
        assert cm.get("web.search.scenario_routing.auto_detect") is True
        assert cm.get("web.search.scenario_routing.profiles.news")[0] == "brave"
        assert cm.get("memory.context_backend.enabled") is False
        assert cm.get("memory.context_backend.mode") == "openviking"
        assert cm.get("memory.context_backend.data_dir") == "data/openviking"
        assert cm.get("memory.context_backend.session_prefix") == "gazer"
        assert cm.get("memory.context_backend.default_user") == "owner"
        assert cm.get("memory.context_backend.commit_every_messages") == 8
        assert cm.get("agents.list") is None
        assert cm.get("agents.bindings") is None
        assert cm.get("agents.orchestrator") is None
        assert cm.get("agents.templates") is None
        assert cm.get("models.router.enabled") is True
        assert cm.get("models.router.rollout.enabled") is True
        assert cm.get("models.router.rollout.owner_only") is True
        assert cm.get("models.router.complexity_routing.enabled") is False
        assert cm.get("models.router.complexity_routing.simple_prefer_cost") is True
        assert cm.get("models.router.complexity_routing.complex_prefer_success_rate") is True
        assert cm.get("models.router.deployment_orchestrator.enabled") is False
        assert cm.get("models.router.deployment_orchestrator.mode") == "manual"
        assert cm.get("models.router.deployment_orchestrator.canary.weight") == 0.1
        assert cm.get("models.router.deployment_orchestrator.auto_failover.enabled") is True
        assert cm.get("models.prompt_cache.enabled") is False
        assert cm.get("models.prompt_cache.ttl_seconds") == 300
        assert cm.get("models.prompt_cache.max_items") == 512
        assert cm.get("models.prompt_cache.segment_policy") == "stable_prefix"
        assert cm.get("models.prompt_cache.scope_fields") == ["session_key", "channel", "sender_id"]
        assert cm.get("models.prompt_cache.sanitize_sensitive") is True
        assert cm.get("trainer.online_policy_loop.enabled") is True
        assert cm.get("trainer.online_policy_loop.require_review") is True
        assert cm.get("trainer.online_policy_loop.gate.require_release_gate_open") is True
        assert cm.get("trainer.online_policy_loop.gate.min_eval_pass_rate") == 0.55
        assert cm.get("trainer.online_policy_loop.gate.min_trajectory_success_rate") == 0.6
        assert cm.get("trainer.online_policy_loop.gate.max_terminal_error_rate") == 0.4
        assert cm.get("security.readonly_channel_ids") == {}
        assert cm.get("security.parallel_tool_lane_limits.io") == 2
        assert cm.get("security.parallel_tool_lane_limits.device") == 1
        assert cm.get("security.parallel_tool_lane_limits.network") == 2
        assert cm.get("security.tool_batching.enabled") is True
        assert cm.get("security.tool_batching.max_batch_size") == 4
        assert cm.get("security.tool_batching.dedupe_enabled") is False
        assert cm.get("security.tool_planner.enabled") is True
        assert cm.get("security.tool_planner.compact_results") is True
        assert cm.get("security.tool_planner.max_result_chars") == 2400
        assert cm.get("security.tool_planner.error_max_result_chars") == 4000
        assert cm.get("security.tool_planner.head_chars") == 900
        assert cm.get("security.tool_planner.tail_chars") == 700
        assert cm.get("security.threat_scan.enabled") is False
        assert cm.get("security.threat_scan.provider") == "virustotal"
        assert cm.get("security.threat_scan.fail_mode") == "open"
        assert cm.get("nonexistent.key", "fallback") == "fallback"

    def test_set_and_persist(self, tmp_config_file):
        cm = ConfigManager(config_path=tmp_config_file)
        cm.set("personality.name", "TestGazer")
        assert cm.get("personality.name") == "TestGazer"

        # Reload and verify persistence
        cm2 = ConfigManager(config_path=tmp_config_file)
        assert cm2.get("personality.name") == "TestGazer"

    def test_set_creates_nested_keys(self, tmp_config_file):
        cm = ConfigManager(config_path=tmp_config_file)
        cm.set("new.deeply.nested.key", 42)
        assert cm.get("new.deeply.nested.key") == 42

    def test_merge_defaults(self, tmp_config_file):
        """New default keys should be merged into existing config."""
        # Write a minimal config
        with open(tmp_config_file, "w") as f:
            yaml.safe_dump({"personality": {"name": "Custom"}}, f)

        cm = ConfigManager(config_path=tmp_config_file)
        # The existing key should be preserved
        assert cm.get("personality.name") == "Custom"
        # New default keys should be present
        assert cm.get("voice.provider") == "edge-tts"
        assert cm.get("voice.cloud.model") == "gpt-4o-mini-tts"
        assert cm.get("security.coding_benchmark_scheduler.enabled") is False
        assert cm.get("canvas.enabled") is True

    def test_merge_defaults_does_not_alias_defaults(self, tmp_config_file):
        """Missing dict keys should be deep-copied, not aliased to DEFAULT_CONFIG."""
        with open(tmp_config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump({"security": {"tool_groups": {"desktop": ["screen_observe"]}}}, f)

        cm = ConfigManager(config_path=tmp_config_file)
        cm.set("security.owner_channel_ids.feishu", "ou_test_owner")

        reloaded = ConfigManager(config_path=tmp_config_file)
        assert reloaded.get("security.owner_channel_ids.feishu") == "ou_test_owner"

        with open(tmp_config_file, "r", encoding="utf-8") as f:
            persisted = yaml.safe_load(f) or {}
        assert persisted["security"]["owner_channel_ids"]["feishu"] == "ou_test_owner"
        assert DEFAULT_CONFIG["security"]["owner_channel_ids"] == {}

    def test_check_reload(self, tmp_config_file):
        cm = ConfigManager(config_path=tmp_config_file)
        original_name = cm.get("personality.name")

        # Modify the file externally
        cm.data["personality"]["name"] = "External"
        cm.save()

        cm2 = ConfigManager(config_path=tmp_config_file)
        cm2.check_reload()  # should detect change
        assert cm2.get("personality.name") == "External"

    def test_get_top_level(self, tmp_config_file):
        cm = ConfigManager(config_path=tmp_config_file)
        voice = cm.get("voice")
        assert isinstance(voice, dict)
        assert "provider" in voice

    def test_rejects_legacy_embedding_keys_in_strict_mode(self, tmp_config_file):
        with open(tmp_config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "models": {
                        "embedding_provider": "openai",
                        "embedding_model": "text-embedding-3-small",
                    }
                },
                f,
                allow_unicode=True,
                sort_keys=False,
            )

        with pytest.raises(RuntimeError, match="Deprecated config keys detected"):
            ConfigManager(config_path=tmp_config_file)

    def test_persist_order_models_embedding_before_router(self, tmp_config_file):
        cm = ConfigManager(config_path=tmp_config_file)
        cm.set("models.embedding.provider", "dashscope")
        cm.set("models.embedding.model", "text-embedding-v3")

        with open(tmp_config_file, "r", encoding="utf-8") as f:
            raw = f.read()
        models_idx = raw.find("models:")
        embedding_idx = raw.find("  embedding:", models_idx)
        router_idx = raw.find("  router:", models_idx)
        assert embedding_idx != -1
        assert router_idx != -1
        assert embedding_idx < router_idx

    def test_rejects_removed_api_auth_bypass_keys(self, tmp_config_file):
        with open(tmp_config_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "api": {
                        "allow_loopback_without_token": True,
                        "local_bypass_environments": ["dev"],
                    }
                },
                f,
                allow_unicode=True,
                sort_keys=False,
            )

        with pytest.raises(RuntimeError, match="Deprecated config keys detected"):
            ConfigManager(config_path=tmp_config_file)

    def test_to_safe_dict_masks_nested_sensitive_values(self, tmp_config_file):
        cm = ConfigManager(config_path=tmp_config_file)
        cm.set_many(
            {
                "voice.cloud.api_key": "voice-secret",
                "perception.spatial.cloud.api_key": "spatial-secret",
                "asr.cloud.api_key": "asr-secret",
                "web.search.brave_api_key": "brave-secret",
                "plugins.signature.trusted_keys.prod": "trusted-secret",
            }
        )
        safe = cm.to_safe_dict()

        assert safe["voice"]["cloud"]["api_key"] == "***"
        assert safe["perception"]["spatial"]["cloud"]["api_key"] == "***"
        assert safe["asr"]["cloud"]["api_key"] == "***"
        assert safe["web"]["search"]["brave_api_key"] == "***"
        assert safe["plugins"]["signature"]["trusted_keys"]["prod"] == "***"

    def test_is_sensitive_config_path_supports_recursive_wildcards(self):
        assert is_sensitive_config_path("voice.cloud.api_key") is True
        assert is_sensitive_config_path("web.search.brave_api_key") is True
        assert is_sensitive_config_path("plugins.signature.trusted_keys.prod") is True
        assert is_sensitive_config_path("api.max_upload_bytes") is False

    def test_persona_system_prompt_writes_to_soul_and_not_settings_yaml(self, tmp_path):
        config_path = tmp_path / "config" / "settings.yaml"
        cm = ConfigManager(config_path=str(config_path))

        cm.set("personality.system_prompt", "# Prompt\n\nUse SOUL as source.")

        soul_path = tmp_path / "assets" / "SOUL.md"
        assert soul_path.is_file()
        assert soul_path.read_text(encoding="utf-8") == "# Prompt\n\nUse SOUL as source.\n"
        assert cm.get("personality.system_prompt") == "# Prompt\n\nUse SOUL as source."

        persisted = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        personality = persisted.get("personality", {})
        assert isinstance(personality, dict)
        assert "system_prompt" not in personality

    def test_persona_system_prompt_migrates_from_settings_yaml_to_soul(self, tmp_path):
        config_path = tmp_path / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump(
                {
                    "personality": {
                        "name": "Gazer",
                        "system_prompt": "legacy prompt",
                    }
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        cm = ConfigManager(config_path=str(config_path))
        soul_path = tmp_path / "assets" / "SOUL.md"
        assert soul_path.is_file()
        assert soul_path.read_text(encoding="utf-8") == "legacy prompt\n"
        assert cm.get("personality.system_prompt") == "legacy prompt"

        cm.save()
        persisted = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        personality = persisted.get("personality", {})
        assert isinstance(personality, dict)
        assert "system_prompt" not in personality

    def test_to_safe_dict_includes_persona_system_prompt_from_soul(self, tmp_path):
        config_path = tmp_path / "config" / "settings.yaml"
        cm = ConfigManager(config_path=str(config_path))
        cm.set("personality.system_prompt", "safe prompt from soul")

        safe = cm.to_safe_dict()
        personality = safe.get("personality", {})
        assert isinstance(personality, dict)
        assert personality.get("system_prompt") == "safe prompt from soul"

    def test_to_safe_dict_omits_internal_planning_policy(self, tmp_config_file):
        cm = ConfigManager(config_path=tmp_config_file)
        cm.set("agents.defaults.planning.mode", "always")
        cm.set("agents.defaults.planning.auto.min_message_chars", 999)

        safe = cm.to_safe_dict()
        defaults = safe.get("agents", {}).get("defaults", {})

        assert isinstance(defaults, dict)
        assert "planning" not in defaults

    def test_is_internal_admin_config_path_matches_nested_planning_fields(self):
        assert is_internal_admin_config_path("agents.defaults.planning") is True
        assert is_internal_admin_config_path("agents.defaults.planning.auto.min_message_chars") is True
        assert is_internal_admin_config_path("agents.defaults.model.primary") is False
