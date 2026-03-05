"""Default configuration for Gazer.

Extracted from ``config_manager.py`` to keep the config class focused on
load / merge / persist logic.  This module is imported by
``config_manager.py`` and is the single source of truth for all default
config values.
"""

import os

LATEST_CONFIG_VERSION = 2

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
        # Tool planner: dependency-aware scheduling + tool-result compaction.
        "tool_planner": {
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
