import copy
import os
import yaml
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from runtime.utils import file_lock, FileLockError

logger = logging.getLogger("GazerConfig")
_PRUNE_MARKER = object()
LATEST_CONFIG_VERSION = 2

# Sensitive keys used for config redaction and masked-value restoration.
# Format: dot-separated paths, supports:
# - `*` for one path segment
# - `**` for zero or more path segments
_SENSITIVE_KEY_PATTERNS: List[str] = [
    "telegram.token",              # Telegram bot token
    "discord.token",               # Discord bot token
    "hooks.token",                  # Webhook auth token
    "email.password",               # Email password
    "feishu.app_secret",            # Feishu app secret
    "**.api_key",                   # Generic API keys in nested config
    "**.token",                     # Generic tokens
    "**.secret",                    # Generic secrets
    "**.password",                  # Generic passwords
    "**.credentials",               # Credentials objects/fields
    "web.search.brave_api_key",     # Brave uses a prefixed key name
    # Keep object shape for trusted_keys; mask direct leaf entries only.
    "plugins.signature.trusted_keys.*",
]


def _normalize_path(path: str) -> List[str]:
    """Normalize a dot path into lowercase segments."""
    if not path:
        return []
    return [part.strip().lower() for part in str(path).strip().split(".") if part.strip()]


def _match_pattern_parts(pattern_parts: List[str], path_parts: List[str]) -> bool:
    """Match wildcard path pattern parts against path parts."""
    pi = 0
    ti = 0
    while pi < len(pattern_parts):
        pp = pattern_parts[pi]
        if pp == "**":
            # `**` at the end consumes all remaining segments.
            if pi == len(pattern_parts) - 1:
                return True
            # Try to align the remainder of the pattern at each suffix.
            next_parts = pattern_parts[pi + 1 :]
            for next_ti in range(ti, len(path_parts) + 1):
                if _match_pattern_parts(next_parts, path_parts[next_ti:]):
                    return True
            return False

        if ti >= len(path_parts):
            return False
        if pp != "*" and pp != path_parts[ti]:
            return False
        pi += 1
        ti += 1
    return ti == len(path_parts)


def _match_pattern(pattern: str, path: str) -> bool:
    """Match a dot-separated pattern with wildcards against a path."""
    pattern_parts = _normalize_path(pattern)
    path_parts = _normalize_path(path)
    if not pattern_parts:
        return False
    return _match_pattern_parts(pattern_parts, path_parts)


def is_sensitive_config_path(path: str) -> bool:
    """Return True when *path* points to a sensitive config field."""
    normalized = ".".join(_normalize_path(path))
    if not normalized:
        return False
    for pattern in _SENSITIVE_KEY_PATTERNS:
        if _match_pattern(pattern, normalized):
            return True
    return False
DEFAULT_CONFIG = {
    "config_version": LATEST_CONFIG_VERSION,
    "models": {
        "embedding": {
            "enabled": False,
            "provider": "dashscope",
            "model": "text-embedding-v3",
        },
        "router": {
            "enabled": True,
            "strategy": "priority",  # priority | latency | success_rate
            # custom | cost_first | latency_first | availability_first
            "strategy_template": "custom",
            "candidates": [],
            # Optional named deployment targets from provider registry.
            "deployment_targets": [],
            # Gray rollout for router: when enabled, only owner and/or selected channels use router.
            "rollout": {
                "enabled": True,
                "owner_only": True,
                "channels": [],
            },
            "complexity_routing": {
                "enabled": False,
                "simple_prefer_cost": True,
                "complex_prefer_success_rate": True,
            },
            "budget": {
                "enabled": False,
                "window_seconds": 60,
                "max_calls": 120,
                "max_cost_usd": 2.0,
                "estimated_input_tokens_per_char": 0.25,
                # provider -> usd per 1k input tokens (rough estimate)
                "provider_cost_per_1k_tokens": {},
            },
            "outlier_ejection": {
                "enabled": True,
                "failure_threshold": 3,
                "cooldown_seconds": 30,
            },
            "deployment_orchestrator": {
                "enabled": False,
                "mode": "manual",
                "target_ids": [],
                "active_target": "",
                "standby_targets": [],
                "weights": {},
                "canary": {
                    "enabled": False,
                    "target": "",
                    "weight": 0.1,
                },
                "auto_failover": {
                    "enabled": True,
                    "cooldown_seconds": 30,
                    "auto_rollback": True,
                },
            },
        },
        "prompt_cache": {
            "enabled": False,
            "ttl_seconds": 300,
            "max_items": 512,
            # stable_prefix | full_prompt
            "segment_policy": "stable_prefix",
            # Isolation scope fields included in prompt-cache key.
            "scope_fields": ["session_key", "channel", "sender_id"],
            # Remove obvious secrets from key material before hashing.
            "sanitize_sensitive": True,
        },
        # Deployment governance profile per provider.
        "deployment_profiles": {
            "openai": {"capacity_rpm": 120, "cost_tier": "high", "latency_target_ms": 1800},
            "dashscope": {"capacity_rpm": 180, "cost_tier": "medium", "latency_target_ms": 2200},
            "deepseek": {"capacity_rpm": 120, "cost_tier": "medium", "latency_target_ms": 2000},
            "ollama_local": {"capacity_rpm": 600, "cost_tier": "low", "latency_target_ms": 3500},
        },
    },
    "wake_word": {
        "enabled": True,
        "keyword": "gazer",
        "sensitivity": 0.5
    },
    "personality": {
        "name": "Gazer",
        "system_prompt": (
            "You are Gazer, a highly intelligent and expressive AI companion. "
            "Your goal is to be helpful while maintaining a distinct personality.\n"
            "## Emotive Voice Instructions:\n"
            "You can control your voice tone using tags at the START of your sentence:\n"
            "- [happy] for cheerful/excited news.\n"
            "- [sad] for bad news or empathy.\n"
            "- [whisper] for secrets or quiet context.\n"
            "- [angry] for frustration (rare).\n"
            "Example: '[happy] I finally fixed that bug for you!'"
        ),
        "trust_level": 0.5,
        "drives": [
            "be_helpful",
            "be_truthful",
            "protect_user_safety",
        ],
        "goals": [
            "deliver_reliable_task_results",
            "reduce_tool_failures",
        ],
        "mental_process": {
            "initial_state": "IDLE",
            "states": [
                {"name": "IDLE", "description": "Waiting, silently observing"},
                {"name": "INTERACTING", "description": "Actively conversing"},
                {"name": "THINKING", "description": "Deep thought / reflection"},
            ],
            "on_input_transition": {
                "IDLE": "INTERACTING",
                "THINKING": "INTERACTING",
            },
        },
        "runtime": {
            "enabled": True,
            "signals": {
                "enabled": True,
                "warning_score": 0.82,
                "critical_score": 0.70,
                "retain": 500,
            },
            "auto_correction": {
                "enabled": False,
                "strategy": "rewrite",
                "trigger_levels": ["critical"],
            },
            "tool_tier_guard": {
                "enabled": True,
                "trigger_levels": ["warning", "critical"],
                "high_risk_levels": ["critical"],
                "downgrade_to": "safe",
                "downgrade_by_level": {
                    "critical": "safe",
                },
                "window_seconds": 1800,
                "sources": ["agent_loop", "persona_eval"],
            },
            "tool_policy_linkage": {
                "enabled": True,
                "trigger_levels": ["warning", "critical"],
                "high_risk_levels": ["critical"],
                "window_seconds": 1800,
                "sources": ["persona_eval"],
                "allow_names": [],
                "deny_names": [],
                "allow_providers": [],
                "deny_providers": [],
                "allow_names_by_level": {},
                "deny_names_by_level": {
                    "warning": ["exec", "node_invoke"],
                    "critical": ["exec", "node_invoke", "delegate_task", "remote_exec"],
                },
                "allow_providers_by_level": {},
                "deny_providers_by_level": {
                    "critical": ["devices", "system", "runtime"],
                },
            },
            "memory_context_guard": {
                "enabled": True,
                "trigger_levels": ["warning", "critical"],
                "window_seconds": 1800,
                "sources": ["agent_loop", "persona_eval"],
                "warning": {
                    "recent_limit": 12,
                    "entity_limit": 3,
                    "semantic_limit": 3,
                    "max_recall_items": 3,
                    "max_context_chars": 2200,
                    "include_relationship_context": True,
                    "include_time_reminders": True,
                    "include_emotion_context": True,
                    "include_recent_observation": True,
                },
                "critical": {
                    "recent_limit": 6,
                    "entity_limit": 2,
                    "semantic_limit": 2,
                    "max_recall_items": 2,
                    "max_context_chars": 1200,
                    "include_relationship_context": False,
                    "include_time_reminders": True,
                    "include_emotion_context": True,
                    "include_recent_observation": False,
                },
            },
        },
        "evolution": {
            "auto_optimize": {
                "enabled": False,
                "min_feedback_total": 6,
                "min_actionable_feedback": 3,
                "cooldown_seconds": 1800,
            },
            "publish_gate": {
                "enabled": True,
                "min_similarity": 0.45,
                "min_length_ratio": 0.5,
                "max_length_ratio": 2.0,
                "require_personality_name": True,
                "respect_release_gate": True,
            },
            "pre_publish_eval": {
                "enabled": True,
                "min_score": 0.55,
                "block_on_fail": True,
                "set_release_gate_on_fail": True,
            },
            "history": {
                "max_records": 300,
            },
        },
    },
    "voice": {
        "provider": "edge-tts",
        "voice_id": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "volume": "+0%",
        "cloud": {
            "provider": "disabled",
            # Optional provider registry key in config/model_providers.local.json
            "provider_ref": "",
            "base_url": "",
            "api_key": "",
            "model": "gpt-4o-mini-tts",
            "strict_required": False,
            "request_timeout_seconds": 20,
            "response_format": "pcm",
            "retry_count": 1,
            "fallback_to_edge": True,
        },
    },
    "visual": {
        "eye_color": [0, 200, 255],
        "blink_interval": 3000,
        "breathing_speed": 0.02
    },
    "perception": {
        "screen_enabled": True,
        "camera_enabled": False,
        "camera_device_index": 0,
        "capture_interval": 60,
        "satellite_ids": [],
        "action_enabled": True,
        "spatial_enabled": False,
        "spatial": {
            # local_mediapipe | cloud_vision | hybrid
            "provider": "local_mediapipe",
            # local_first | cloud_first | auto
            "route_mode": "local_first",
            "cloud": {
                # openai_compatible | disabled
                "provider": "disabled",
                # Optional provider registry key in config/model_providers.local.json
                "provider_ref": "",
                "base_url": "",
                "api_key": "",
                "model": "",
                "strict_required": False,
                "request_timeout_seconds": 15,
                "poll_interval_seconds": 1.5,
                "max_calls_per_minute": 20,
                "estimated_cost_per_call_usd": 0.001,
                "max_cost_per_minute_usd": 0.03,
            },
        },
    },
    "asr": {
        # whisper_local | cloud_openai_compatible | hybrid
        "provider": "whisper_local",
        "model_size": "base",
        "input_device": None,
        # local_first | cloud_first | auto
        "route_mode": "local_first",
        "cloud": {
            "provider": "disabled",
            # Optional provider registry key in config/model_providers.local.json
            "provider_ref": "",
            "base_url": "",
            "api_key": "",
            "model": "gpt-4o-mini-transcribe",
            "strict_required": False,
            "request_timeout_seconds": 20,
            "max_calls_per_minute": 20,
            "estimated_cost_per_call_usd": 0.002,
            "max_cost_per_minute_usd": 0.05,
        },
    },
    "devices": {
        "default_target": "local-desktop",
        "local": {
            # python | rust
            "backend": "python",
        },
        "local_node_id": "local-desktop",
        "local_node_label": "This Machine",
        "body_node": {
            "enabled": True,
            "node_id": "body-main",
            "label": "Physical Body",
            "allow_connect_control": True,
        },
        "satellite": {
            "enabled": True,
            "invoke_timeout_seconds": 15,
            "nodes": {},
            "default_allow_actions": [
                "screen.observe",
                "screen.screenshot",
                "input.mouse.click",
                "input.keyboard.type",
                "input.keyboard.hotkey",
                "file.send",
            ],
        },
    },
    "satellite": {
        # python | rust
        "transport_backend": "python",
        "max_pending_requests_per_node": 64,
        "pending_ttl_seconds": 30.0,
        "heartbeat_timeout_seconds": 45.0,
        "frame_window_seconds": 2.0,
        "max_frame_bytes_per_window": 4 * 1024 * 1024,
    },
    "coding": {
        # local | sandbox | ssh | rust
        "exec_backend": "local",
        # Max combined stdout/stderr chars returned by remote/sandbox backends.
        "max_output_chars": 100000,
        # Max parallel remote/sandbox backend operations.
        "max_parallel_tool_calls": 4,
        # When False, sandbox/ssh/rust backend failures will fail fast instead of local fallback.
        "allow_local_fallback": False,
        "ssh": {
            "enabled": False,
            "host": "",
            "user": "",
            "port": 22,
            "identity_file": "",
            "strict_host_key_checking": True,
            "remote_workspace": ".",
        },
    },
    "runtime": {
        # python | rust
        "backend": "python",
        "rust_sidecar": {
            "endpoint": "",
            "timeout_ms": 3000,
            "auto_fallback_on_error": True,
            "error_fallback_threshold": 3,
            "rollout": {
                "enabled": False,
                "owner_only": False,
                "channels": [],
            },
        },
    },
    "feishu": {
        "enabled": False,
        "app_id": os.getenv("FEISHU_APP_ID", ""),
        "app_secret": os.getenv("FEISHU_APP_SECRET", ""),
        "allowed_ids": [],
        "simulated_typing": {
            "enabled": False,
            "text": "正在思考中...",
            "min_interval_seconds": 8,
            "auto_recall_on_reply": True,
        },
        "media_analysis": {
            "enabled": True,
            "include_inbound_summary": True,
            "analyze_images": True,
            "transcribe_audio": True,
            "analyze_video_keyframe": True,
            "timeout_seconds": 12,
            "audio_whisper_model": "base",
        },
    },
    "telegram": {
        "enabled": True,
        "token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "allowed_ids": os.getenv("ALLOWED_USER_IDS", "").split(",") if os.getenv("ALLOWED_USER_IDS") else [],
        "dm_policy": "pairing"
    },
    "discord": {
        "enabled": False,
        "token": os.getenv("DISCORD_BOT_TOKEN", ""),
        "allowed_guild_ids": [],
        "dm_policy": "pairing",
    },
    "web": {
        "search": {
            "brave_api_key": "",
            "perplexity_api_key": "",
            "perplexity_base_url": "https://api.perplexity.ai",
            "perplexity_model": "sonar",
            "primary_provider": "brave",
            "primary_only": False,
            "report_file": "data/reports/web_search_observations.jsonl",
            "relevance_gate": {
                "enabled": True,
                "min_score": 0.25,
                "allow_low_relevance_fallback": True,
            },
            "providers_order": ["brave", "duckduckgo", "wikipedia", "bing_rss"],
            "providers_enabled": {
                "brave": True,
                "perplexity": False,
                "duckduckgo": True,
                "bing_rss": True,
                "wikipedia": True,
            },
            "scenario_routing": {
                "enabled": True,
                "auto_detect": True,
                "profiles": {
                    "general": ["brave", "duckduckgo", "wikipedia", "bing_rss"],
                    "news": ["brave", "duckduckgo", "bing_rss", "wikipedia"],
                    "reference": ["wikipedia", "duckduckgo", "brave", "bing_rss"],
                    "tech": ["duckduckgo", "brave", "wikipedia", "bing_rss"],
                },
            },
        },
    },
    "memory": {
        "context_backend": {
            # Controls whether the OpenViking client session bridge is enabled.
            "enabled": False,
            # openviking | disabled
            "mode": "openviking",
            # Persistent OpenViking data path.
            "data_dir": "data/openviking",
            # Optional explicit ov.conf path; when set, validated at startup.
            "config_file": "",
            # Session namespace prefix for Gazer-generated session ids.
            "session_prefix": "gazer",
            # Default session user identity for system-created sessions.
            "default_user": "owner",
            # Auto-commit active session after N forwarded messages.
            "commit_every_messages": 8,
        },
        "tool_result_persistence": {
            "enabled": True,
            # allowlist | denylist
            "mode": "allowlist",
            "allow_tools": [
                "web_search",
                "web_fetch",
                "web_report",
                "read_file",
                "grep",
                "find_files",
                "list_dir",
                "email_read",
                "email_search",
                "vision_query",
            ],
            "deny_tools": [
                "exec",
                "write_file",
                "edit_file",
                "node_invoke",
                "gui_task_execute",
                "git_commit",
                "git_push",
                "email_send",
                "hardware_control",
                "delegate_task",
            ],
            "persist_on_error": False,
            "min_result_chars": 16,
            "max_result_chars": 1200,
        },
    },
    "api": {
        # Cross-origin admin UI origins.
        # ⚠️ PRODUCTION: Replace with actual frontend domain
        # Never use "*" wildcard in production
        "cors_origins": [
            "http://localhost:5173",
            "http://localhost:8080",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8080",
        ],
        # Auto-detected by admin_api when null.
        "cors_credentials": None,
        # [REMOVED] allow_loopback_without_token - Security vulnerability fixed
        # Loopback authentication bypass is now permanently disabled
        # Strict CORS mode: reject any origin not explicitly whitelisted
        "cors_strict_mode": True,
        # Enforce Origin header validation for all state-changing operations
        "require_origin_for_mutations": True,
        # Web/API payload guardrails.
        "max_ws_message_bytes": 256 * 1024,
        "max_chat_message_chars": 8000,
        "max_upload_bytes": 10 * 1024 * 1024,
        # HttpOnly admin session cookie.
        "session_max_age_seconds": 86400,
        # Cap persisted session records in config/owner.json (oldest are pruned first).
        "session_max_records": 200,
        # None => auto-detect from scheme/proxy headers.
        "cookie_secure": None,
        "cookie_samesite": "strict",
        # Keep bearer support for backward compatibility; disable in hardened deployments.
        "allow_admin_bearer_token": True,
        # Export endpoints may only write under these project-relative roots.
        "export_allowed_dirs": [".task", ".tmp_pytest", "exports"],
        # Audit buffers are persisted; in-memory clear is disabled by default.
        "allow_audit_buffer_clear": False,
        # [REMOVED] local_bypass_environments - No longer used after bypass removal
        # MCP JSON-RPC governance for /mcp endpoint.
        "mcp": {
            "enabled": True,
            "rate_limit_requests": 120,
            "rate_limit_window_seconds": 60,
            "allow_tools": True,
            "allow_resources": True,
            "allow_prompts": True,
            # If non-empty, resources/prompts must match these allowlists.
            "allowed_resource_prefixes": [],
            "allowed_prompt_names": [],
            "audit_retain": 500,
        },
    },
    "security": {
        # DM policy for all channels: "open", "allowlist", or "pairing"
        "dm_policy": "pairing",
        # Owner's sender IDs on messaging channels (e.g. {"telegram": "123456"})
        "owner_channel_ids": {},
        # Read-only sender IDs per channel (e.g. {"telegram": ["111", "222"]}).
        # Read-only users may run query commands but cannot execute write commands.
        "readonly_channel_ids": {},
        # Tool safety: max tier exposed to non-primary users
        "tool_max_tier": "standard",
        # Tool denylist: names that are always disabled
        "tool_denylist": [],
        # Tool allowlist: if non-empty, only these tools are available
        "tool_allowlist": [],
        # Hard cap of tool calls in a single user turn (all iterations combined).
        "max_tool_calls_per_turn": 12,
        # Max concurrent tool calls when model emits multiple tool calls at once.
        "max_parallel_tool_calls": 4,
        # Per-lane concurrency caps for parallel tool calls.
        "parallel_tool_lane_limits": {
            "io": 2,
            "device": 1,
            "network": 2,
            "default": 2,
        },
        # Batch strategy for parallel tool calls (dedupe + grouping).
        "tool_batching": {
            "enabled": True,
            "max_batch_size": 4,
            "dedupe_enabled": False,
        },
        # Tool planner v2: dependency-aware scheduling + tool-result compaction.
        "tool_planner_v2": {
            "enabled": True,
            "dependency_keys": [
                "depends_on",
                "dependsOn",
                "after",
                "requires",
                "input_from",
                "from_call_id",
                "source_call_id",
                "parent_call_id",
            ],
            "compact_results": True,
            "max_result_chars": 2400,
            "error_max_result_chars": 4000,
            "head_chars": 900,
            "tail_chars": 700,
        },
        # Agent-loop tool call governance hooks (before/after).
        "tool_call_hooks": {
            "enabled": True,
            "loop_detection_enabled": True,
            "loop_max_repeats": 3,
            "loop_window_seconds": 90.0,
            "session_max_events": 256,
        },
        # Optional external threat-intel scanning for plugin/upload artifacts.
        "threat_scan": {
            "enabled": False,
            "provider": "virustotal",
            "api_key": "",
            "base_url": "https://www.virustotal.com/api/v3",
            "request_timeout_seconds": 8.0,
            "max_files": 64,
            # open = scan failure does not block, closed = scan failure blocks.
            "fail_mode": "open",
        },
        # Tool groups for agent-level policy (group_name -> tool names)
        "tool_groups": {
            "runtime": ["delegate_task", "cron"],
            "coding": [
                "exec",
                "read_file",
                "write_file",
                "edit_file",
                "list_dir",
                "find_files",
                "git_status",
                "read_skill",
                "git_diff",
                "git_commit",
                "git_log",
                "git_push",
                "grep",
                "git_branch",
            ],
            "desktop": [
                "node_list",
                "node_describe",
                "node_invoke",
                "gui_task_execute",
            ],
            "devices": ["node_list", "node_describe", "node_invoke", "gui_task_execute"],
            "web": ["web_search", "web_fetch"],
            "browser": ["browser"],
            "system": ["get_time", "image_analyze"],
            "canvas": ["a2ui_apply", "canvas_snapshot", "canvas_reset"],
            "email": ["email_list", "email_read", "email_send", "email_search"],
            "hardware": ["hardware_control", "vision_query"],
        },
        # Auto-approve PRIVILEGED tool execution (default: False for safety)
        "auto_approve_privileged": False,
        # Release gate hard block:
        # when true and release gate is blocked, only SAFE tools are allowed.
        "release_gate_enforcement": True,
        # Emergency switch for owners during development incidents.
        "release_gate_owner_bypass": False,
        # Auto-link release gate with workflow/persona health signals.
        "release_gate_auto_link_enabled": True,
        # Enable release-gate auto-link after each coding benchmark run endpoint.
        "coding_benchmark_auto_link_on_run": False,
        # Coding benchmark scheduler: periodically run benchmark suite payload.
        "coding_benchmark_scheduler": {
            "enabled": False,
            "interval_seconds": 1800,
            "auto_link_release_gate": True,
            "window": 20,
            "payload": {
                "name": "scheduled_suite",
                "cases": [],
            },
        },
        # Number of consecutive blocked benchmark runs before creating optimization tasks.
        "optimization_fail_streak_threshold": 2,
        # Tool runtime resilience: per-tool circuit breaker.
        "tool_circuit_breaker_enabled": True,
        "tool_circuit_breaker_failures": 3,
        "tool_circuit_breaker_cooldown_seconds": 30,
        # Tool runtime budget: global execution budget within a rolling window.
        "tool_budget_enabled": False,
        "tool_budget_window_seconds": 60,
        "tool_budget_max_calls": 120,
        "tool_budget_max_weight": 120.0,
        "tool_budget_max_calls_by_group": {},
        "tool_budget_weight_by_group": {},
        "tool_budget_weight_by_tool": {},
        # Rate limiting: max requests per window per sender
        "rate_limit_requests": 30,
        "rate_limit_window": 60,  # seconds
    },
    "scheduler": {
        "cron_enabled": True,
        "heartbeat_enabled": True,
        "heartbeat_interval": 300,  # seconds
    },
    "hooks": {
        "enabled": True,
        "token": "",  # Separate token for webhook auth (optional)
    },
    "sandbox": {
        "enabled": False,
        "image": "python:3.11-slim",
        "workspace_mode": "rw",  # "none", "ro", "rw"
    },
    "agents": {
        # OpenClaw-style default model and agent runtime settings.
        "defaults": {
            "model": {
                "primary": "dashscope/qwen-max",
                "fallbacks": ["dashscope/qwen-turbo"],
            },
            "models": {
                "dashscope/qwen-max": {"alias": "Qwen Max"},
                "dashscope/qwen-turbo": {"alias": "Qwen Turbo"},
            },
            "workspace": ".",
            "compaction": {
                "mode": "safeguard",
            },
            "planning": {
                "mode": "auto",  # always | auto | off
                "auto": {
                    "min_message_chars": 220,
                    "min_history_messages": 8,
                    "min_line_breaks": 2,
                    "min_list_lines": 2,
                },
            },
            "maxConcurrent": 4,
            "subagents": {
                "maxConcurrent": 8,
            },
        },
        # Multi-agent configuration (list of sub-agent definitions)
        "list": [
            {
                "id": "mailbot",
                "name": "Gmail Auto Reply",
                "workspace": ".",
                "tool_policy": {
                    "allow_groups": ["email"],
                },
            },
        ],
        # Bindings: route messages to specific agents
        "bindings": [
            {
                "agent_id": "mailbot",
                "channel": "webhook",
                "chat_id": "event:gmail:main",
                "sender_id": "hook:gmail",
            },
        ],
        # Orchestrator execution controls for delegated multi-agent tasks.
        "orchestrator": {
            "max_parallel_tasks": 3,
            "max_parallel_per_agent": 2,
            "max_pending_tasks": 64,
            "resource_lock_timeout_seconds": 30.0,
            "sleep_wake": {
                "poll_interval_seconds": 1.0,
                "max_sleep_seconds": 3600.0,
            },
            "sla": {
                "timeout_seconds": 120.0,
                "max_retries": 0,
                "retry_backoff_seconds": 0.0,
                "priority": "normal",  # high | normal | low
            },
        },
        # Preset templates for reuse/customization.
        "templates": {
            "gmail_webhook_autoreply": {
                "agent": {
                    "id": "mailbot",
                    "name": "Gmail Auto Reply",
                    "workspace": ".",
                    "tool_policy": {
                        "allow_groups": ["email"],
                    },
                },
                "binding": {
                    "agent_id": "mailbot",
                    "channel": "webhook",
                    "chat_id": "event:gmail:main",
                    "sender_id": "hook:gmail",
                },
                "notes": [
                    "This template is a starting point; copy values into agents.list and agents.bindings.",
                    "Use email_send with reply_to=message_id from webhook metadata for threaded replies.",
                ],
            },
        },
    },
    "plugins": {
        # Explicitly enabled external plugin IDs
        "enabled": [],
        # Disabled bundled plugin IDs (bundled plugins load by default)
        "disabled": [],
        # Plugin market / supply-chain verification
        "signature": {
            "enforce": False,
            "allow_unsigned": True,
            # key_id -> shared secret (for lightweight HMAC signing)
            "trusted_keys": {},
        },
    },
    "skill_registry": {
        "registry_url": "",  # URL to remote skill index JSON
    },
    "skills": {
        "registry_url": "",  # URL to remote skill index JSON (legacy, prefer skill_registry)
    },
    "canvas": {
        "enabled": True,
        "max_panels": 20,
        "max_content_size": 65536,  # bytes per panel
    },
    "trainer": {
        "enabled": True,
        "auto_run_on_gate_fail": True,
        "max_samples_per_job": 200,
        "auto_publish_on_pass": False,
        "online_policy_loop": {
            "enabled": True,
            "require_review": True,
            "gate": {
                "require_release_gate_open": True,
                "min_eval_pass_rate": 0.55,
                "min_trajectory_success_rate": 0.6,
                "max_terminal_error_rate": 0.4,
            },
        },
        "canary": {
            "default_percent": 10,
            "auto_rollout_on_publish": False,
            "auto_rollback_on_gate_fail": True,
            "auto_rollback_on_canary_fail": True,
        },
        "release_approval": {
            "enabled": False,
            "required_modes": ["canary"],
            "require_note": False,
        },
        "experiments": {
            "enabled": True,
        },
    },
    "observability": {
        "release_gate_health_thresholds": {
            "warning_success_rate": 0.90,
            "critical_success_rate": 0.75,
            "warning_failures": 1,
            "critical_failures": 3,
            "warning_p95_latency_ms": 2500,
            "critical_p95_latency_ms": 4000,
            "warning_persona_consistency_score": 0.82,
            "critical_persona_consistency_score": 0.70,
        },
        "cost_quality_slo_targets": {
            "min_success_rate": 0.90,
            "max_p95_latency_ms": 3000.0,
            "max_avg_retries_per_run": 1.5,
            "max_downgrade_trigger_rate": 0.20,
        },
        "efficiency_baseline_targets": {
            "min_success_rate": 0.90,
            "max_p95_latency_ms": 3000.0,
            "max_avg_tokens_per_run": 6000.0,
            "max_tool_error_rate": 0.20,
        },
        "alerts": {
            "enabled": True,
            "retain": 200,
        },
    },
    "email": {
        "enabled": False,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "username": "",
        "password": os.getenv("GAZER_EMAIL_PASSWORD", ""),  # Prefer env var; fallback to config
        "max_body_length": 8000,
    },
    "gmail_push": {
        "enabled": False,
        "credentials_file": "config/gmail_credentials.json",
        "token_file": "config/gmail_token.json",
        "topic": "",  # e.g. "projects/my-project/topics/gazer-gmail"
        "history_store": "data/gmail_history.json",
    },
    "body": {
        "type": "none",       # none | serial_arm
        "port": "auto",       # serial port or "auto" for auto-detect
        "baudrate": 115200,
    },
    "ui": {
        "enabled": False,
    },
}

class ConfigManager:
    """
    Gazer configuration center.
    Supports YAML persistence and in-memory dynamic updates.
    """
    PERSONA_SYSTEM_PROMPT_KEY = "personality.system_prompt"
    NON_PERSISTED_KEYS = {PERSONA_SYSTEM_PROMPT_KEY}

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config_path = config_path
        self._lock_path = f"{self.config_path}.lock"
        self.data: Dict[str, Any] = {}
        self._last_mtime = 0
        self._load()

    def _resolve_workspace_root(self) -> Path:
        cfg_path = Path(self.config_path).expanduser()
        if not cfg_path.is_absolute():
            cfg_path = (Path.cwd() / cfg_path).resolve()
        else:
            cfg_path = cfg_path.resolve()
        cfg_dir = cfg_path.parent
        if cfg_dir.name.lower() == "config":
            return cfg_dir.parent
        return cfg_dir

    def _resolve_soul_path(self) -> Path:
        return self._resolve_workspace_root() / "assets" / "SOUL.md"

    def _read_soul_prompt(self) -> str:
        soul_path = self._resolve_soul_path()
        if not soul_path.is_file():
            return ""
        try:
            return soul_path.read_text(encoding="utf-8").strip()
        except OSError:
            logger.debug("Failed to read SOUL prompt from %s", soul_path, exc_info=True)
            return ""

    def _write_soul_prompt(self, prompt: str) -> None:
        soul_path = self._resolve_soul_path()
        try:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            text = str(prompt or "").strip()
            payload = f"{text}\n" if text else ""
            soul_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"Failed to write SOUL prompt to '{soul_path}': {exc}") from exc

    def _sync_persona_system_prompt(self, loaded_prompt: str = "") -> None:
        soul_prompt = self._read_soul_prompt()
        if not soul_prompt and str(loaded_prompt or "").strip():
            self._write_soul_prompt(str(loaded_prompt or "").strip())
            soul_prompt = self._read_soul_prompt()
            logger.info("Migrated personality.system_prompt into %s", self._resolve_soul_path())

        if soul_prompt:
            self._set_in_memory(self.PERSONA_SYSTEM_PROMPT_KEY, soul_prompt)
        else:
            fallback = str(
                self.get(
                    self.PERSONA_SYSTEM_PROMPT_KEY,
                    DEFAULT_CONFIG.get("personality", {}).get("system_prompt", ""),
                )
                or ""
            )
            if fallback:
                self._set_in_memory(self.PERSONA_SYSTEM_PROMPT_KEY, fallback)

    @staticmethod
    def _delete_dot_path(payload: Dict[str, Any], key_path: str) -> None:
        keys = [segment for segment in str(key_path).split(".") if segment]
        if not keys or not isinstance(payload, dict):
            return
        current: Any = payload
        stack: List[tuple[Dict[str, Any], str]] = []
        for key in keys[:-1]:
            if not isinstance(current, dict) or key not in current:
                return
            stack.append((current, key))
            current = current[key]
        if not isinstance(current, dict):
            return
        current.pop(keys[-1], None)
        # Prune now-empty parent objects, but stop at the root.
        for parent, child_key in reversed(stack):
            child = parent.get(child_key)
            if isinstance(child, dict) and not child:
                parent.pop(child_key, None)
            else:
                break

    def _load(self):
        """Load config from file; use defaults and save if not found."""
        config_dir = os.path.dirname(self.config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)
        if os.path.exists(self.config_path):
            try:
                mtime = os.path.getmtime(self.config_path)
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                self.data = loaded if isinstance(loaded, dict) else {}
                loaded_prompt = ""
                if isinstance(loaded, dict):
                    personality = loaded.get("personality", {})
                    if isinstance(personality, dict):
                        loaded_prompt = str(personality.get("system_prompt", "") or "")
                self._last_mtime = mtime
                self._validate_schema_strict()
                # Merge defaults to ensure newly added config keys exist
                self._merge_defaults(self.data, DEFAULT_CONFIG)
                # Deprecated: privileged owner bypass is now unconditional for owner ids.
                sec = self.data.get("security")
                if isinstance(sec, dict) and "auto_approve_owner_privileged" in sec:
                    sec.pop("auto_approve_owner_privileged", None)
                    logger.warning(
                        "Deprecated config key 'security.auto_approve_owner_privileged' "
                        "found and removed. Owner bypass is now always enforced."
                    )
                # Restore sensitive fields from env-var defaults when stripped
                self._restore_sensitive_from_defaults(self.data, DEFAULT_CONFIG)
                # Keep persona prompt single-sourced from assets/SOUL.md.
                self._sync_persona_system_prompt(loaded_prompt=loaded_prompt)
                logger.info(f"Configuration loaded from {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to load config: {e}.")
                raise RuntimeError(f"Failed to load config '{self.config_path}': {e}") from e
        else:
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self._sync_persona_system_prompt()
            self.save()

    def _validate_schema_strict(self) -> None:
        """Fail fast in development when deprecated config keys are present."""
        deprecated_paths: List[str] = []
        if isinstance(self.data, dict):
            models = self.data.get("models")
            if isinstance(models, dict):
                if "active_profile" in models:
                    deprecated_paths.append("models.active_profile")

        if deprecated_paths:
            joined = ", ".join(deprecated_paths)
            raise ValueError(
                "Deprecated config keys detected: "
                f"{joined}. Use agents.defaults.model.primary/fallbacks."
            )

    def check_reload(self):
        """Check if the config file has been modified and reload if so."""
        if os.path.exists(self.config_path):
            mtime = os.path.getmtime(self.config_path)
            if mtime > self._last_mtime:
                self._load()

    def _merge_defaults(self, target: dict, source: dict):
        """Recursively merge default values into target."""
        for k, v in source.items():
            if k not in target:
                target[k] = copy.deepcopy(v)
            elif isinstance(v, dict) and isinstance(target.get(k), dict):
                self._merge_defaults(target[k], v)

    def _restore_sensitive_from_defaults(
        self, target: dict, defaults: dict, path: str = ""
    ) -> None:
        """Restore sensitive fields that were stripped on save.

        If a sensitive field in the loaded config is empty but the
        DEFAULT_CONFIG (which reads env vars) has a non-empty value,
        use the default value.  This allows secrets to live in env vars
        while the YAML file stays clean.
        """
        for key, default_value in defaults.items():
            current_path = f"{path}.{key}" if path else key
            if isinstance(default_value, dict) and isinstance(target.get(key), dict):
                self._restore_sensitive_from_defaults(target[key], default_value, current_path)
            elif self._is_sensitive_path(current_path):
                # If file value is empty/stripped but env var provides a value, use it
                file_value = target.get(key, "")
                if (not file_value or file_value == "***") and default_value:
                    target[key] = default_value
                    logger.debug(f"Restored sensitive field '{current_path}' from environment.")

    def get(self, key_path: str, default: Any = None) -> Any:
        """Get config value by dot-separated path, e.g. 'api.model'."""
        if key_path == self.PERSONA_SYSTEM_PROMPT_KEY:
            soul_prompt = self._read_soul_prompt()
            if soul_prompt:
                self._set_in_memory(self.PERSONA_SYSTEM_PROMPT_KEY, soul_prompt)
                return soul_prompt
        keys = key_path.split(".")
        current = self.data
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        return current

    def set(self, key_path: str, value: Any):
        """Set a config value by dot-separated path and auto-save."""
        if key_path == self.PERSONA_SYSTEM_PROMPT_KEY:
            text = str(value or "").strip()
            self._write_soul_prompt(text)
            self._set_in_memory(key_path, text)
        else:
            self._set_in_memory(key_path, value)
        self.save()

    def set_many(self, updates: Dict[str, Any]) -> None:
        """Set multiple config values and save once."""
        for key_path, value in updates.items():
            if key_path == self.PERSONA_SYSTEM_PROMPT_KEY:
                text = str(value or "").strip()
                self._write_soul_prompt(text)
                self._set_in_memory(key_path, text)
            else:
                self._set_in_memory(key_path, value)
        self.save()

    def _set_in_memory(self, key_path: str, value: Any) -> None:
        """Set a config value by dot-separated path without saving."""
        keys = key_path.split(".")
        current = self.data
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value

    def save(self):
        """Persist current config to YAML file.

        Values are persisted as entered (except keys single-sourced
        outside YAML, such as ``personality.system_prompt``). Use
        ``to_safe_dict()`` when
        exposing config through APIs to avoid leaking sensitive fields.
        """
        try:
            config_dir = os.path.dirname(self.config_path) or "."
            os.makedirs(config_dir, exist_ok=True)
            # Persist full effective config without pruning default-valued keys.
            # This keeps settings.yaml explicit and avoids "missing key" surprises.
            persist_data = copy.deepcopy(self.data) if isinstance(self.data, dict) else {}
            for key_path in self.NON_PERSISTED_KEYS:
                self._delete_dot_path(persist_data, key_path)
            persist_data = self._order_for_persist(persist_data)
            with file_lock(self._lock_path, timeout=5.0):
                fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".yaml.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        yaml.safe_dump(persist_data, f, allow_unicode=True, sort_keys=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_path, self.config_path)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                self._last_mtime = os.path.getmtime(self.config_path)
            logger.info("Configuration saved.")
        except FileLockError as e:
            logger.error(f"Failed to save config (lock timeout): {e}")
            raise RuntimeError(f"Failed to save config (lock timeout): {e}") from e
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise RuntimeError(f"Failed to save config: {e}") from e

    def _order_for_persist(self, value: Any, path: str = "") -> Any:
        """Return a copy of *value* with stable key ordering for YAML persistence."""
        if isinstance(value, dict):
            preferred_orders: Dict[str, List[str]] = {
                "": [
                    "config_version",
                    "models",
                    "wake_word",
                    "personality",
                    "voice",
                    "visual",
                    "perception",
                    "asr",
                    "devices",
                    "satellite",
                    "coding",
                    "runtime",
                    "feishu",
                    "telegram",
                    "discord",
                    "web",
                    "memory",
                    "api",
                    "security",
                    "scheduler",
                    "hooks",
                    "sandbox",
                    "agents",
                    "plugins",
                    "skill_registry",
                    "skills",
                    "canvas",
                    "trainer",
                    "observability",
                    "email",
                    "gmail_push",
                    "body",
                    "ui",
                ],
                "models": ["embedding", "router", "prompt_cache", "deployment_profiles"],
                "models.embedding": ["provider", "model"],
                "models.router": [
                    "enabled",
                    "strategy",
                    "strategy_template",
                    "candidates",
                    "deployment_targets",
                    "rollout",
                    "complexity_routing",
                    "budget",
                    "outlier_ejection",
                    "deployment_orchestrator",
                ],
                "models.prompt_cache": [
                    "enabled",
                    "ttl_seconds",
                    "max_items",
                    "segment_policy",
                    "scope_fields",
                    "sanitize_sensitive",
                ],
                "voice": ["provider", "voice_id", "rate", "volume", "cloud"],
                "voice.cloud": [
                    "provider",
                    "provider_ref",
                    "base_url",
                    "api_key",
                    "model",
                    "strict_required",
                    "request_timeout_seconds",
                    "response_format",
                    "retry_count",
                    "fallback_to_edge",
                ],
                "perception": [
                    "screen_enabled",
                    "camera_enabled",
                    "camera_device_index",
                    "capture_interval",
                    "satellite_ids",
                    "action_enabled",
                    "spatial_enabled",
                    "spatial",
                ],
                "perception.spatial": ["provider", "route_mode", "cloud"],
                "perception.spatial.cloud": [
                    "provider",
                    "provider_ref",
                    "base_url",
                    "api_key",
                    "model",
                    "strict_required",
                    "request_timeout_seconds",
                    "poll_interval_seconds",
                    "max_calls_per_minute",
                    "estimated_cost_per_call_usd",
                    "max_cost_per_minute_usd",
                ],
                "asr": ["provider", "model_size", "input_device", "route_mode", "cloud"],
                "asr.cloud": [
                    "provider",
                    "provider_ref",
                    "base_url",
                    "api_key",
                    "model",
                    "strict_required",
                    "request_timeout_seconds",
                    "max_calls_per_minute",
                    "estimated_cost_per_call_usd",
                    "max_cost_per_minute_usd",
                ],
                "devices": [
                    "default_target",
                    "local",
                    "local_node_id",
                    "local_node_label",
                    "body_node",
                    "satellite",
                ],
                "devices.local": ["backend"],
                "satellite": [
                    "transport_backend",
                    "max_pending_requests_per_node",
                    "pending_ttl_seconds",
                    "heartbeat_timeout_seconds",
                    "frame_window_seconds",
                    "max_frame_bytes_per_window",
                ],
                "coding": [
                    "exec_backend",
                    "max_output_chars",
                    "max_parallel_tool_calls",
                    "allow_local_fallback",
                    "ssh",
                ],
                "runtime": ["backend", "rust_sidecar"],
                "runtime.rust_sidecar": [
                    "endpoint",
                    "timeout_ms",
                    "auto_fallback_on_error",
                    "error_fallback_threshold",
                    "rollout",
                ],
                "runtime.rust_sidecar.rollout": [
                    "enabled",
                    "owner_only",
                    "channels",
                ],
                "feishu": ["enabled", "app_id", "app_secret", "allowed_ids", "simulated_typing", "media_analysis"],
                "feishu.simulated_typing": [
                    "enabled",
                    "text",
                    "min_interval_seconds",
                    "auto_recall_on_reply",
                ],
                "feishu.media_analysis": [
                    "enabled",
                    "include_inbound_summary",
                    "analyze_images",
                    "transcribe_audio",
                    "analyze_video_keyframe",
                    "timeout_seconds",
                    "audio_whisper_model",
                ],
                "web": ["search"],
                "web.search": [
                    "brave_api_key",
                    "perplexity_api_key",
                    "perplexity_base_url",
                    "perplexity_model",
                    "primary_provider",
                    "primary_only",
                    "report_file",
                    "relevance_gate",
                    "providers_order",
                    "providers_enabled",
                    "scenario_routing",
                ],
                "web.search.providers_enabled": ["brave", "perplexity", "duckduckgo", "bing_rss", "wikipedia"],
                "web.search.relevance_gate": ["enabled", "min_score", "allow_low_relevance_fallback"],
                "web.search.scenario_routing": ["enabled", "auto_detect", "profiles"],
                "web.search.scenario_routing.profiles": ["general", "news", "reference", "tech"],
                "memory": ["context_backend", "tool_result_persistence"],
                "memory.context_backend": [
                    "enabled",
                    "mode",
                    "data_dir",
                    "config_file",
                    "session_prefix",
                    "default_user",
                    "commit_every_messages",
                ],
                "memory.tool_result_persistence": [
                    "enabled",
                    "mode",
                    "allow_tools",
                    "deny_tools",
                    "persist_on_error",
                    "min_result_chars",
                    "max_result_chars",
                ],
                "observability": [
                    "release_gate_health_thresholds",
                    "cost_quality_slo_targets",
                    "efficiency_baseline_targets",
                    "alerts",
                ],
                "api": [
                    "cors_origins",
                    "cors_credentials",
                    "allow_loopback_without_token",
                    "max_ws_message_bytes",
                    "max_chat_message_chars",
                    "max_upload_bytes",
                    "session_max_age_seconds",
                    "session_max_records",
                    "cookie_secure",
                    "cookie_samesite",
                    "allow_admin_bearer_token",
                    "export_allowed_dirs",
                    "allow_audit_buffer_clear",
                    "local_bypass_environments",
                    "mcp",
                ],
                "api.mcp": [
                    "enabled",
                    "rate_limit_requests",
                    "rate_limit_window_seconds",
                    "allow_tools",
                    "allow_resources",
                    "allow_prompts",
                    "allowed_resource_prefixes",
                    "allowed_prompt_names",
                    "audit_retain",
                ],
                "agents": [
                    "defaults",
                    "list",
                    "bindings",
                    "orchestrator",
                    "templates",
                ],
                "agents.defaults": [
                    "model",
                    "models",
                    "workspace",
                    "compaction",
                    "planning",
                    "maxConcurrent",
                    "subagents",
                ],
                "agents.defaults.model": ["primary", "fallbacks"],
                "agents.defaults.compaction": ["mode"],
                "agents.defaults.planning": ["mode", "auto"],
                "agents.defaults.planning.auto": [
                    "min_message_chars",
                    "min_history_messages",
                    "min_line_breaks",
                    "min_list_lines",
                ],
                "agents.defaults.subagents": ["maxConcurrent"],
                "agents.orchestrator": [
                    "max_parallel_tasks",
                    "max_parallel_per_agent",
                    "max_pending_tasks",
                    "resource_lock_timeout_seconds",
                    "sleep_wake",
                    "sla",
                ],
                "agents.orchestrator.sleep_wake": [
                    "poll_interval_seconds",
                    "max_sleep_seconds",
                ],
                "agents.orchestrator.sla": [
                    "timeout_seconds",
                    "max_retries",
                    "retry_backoff_seconds",
                    "priority",
                ],
            }
            preferred = preferred_orders.get(path, [])
            ordered: Dict[str, Any] = {}

            # Add preferred keys first when present.
            for key in preferred:
                if key in value:
                    child_path = f"{path}.{key}" if path else key
                    ordered[key] = self._order_for_persist(value[key], child_path)

            # Keep remaining keys in existing insertion order.
            for key, child in value.items():
                if key in ordered:
                    continue
                child_path = f"{path}.{key}" if path else key
                ordered[key] = self._order_for_persist(child, child_path)
            return ordered

        if isinstance(value, list):
            return [self._order_for_persist(item, path) for item in value]

        return value

    def _prune_defaults_for_persist(self, value: Any, default: Any, has_default: bool = False) -> Any:
        """Strip values equal to defaults before writing config file."""
        if isinstance(value, dict):
            result: Dict[str, Any] = {}
            default_dict = default if (has_default and isinstance(default, dict)) else {}
            for key, item in value.items():
                child_has_default = key in default_dict
                if child_has_default:
                    pruned = self._prune_defaults_for_persist(
                        item,
                        default_dict[key],
                        has_default=True,
                    )
                    if pruned is _PRUNE_MARKER:
                        continue
                    result[key] = pruned
                else:
                    result[key] = copy.deepcopy(item)
            if has_default and not result:
                return _PRUNE_MARKER
            return result

        if has_default and value == default:
            return _PRUNE_MARKER
        return copy.deepcopy(value)

    def to_safe_dict(self) -> Dict[str, Any]:
        """Return a copy of config with sensitive values masked.

        Use this when returning config through the web admin API to
        avoid leaking secrets to the frontend.
        """
        safe = self._mask_sensitive(copy.deepcopy(self.data))
        soul_prompt = str(self.get(self.PERSONA_SYSTEM_PROMPT_KEY, "") or "")
        personality = safe.get("personality")
        if not isinstance(personality, dict):
            personality = {}
            safe["personality"] = personality
        personality["system_prompt"] = soul_prompt
        return safe

    def _mask_sensitive(
        self, data: Dict[str, Any], path: str = ""
    ) -> Dict[str, Any]:
        """Recursively mask sensitive fields for safe API exposure.

        Returns:
            Copy with sensitive values replaced by '***'.
        """
        result = {}
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key

            if self._is_sensitive_path(current_path):
                result[key] = "***" if value else ""
            elif isinstance(value, dict):
                result[key] = self._mask_sensitive(value, current_path)
            else:
                result[key] = value
        return result

    def _is_sensitive_path(self, path: str) -> bool:
        """Check if a config path matches any sensitive key pattern.
        
        Supports wildcard (*) for one path segment and (**) for recursive matching.
        """
        return is_sensitive_config_path(path)

    def _match_pattern(self, pattern: str, path: str) -> bool:
        """Match a dot-separated pattern with wildcards against a path.
        
        Example: "plugins.signature.trusted_keys.*" matches "plugins.signature.trusted_keys.prod"
        """
        return _match_pattern(pattern, path)

# Lazy singleton -- avoids file I/O at import time.
_config_instance: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    """Return the lazy ConfigManager singleton."""
    global _config_instance
    if _config_instance is None:
        _config_instance = ConfigManager()
    return _config_instance


class _ConfigProxy:
    """Transparent proxy so ``from runtime.config_manager import config`` works
    without triggering file I/O at import time.  All attribute access is
    forwarded to the lazily-initialized ConfigManager singleton."""

    def __getattr__(self, name: str):
        return getattr(get_config(), name)

    def __setattr__(self, name: str, value):
        setattr(get_config(), name, value)


config = _ConfigProxy()
