from pathlib import Path
from types import SimpleNamespace
import copy

import pytest

import runtime.config_manager as config_manager
from agent.loop import AgentLoop
from bus.events import InboundMessage
from bus.queue import MessageBus


class _FakeConfig:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key_path: str, default=None):
        cur = self.data
        for part in key_path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set_many(self, updates: dict) -> None:
        for key_path, value in updates.items():
            cur = self.data
            parts = key_path.split(".")
            for part in parts[:-1]:
                if part not in cur or not isinstance(cur[part], dict):
                    cur[part] = {}
                cur = cur[part]
            cur[parts[-1]] = value


class _DummyContext:
    async def prepare_memory_context(self, _content: str):
        return None

    def build_messages(self, *, history, current_message, media=None, channel=None, chat_id=None):
        return [{"role": "user", "content": current_message}]

    def add_assistant_message(self, messages, content, tool_calls):
        return [*messages, {"role": "assistant", "content": content, "tool_calls": tool_calls}]

    def add_tool_result(self, messages, tool_call_id, tool_name, result):
        return [*messages, {"role": "tool", "content": result, "tool_call_id": tool_call_id, "name": tool_name}]


class _Provider:
    def __init__(self):
        self.calls = 0

    def get_default_model(self) -> str:
        return "gpt-4o"

    async def chat(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("LLM should not be called for channel commands")


def _make_loop(monkeypatch, tmp_path, cfg_data: dict, *, owner_sender_ids: set[str]):
    normalized_cfg = copy.deepcopy(cfg_data)
    models_cfg = normalized_cfg.get("models", {}) if isinstance(normalized_cfg, dict) else {}
    active_profile = models_cfg.get("active_profile", {}) if isinstance(models_cfg, dict) else {}
    if isinstance(active_profile, dict):
        slow = active_profile.get("slow_brain", {}) if isinstance(active_profile.get("slow_brain"), dict) else {}
        fast = active_profile.get("fast_brain", {}) if isinstance(active_profile.get("fast_brain"), dict) else {}
        slow_provider = str(slow.get("provider", "")).strip()
        slow_model = str(slow.get("model", "")).strip()
        fast_provider = str(fast.get("provider", "")).strip()
        fast_model = str(fast.get("model", "")).strip()
        if slow_provider and slow_model:
            agents_cfg = normalized_cfg.setdefault("agents", {})
            defaults_cfg = agents_cfg.setdefault("defaults", {})
            model_cfg = defaults_cfg.setdefault("model", {})
            if isinstance(model_cfg, dict):
                model_cfg["primary"] = f"{slow_provider}/{slow_model}"
                if fast_provider and fast_model:
                    model_cfg["fallbacks"] = [f"{fast_provider}/{fast_model}"]
                elif "fallbacks" not in model_cfg:
                    model_cfg["fallbacks"] = []

    fake_cfg = _FakeConfig(normalized_cfg)
    monkeypatch.setattr(config_manager, "config", fake_cfg)

    def _is_owner(channel: str, sender_id: str) -> bool:
        return f"{channel}:{sender_id}" in owner_sender_ids

    _owner_stub = lambda: SimpleNamespace(is_owner_sender=_is_owner)
    monkeypatch.setattr(
        "security.owner.get_owner_manager",
        _owner_stub,
    )
    # After extracting _execute_channel_command into the mixin, its module
    # has its own reference to get_owner_manager that also needs patching.
    monkeypatch.setattr(
        "agent.loop_mixins.channel_commands.get_owner_manager",
        _owner_stub,
    )

    provider = _Provider()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=Path(tmp_path),
        context_builder=_DummyContext(),
    )
    return loop, provider, fake_cfg


@pytest.mark.asyncio
async def test_channel_help_command_short_circuits_llm(monkeypatch, tmp_path):
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            }
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="/help")
    )
    assert out is not None
    assert "可用命令" in out.content
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_model_show_command_returns_profiles(monkeypatch, tmp_path):
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "dashscope", "model": "qwen-max"},
                    "fast_brain": {"provider": "dashscope", "model": "qwen-turbo"},
                }
            }
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="discord", sender_id="u2", chat_id="c2", content="+model")
    )
    assert out is not None
    assert "slow_brain" in out.content
    assert "dashscope" in out.content
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_model_set_requires_owner_for_remote_channels(monkeypatch, tmp_path):
    loop, provider, cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            }
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(
            channel="telegram",
            sender_id="u3",
            chat_id="c3",
            content="/model set slow dashscope qwen-max",
        )
    )
    assert out is not None
    assert "权限不足" in out.content
    assert cfg.get("agents.defaults.model.primary") == "openai/gpt-4o"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_model_set_by_owner_updates_config_and_runtime_model(monkeypatch, tmp_path):
    loop, provider, cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            }
        },
        owner_sender_ids={"telegram:owner1"},
    )
    out = await loop._process_message(
        InboundMessage(
            channel="telegram",
            sender_id="owner1",
            chat_id="c4",
            content="/model set slow openai gpt-4o-mini",
        )
    )
    assert out is not None
    assert "已更新 slow_brain" in out.content
    assert cfg.get("agents.defaults.model.primary") == "openai/gpt-4o-mini"
    assert loop.model == "gpt-4o-mini"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_router_show_command_returns_status(monkeypatch, tmp_path):
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                },
                "router": {
                    "enabled": True,
                    "strategy": "priority",
                    "strategy_template": "custom",
                    "rollout": {"enabled": True, "owner_only": True, "channels": []},
                },
            }
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="discord", sender_id="u7", chat_id="c7", content="/router")
    )
    assert out is not None
    assert "当前 Router 配置" in out.content
    assert "enabled=True" in out.content
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_router_off_requires_owner_for_remote_channels(monkeypatch, tmp_path):
    loop, provider, cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                },
                "router": {
                    "enabled": True,
                    "strategy": "priority",
                    "strategy_template": "custom",
                    "rollout": {"enabled": True, "owner_only": True, "channels": []},
                },
            }
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="u8", chat_id="c8", content="/router off")
    )
    assert out is not None
    assert "权限不足" in out.content
    assert cfg.get("models.router.enabled") is True
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_router_off_by_owner_applies_one_click_degrade(monkeypatch, tmp_path):
    loop, provider, cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                },
                "router": {
                    "enabled": True,
                    "strategy": "priority",
                    "strategy_template": "custom",
                    "rollout": {"enabled": True, "owner_only": True, "channels": []},
                },
            }
        },
        owner_sender_ids={"telegram:owner2"},
    )
    out = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="owner2", chat_id="c9", content="/router off")
    )
    assert out is not None
    assert "一键降级" in out.content
    assert cfg.get("models.router.enabled") is False
    assert cfg.get("models.router.rollout.enabled") is False
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_tools_show_command_returns_runtime_status(monkeypatch, tmp_path):
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            },
            "security": {
                "max_tool_calls_per_turn": 12,
                "max_parallel_tool_calls": 4,
                "parallel_tool_lane_limits": {"io": 2, "device": 1, "network": 2, "default": 2},
            },
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="discord", sender_id="u10", chat_id="c10", content="/tools")
    )
    assert out is not None
    assert "当前工具运行状态" in out.content
    assert "role=普通用户" in out.content
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_policy_show_command_returns_policy_snapshot(monkeypatch, tmp_path):
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            },
            "security": {
                "tool_groups": {"web": ["web_search"]},
            },
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="discord", sender_id="u11", chat_id="c11", content="/policy")
    )
    assert out is not None
    assert "当前策略状态" in out.content
    assert "sender_is_owner=False" in out.content
    assert "policy_pipeline=" in out.content
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_memory_show_command_reports_openviking_status(monkeypatch, tmp_path):
    ov_dir = tmp_path / "ov_data"
    ov_dir.mkdir(parents=True, exist_ok=True)
    (ov_dir / "memory_events.jsonl").write_text(
        '{"id":"1","content":"a"}\n{"id":"2","content":"b"}\n',
        encoding="utf-8",
    )
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            },
            "memory": {
                "context_backend": {
                    "enabled": True,
                    "mode": "openviking",
                    "data_dir": str(ov_dir),
                    "config_file": "",
                    "session_prefix": "gazer",
                    "default_user": "owner",
                    "commit_every_messages": 8,
                }
            },
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="discord", sender_id="u12", chat_id="c12", content="/memory")
    )
    assert out is not None
    assert "当前记忆后端状态" in out.content
    assert "backend.mode=openviking" in out.content
    assert "memory_events.rows=2" in out.content
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_reset_allowed_for_normal_user(monkeypatch, tmp_path):
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            }
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="discord", sender_id="u13", chat_id="c13", content="/reset")
    )
    assert out is not None
    assert "会话已重置" in out.content
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_reset_blocked_for_readonly_user(monkeypatch, tmp_path):
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            },
            "security": {
                "readonly_channel_ids": {
                    "discord": ["ro_1"],
                }
            },
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(channel="discord", sender_id="ro_1", chat_id="c14", content="/reset")
    )
    assert out is not None
    assert "当前角色=只读" in out.content
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_channel_router_strategy_blocked_for_readonly_user(monkeypatch, tmp_path):
    loop, provider, _cfg = _make_loop(
        monkeypatch,
        tmp_path,
        {
            "models": {
                "active_profile": {
                    "slow_brain": {"provider": "openai", "model": "gpt-4o"},
                    "fast_brain": {"provider": "openai", "model": "gpt-4o-mini"},
                },
                "router": {
                    "enabled": True,
                    "strategy": "priority",
                    "strategy_template": "custom",
                    "rollout": {"enabled": True, "owner_only": True, "channels": []},
                },
            },
            "security": {
                "readonly_channel_ids": {
                    "discord": ["ro_2"],
                }
            },
        },
        owner_sender_ids=set(),
    )
    out = await loop._process_message(
        InboundMessage(
            channel="discord",
            sender_id="ro_2",
            chat_id="c15",
            content="/router strategy latency",
        )
    )
    assert out is not None
    assert "当前角色=只读" in out.content
    assert provider.calls == 0
